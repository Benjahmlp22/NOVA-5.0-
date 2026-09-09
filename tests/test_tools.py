"""Herramientas: sandbox, permisos y memoria."""

from __future__ import annotations

import pytest

from nova.tools import build_registry
from nova.tools.files import SandboxError, _resolve
from nova.tools.registry import PendingConfirmation, Risk, Tool, ToolRegistry, ToolResult

# ── Sandbox de archivos ──────────────────────────────────────────────

@pytest.mark.parametrize("ruta", [
    "../fuera.txt",
    "../../Windows/System32/algo.dll",
    "C:\\Windows\\system32\\config",
    "/etc/passwd",
    "subdir/../../escape.txt",
])
def test_sandbox_rechaza_rutas_que_escapan(ruta):
    with pytest.raises(SandboxError):
        _resolve(ruta)


@pytest.mark.parametrize("ruta", ["nota.txt", "proyecto/notas/idea.md", "a/b/c/d.txt"])
def test_sandbox_acepta_rutas_relativas(ruta):
    assert _resolve(ruta)


def test_sandbox_rechaza_ruta_vacia():
    with pytest.raises(SandboxError):
        _resolve("")


# ── Permisos ─────────────────────────────────────────────────────────

def _reg(policy):
    reg = ToolRegistry(policy)
    for nombre, riesgo in [("s", Risk.SAFE), ("m", Risk.MEDIUM), ("d", Risk.DANGEROUS)]:
        reg.register(Tool(
            name=nombre,
            description=f"prueba {nombre}",
            handler=lambda: ToolResult(ok=True, message="hecho"),
            risk=riesgo,
        ))
    return reg


def test_politica_solo_peligroso():
    reg = _reg("solo_peligroso")
    assert isinstance(reg.execute("s", {}), ToolResult)
    assert isinstance(reg.execute("m", {}), ToolResult)          # va directa
    assert isinstance(reg.execute("d", {}), PendingConfirmation)  # pregunta


def test_politica_estricta():
    reg = _reg("estricto")
    assert isinstance(reg.execute("s", {}), ToolResult)
    assert isinstance(reg.execute("m", {}), PendingConfirmation)
    assert isinstance(reg.execute("d", {}), PendingConfirmation)


def test_confirmado_ejecuta():
    reg = _reg("solo_peligroso")
    resultado = reg.execute("d", {}, confirmed=True)
    assert isinstance(resultado, ToolResult)
    assert resultado.ok


# ── Catálogo para el LLM ─────────────────────────────────────────────

def test_nombres_sin_puntos_para_el_modelo():
    reg = build_registry()
    nombres = [e["function"]["name"] for e in reg.llm_schemas()]
    assert nombres
    assert all("." not in n for n in nombres)


def test_resolver_acepta_ambas_formas():
    reg = build_registry()
    assert reg.resolve("app_open") is reg.resolve("app.open")
    assert reg.resolve("no_existe") is None


def test_exclusiones_no_llegan_al_modelo():
    reg = build_registry()
    schemas = reg.llm_schemas(exclude={"app.open"})
    assert "app_open" not in [e["function"]["name"] for e in schemas]


def test_registro_completo_tiene_lo_esencial():
    reg = build_registry()
    for esperada in ("app.open", "pc.status", "pc.active_window",
                     "screen.capture", "memory.remember", "file.create"):
        assert reg.get(esperada) is not None, esperada


# ── Memoria ──────────────────────────────────────────────────────────

@pytest.fixture
def memoria_temporal(tmp_path, monkeypatch):
    """Apunta la memoria a un archivo temporal, no al del usuario."""
    from nova.tools import memory as mem

    monkeypatch.setattr(mem, "_path", lambda: tmp_path / "memoria.json")
    return mem


def test_memoria_guarda_y_recuerda(memoria_temporal):
    mem = memoria_temporal
    assert mem.remember("mi proyecto vive en la unidad D").ok
    resultado = mem.recall("proyecto")
    assert "unidad D" in resultado.message


def test_memoria_no_duplica(memoria_temporal):
    mem = memoria_temporal
    mem.remember("me llamo Ana")
    segunda = mem.remember("me llamo Ana")
    assert "ya" in segunda.message.lower()


def test_memoria_vacia_responde_con_sentido(memoria_temporal):
    resultado = memoria_temporal.recall("lo que sea")
    assert resultado.ok
    assert "no tengo" in resultado.message.lower()


def test_memoria_olvida(memoria_temporal):
    mem = memoria_temporal
    mem.remember("mi color favorito es el azul")
    assert mem.forget("color").ok
    assert "no tengo" in mem.recall("color").message.lower()


def test_lo_que_se_oculta_al_modelo_existe_de_verdad():
    """Una lista de exclusión que nombra herramientas fantasma no protege nada.

    NOVA4 ocultaba {"logs.read", "command.run", "process.kill"} y ninguna
    existía: registraba 19 herramientas y ofrecía las 19. Parecía haber
    un control que no había.
    """
    from nova.core.agent import LLM_HIDDEN

    reg = build_registry()
    fantasmas = {n for n in LLM_HIDDEN if reg.get(n) is None}
    assert not fantasmas, f"se ocultan herramientas que no existen: {fantasmas}"


def test_el_catalogo_del_modelo_sale_de_expose_to_llm():
    """La decisión de ocultar vive junto a la herramienta, no en una lista."""
    from nova.core.agent import LLM_HIDDEN

    reg = build_registry()
    esperadas = {
        reg.get(n).llm_name
        for n in reg.names()
        if reg.get(n).expose_to_llm and n not in LLM_HIDDEN
    }
    ofrecidas = {e["function"]["name"] for e in reg.llm_schemas(exclude=set(LLM_HIDDEN))}
    assert ofrecidas == esperadas


# ── La memoria llega al prompt (en NOVA4 no llegaba) ─────────────────

def test_memoria_para_prompt_lista_los_hechos(memoria_temporal):
    from nova.tools import memory

    memory.remember("me llamo Ana")
    memory.remember("juego a Stormworks")
    bloque = memory.para_prompt()
    assert "- me llamo Ana" in bloque
    assert "- juego a Stormworks" in bloque


def test_memoria_para_prompt_vacia_no_ensucia_el_prompt(memoria_temporal):
    """Sin recuerdos no se añade una sección vacía al system prompt."""
    from nova.tools import memory

    assert memory.para_prompt() == ""


def test_memoria_para_prompt_tiene_techo(memoria_temporal):
    """El prefill se paga en CADA mensaje: la lista no puede crecer sin fin."""
    from nova.tools import memory

    for i in range(20):
        memory.remember(f"dato numero {i}")
    lineas = memory.para_prompt().splitlines()
    assert len(lineas) == memory.MAX_HECHOS_EN_PROMPT
    # Se quedan los más recientes, no los primeros.
    assert "dato numero 19" in lineas[-1]


def test_el_prompt_incluye_los_recuerdos():
    from nova.core.conversation import build_system_prompt

    prompt = build_system_prompt("## Contexto actual\n- Ahora: lunes", "- odio el cilantro")
    assert "odio el cilantro" in prompt


def test_un_parametro_nulo_es_un_parametro_que_no_se_dio():
    """Los modelos grandes rellenan todos los huecos con null.

    Medido el 01/09 con gpt-oss-120b: `codigo.proyectos` llegaba como
    {"nombre": null}. Con nombre=None, cualquier handler que haga
    `nombre.strip()` revienta — y hay varios.
    """
    from nova.tools.registry import Risk, Tool, ToolRegistry, ToolResult

    def handler(nombre: str = "por defecto") -> ToolResult:
        return ToolResult(ok=True, message=nombre.strip())

    reg = ToolRegistry()
    reg.register(Tool(name="x.y", description="d", handler=handler, risk=Risk.SAFE))

    assert reg.execute("x.y", {"nombre": None}).message == "por defecto"
    assert reg.execute("x.y", {"nombre": "  dado  "}).message == "dado"
