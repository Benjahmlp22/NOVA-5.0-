"""El bucle LLM ↔ herramientas: lo que más caro salió aprender."""

from __future__ import annotations

from nova.core.agent import Agent
from nova.llm.ollama import LLMResponse, ToolCall
from nova.tools.registry import Risk, Tool, ToolRegistry, ToolResult


class FakeLLM:
    """Responde un guion fijo y guarda lo que le mandaron."""

    def __init__(self, guion: list[LLMResponse]) -> None:
        self.guion = list(guion)
        self.llamadas: list[dict] = []

    def chat(self, messages, tools=None, on_trozo=None):  # noqa: ANN001
        self.llamadas.append({"messages": list(messages), "tools": tools})
        respuesta = self.guion.pop(0)
        if on_trozo is not None and respuesta.text:
            # Imita el streaming de Ollama: el texto llega a cachos, y a
            # cachos que no respetan los límites de frase. Mandarlo de
            # una pieza no probaría nada de lo que puede salir mal.
            for i in range(0, len(respuesta.text), 7):
                on_trozo(respuesta.text[i:i + 7])
        return respuesta


def _registro(policy: str = "solo_peligroso") -> ToolRegistry:
    reg = ToolRegistry(policy)
    reg.register(Tool(
        name="test.eco",
        description="Repite un texto",
        handler=lambda text="": ToolResult(ok=True, message=f"Eco: {text}"),
        schema={"type": "object", "properties": {"text": {"type": "string"}}},
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="test.borrar",
        description="Borra algo importante",
        handler=lambda: ToolResult(ok=True, message="Borrado."),
        risk=Risk.DANGEROUS,
    ))
    reg.register(Tool(
        name="test.abrir",
        description="Abre una app",
        handler=lambda: ToolResult(ok=True, message="Abierta."),
        risk=Risk.MEDIUM,
    ))
    return reg


def test_respuesta_directa_sin_herramientas():
    llm = FakeLLM([LLMResponse(text="Hola.")])
    reply = Agent(llm, _registro()).run("sys", [], "hola")
    assert reply.text == "Hola."
    assert reply.tools_used == []
    # El catálogo se le ofrece igualmente, por si lo necesita.
    assert llm.llamadas[0]["tools"]


def test_ejecuta_herramienta_y_responde():
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="test_eco", args={"text": "hola"})]),
        LLMResponse(text="Dice hola."),
    ])
    reply = Agent(llm, _registro()).run("sys", [], "haz eco")
    assert reply.text == "Dice hola."
    assert reply.tools_used == ["test.eco"]


def test_resultado_llega_como_texto_plano_no_json():
    """El fix del bucle: con JSON, el modelo repetía la llamada."""
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="test_eco", args={"text": "ping"})]),
        LLMResponse(text="Listo."),
    ])
    Agent(llm, _registro()).run("sys", [], "eco")

    mensajes_tool = [m for m in llm.llamadas[1]["messages"] if m.get("role") == "tool"]
    assert len(mensajes_tool) == 1
    contenido = mensajes_tool[0]["content"]
    assert contenido == "Eco: ping"
    assert not contenido.strip().startswith("{")


def test_peligrosa_se_para_y_pide_confirmacion():
    llm = FakeLLM([LLMResponse(tool_calls=[ToolCall(id="1", name="test_borrar")])])
    reply = Agent(llm, _registro()).run("sys", [], "borra")
    assert reply.pending is not None
    assert reply.pending.tool == "test.borrar"
    assert reply.tools_used == []  # NO se ejecutó


def test_media_se_ejecuta_sola_con_politica_normal():
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="test_abrir")]),
        LLMResponse(text="Ya está."),
    ])
    reply = Agent(llm, _registro("solo_peligroso")).run("sys", [], "abre")
    assert reply.tools_used == ["test.abrir"]
    assert reply.pending is None


def test_media_pregunta_en_modo_estricto():
    llm = FakeLLM([LLMResponse(tool_calls=[ToolCall(id="1", name="test_abrir")])])
    reply = Agent(llm, _registro("estricto")).run("sys", [], "abre")
    assert reply.pending is not None


def test_peligrosa_pregunta_incluso_en_modo_relajado():
    """La política nunca puede saltarse lo DANGEROUS."""
    llm = FakeLLM([LLMResponse(tool_calls=[ToolCall(id="1", name="test_borrar")])])
    reply = Agent(llm, _registro("solo_peligroso")).run("sys", [], "borra")
    assert reply.pending is not None


def test_confirmar_ejecuta_lo_pendiente():
    llm = FakeLLM([LLMResponse(tool_calls=[ToolCall(id="1", name="test_borrar")])])
    agente = Agent(llm, _registro())
    reply = agente.run("sys", [], "borra")
    resultado = agente.confirm(reply.pending)
    assert resultado.ok
    assert resultado.message == "Borrado."


def test_herramienta_inexistente_no_rompe():
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="fantasma")]),
        LLMResponse(text="Eso no existe."),
    ])
    reply = Agent(llm, _registro()).run("sys", [], "x")
    assert reply.text == "Eso no existe."


def test_tope_de_rondas_fuerza_cierre():
    bucle = LLMResponse(tool_calls=[ToolCall(id="1", name="test_eco", args={"text": "x"})])
    llm = FakeLLM([bucle, bucle, LLMResponse(text="Resumen.")])
    reply = Agent(llm, _registro(), max_rounds=2).run("sys", [], "x")
    assert reply.text == "Resumen."
    assert reply.rounds == 2


def test_argumentos_invalidos_se_reportan_sin_crashear():
    llm = FakeLLM([
        LLMResponse(tool_calls=[ToolCall(id="1", name="test_eco", args={"inventado": 1})]),
        LLMResponse(text="Me equivoqué."),
    ])
    reply = Agent(llm, _registro()).run("sys", [], "x")
    assert reply.text == "Me equivoqué."
    assert reply.tools_used == []


# ── Hablar mientras el modelo aún escribe ────────────────────────────
#
# Empezar a hablar con la primera frase quita hasta un segundo largo de
# silencio (medido: 1.81 s → 0.50 s en "qué hora es"). Pero abre dos
# formas de quedar fatal, y las dos están cerradas aquí.

def _agente_con_frases(guion):
    dichas: list[str] = []
    agente = Agent(FakeLLM(guion), _registro(), on_frase=dichas.append)
    return agente, dichas


def test_las_frases_se_van_diciendo_segun_se_generan():
    agente, dichas = _agente_con_frases(
        [LLMResponse(text="Listo, he abierto Chrome. Ya lo tienes delante.")]
    )
    agente.run("sys", [], "abre chrome")
    assert dichas == ["Listo, he abierto Chrome.", "Ya lo tienes delante."]


def test_no_se_dice_una_frase_a_medias():
    """El TTS entona un trozo cortado como si fuera el final."""
    agente, dichas = _agente_con_frases([LLMResponse(text="Esto no tiene punto todavía")])
    agente.run("sys", [], "hola")
    # Sin puntuación no hay frase cerrada hasta el cierre del turno.
    assert dichas == ["Esto no tiene punto todavía"]


def test_una_coletilla_no_se_cuela_por_ir_deprisa():
    """El filtro mira el FINAL del texto, y en streaming aún no se sabe.

    Sin comprobar frase a frase, NOVA soltaría en alto un "¿en qué puedo
    ayudarte?" que el filtro habría quitado un segundo después.
    """
    agente, dichas = _agente_con_frases(
        [LLMResponse(text="Son las diez y media. ¿En qué puedo ayudarte hoy?")]
    )
    respuesta = agente.run("sys", [], "que hora es")
    assert dichas == ["Son las diez y media."]
    assert "ayudarte" not in respuesta.text


def test_lo_ya_dicho_no_se_repite_al_terminar():
    """Si se dijo mientras se generaba, la app NO debe decirlo otra vez."""
    agente, dichas = _agente_con_frases([LLMResponse(text="Hecho. Todo listo.")])
    respuesta = agente.run("sys", [], "haz algo")
    assert respuesta.ya_dicho
    assert " ".join(dichas) == respuesta.text


def test_sin_streaming_nada_cambia():
    """El camino de siempre sigue existiendo: la app decide cuál usa."""
    agente = Agent(FakeLLM([LLMResponse(text="Hecho.")]), _registro())
    respuesta = agente.run("sys", [], "haz algo")
    assert respuesta.text == "Hecho."
    assert not respuesta.ya_dicho


def test_una_ronda_de_herramientas_no_habla():
    """Mientras ejecuta no hay nada que decir: eso llega después."""
    agente, dichas = _agente_con_frases([
        LLMResponse(tool_calls=[ToolCall(id="1", name="test_eco", args={"text": "hola"})]),
        LLMResponse(text="Listo."),
    ])
    agente.run("sys", [], "haz eco")
    assert dichas == ["Listo."]
