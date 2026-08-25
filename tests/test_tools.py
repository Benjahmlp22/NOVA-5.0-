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
    mem.remember("me llamo Benja")
    segunda = mem.remember("me llamo Benja")
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
