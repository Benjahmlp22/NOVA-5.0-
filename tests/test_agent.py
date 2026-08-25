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

    def chat(self, messages, tools=None):  # noqa: ANN001
        self.llamadas.append({"messages": list(messages), "tools": tools})
        return self.guion.pop(0)


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
