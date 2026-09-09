"""El cliente de Ollama, en lo que no necesita a Ollama abierto.

Aquí sólo hay dos cosas, pero son las dos que deciden si NOVA va lenta
sin que nadie sepa por qué: saber cuánto del modelo está de verdad en la
GPU, y cambiarse al pequeño sin dejar los dos ocupando memoria.
"""

from __future__ import annotations

from nova.llm.ollama import OllamaClient


class _HttpFalso:
    """Responde lo que le digas y apunta lo que le piden."""

    def __init__(self, ps: dict | None = None) -> None:
        self._ps = ps or {"models": []}
        self.posts: list[tuple[str, dict]] = []

    def get(self, url, **kw):  # noqa: ANN001, ARG002
        class R:
            def __init__(self, datos):
                self._d = datos

            def raise_for_status(self):
                pass

            def json(self):
                return self._d

        return R(self._ps)

    def post(self, url, json=None, **kw):  # noqa: ANN001, A002, ARG002
        self.posts.append((url, json or {}))
        return None


def _cliente(ps=None) -> OllamaClient:
    c = OllamaClient("http://127.0.0.1:11434", "grande")
    c._http = _HttpFalso(ps)
    return c


# ── ¿Está el modelo dentro de la GPU? ────────────────────────────────

def test_residencia_completa_cuando_todo_esta_en_vram():
    c = _cliente({"models": [{"model": "grande", "size": 1000, "size_vram": 1000}]})
    assert c.residencia() == 1.0


def test_residencia_se_hunde_cuando_un_juego_expulsa_al_modelo():
    """El caso real medido: 330 MB de 3.2 GB con Star Citizen abierto."""
    c = _cliente({"models": [{"model": "grande", "size": 3200, "size_vram": 330}]})
    assert round(c.residencia(), 2) == 0.10


def test_residencia_desconocida_no_inventa_gpu():
    """Ante la duda, no alarmar: no vale cambiar de modelo por un fallo
    al leer /api/ps."""
    assert _cliente({"models": []}).residencia() is None
    assert _cliente({"models": [{"model": "grande", "size": 0}]}).residencia() is None


# ── Cambiar de modelo ────────────────────────────────────────────────

def test_al_cambiar_de_modelo_se_suelta_el_anterior():
    """Si no, los dos se quedan residentes — y el motivo de cambiar era
    justo que no cabía uno."""
    c = _cliente()
    c.usar_modelo("pequeño")
    assert c.model == "pequeño"
    url, cuerpo = c._http.posts[-1]
    assert url.endswith("/api/generate")
    assert cuerpo["model"] == "grande"
    assert cuerpo["keep_alive"] == 0


def test_cambiar_al_mismo_modelo_no_hace_nada():
    c = _cliente()
    c.usar_modelo("grande")
    assert c._http.posts == []


def test_medicion_stream_separa_carga_de_tiempo_al_primer_fragmento():
    import httpx

    cliente = OllamaClient("http://ollama.invalid", "pequeno")
    cliente._http.close()
    cliente._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(
        200, text='{"message":{"content":"Hola"}}\n'
        '{"done":true,"load_duration":2500000000,"eval_count":1}\n')))
    partes = []
    try:
        respuesta = cliente.chat([], on_trozo=partes.append)
        assert partes == ["Hola"] and respuesta.carga_s == 2.5
        assert respuesta.primer_fragmento_s is not None
        assert respuesta.primer_fragmento_s >= 0
    finally:
        cliente.close()
