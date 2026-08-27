"""El puente con PowerShell, y sobre todo lo que le pasó de verdad.

Un solo agotamiento de plazo desincronizaba la cola PARA SIEMPRE: la
respuesta atrasada la recogía la petición siguiente y a partir de ahí
todo iba corrido un puesto. En las voces eso se oía como NOVA diciendo
"S", "EST" y trozos sueltos, porque daba por escrita una frase que aún
no lo estaba y leía el WAV anterior a medio escribir.

Aquí no se abre PowerShell: se falsea el proceso, que es la única forma
de provocar un retraso a voluntad.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nova.winrt import PuenteWinRT


class _ProcesoFalso:
    """Un PowerShell de mentira que responde cuando se le dice."""

    def __init__(self) -> None:
        self.escrito: list[str] = []
        self.stdin = self
        self.stdout = None

    # Hace de stdin.
    def write(self, texto: str) -> None:
        self.escrito.append(texto.strip())

    def flush(self) -> None:
        pass

    def poll(self):  # noqa: ANN201
        return None


@pytest.fixture
def puente():
    p = PuenteWinRT(Path("no/existe.ps1"), "prueba")
    p._proc = _ProcesoFalso()
    return p


def _responder(puente, texto: str) -> None:
    """Como si PowerShell hubiera escrito esa línea."""
    puente._respuestas.put(texto)


def _quemar_una(puente) -> None:
    """Gasta el número 1, para que lo de después no sea la primera.

    Hace falta porque el contador arranca en 1: una línea "1 OK" metida
    a mano como si fuera vieja sería, en realidad, la respuesta legítima
    de la primera petición.
    """
    _responder(puente, "1 OK")
    puente.mandar("PRIMERA")


# ── Lo normal ────────────────────────────────────────────────────────

def test_cada_peticion_va_numerada(puente):
    _responder(puente, "1 OK")
    puente.mandar("HAZ ALGO")
    assert puente._proc.escrito == ["1 HAZ ALGO"]


def test_los_numeros_no_se_repiten(puente):
    for i in (1, 2, 3):
        _responder(puente, f"{i} OK")
        puente.mandar("ALGO")
    assert [e.split(" ")[0] for e in puente._proc.escrito] == ["1", "2", "3"]


def test_una_respuesta_normal_llega(puente):
    _responder(puente, "1 OK")
    assert puente.mandar("ALGO") == "OK"


def test_una_orden_de_varias_lineas_las_junta(puente):
    for linea in ("1 VOZ\tPablo", "1 VOZ\tLaura", "1 OK"):
        _responder(puente, linea)
    assert puente.mandar_varias("VOCES") == ["VOZ\tPablo", "VOZ\tLaura"]


def test_un_error_no_se_confunde_con_una_respuesta(puente):
    _responder(puente, "1 ERROR no existe eso")
    assert puente.mandar("ALGO") == ""


# ── El fallo que sonaba como "S" y "EST" ─────────────────────────────

def test_una_respuesta_atrasada_no_la_cobra_la_siguiente(puente):
    """EL bug. Sin números, la segunda petición daba por buena la
    respuesta de la primera y contestaba antes de que pasara nada."""
    # La primera se pierde: nadie responde a tiempo.
    assert puente.mandar("PRIMERA", espera=0.01) == ""

    # Ahora llega tarde, cuando ya nadie la espera.
    _responder(puente, "1 OK")

    # Y la segunda NO debe cobrarse esa: sólo la suya.
    _responder(puente, "2 OK")
    assert puente.mandar("SEGUNDA") == "OK"
    # Se mandó de verdad, no se resolvió con basura de la cola.
    assert puente._proc.escrito[-1] == "2 SEGUNDA"


def test_sin_su_respuesta_no_dice_que_salio_bien(puente):
    """Decir que sí sin haberlo hecho es lo que rompía el audio: NOVA
    leía un WAV que aún no estaba escrito."""
    _quemar_una(puente)
    _responder(puente, "1 OK")          # atrasada, de la que ya pasó
    assert puente.mandar("NUEVA", espera=0.05) == ""


def test_varias_atrasadas_seguidas_se_descartan_todas(puente):
    for _ in range(3):
        assert puente.mandar("SE PIERDE", espera=0.01) == ""
    for n in (1, 2, 3):
        _responder(puente, f"{n} OK")    # llegan las tres, tarde
    _responder(puente, "4 OK")
    assert puente.mandar("LA BUENA") == "OK"


def test_la_de_varias_lineas_tambien_se_protege(puente):
    _quemar_una(puente)
    _responder(puente, "1 IDIOMA\tviejo")   # atrasadas de la que ya pasó
    _responder(puente, "1 OK")
    _responder(puente, "2 IDIOMA\tes-ES")
    _responder(puente, "2 OK")
    assert puente.mandar_varias("IDIOMAS") == ["IDIOMA\tes-ES"]


# ── Cuando el proceso no está ────────────────────────────────────────

def test_sin_proceso_no_finge(puente):
    puente._proc = None
    assert puente.mandar("ALGO") == ""
    assert puente.mandar_varias("ALGO") == []


def test_arrancar_sin_guion_no_revienta():
    p = PuenteWinRT(Path("no/existe.ps1"), "prueba")
    assert p.start() is False
    assert "falta" in p.error


# ── La red de seguridad del altavoz ──────────────────────────────────

def test_no_da_por_buena_una_frase_que_no_se_escribio(tmp_path):
    """Aunque el puente diga OK. Si el WAV no está, hay que caer a SAPI
    en vez de reproducir lo que hubiera antes en esa ruta."""
    from nova.voice.onecore import SintetizadorOneCore

    s = SintetizadorOneCore()
    s._puente = _PuenteQueSiempreDiceQueSi()
    assert s.sintetizar("hola", tmp_path / "no_se_escribe.wav") is False


def test_borra_el_anterior_antes_de_pedir_el_nuevo(tmp_path):
    """Si no, un fallo silencioso reproduce la frase de antes: es
    exactamente lo que se oía como trozos sueltos."""
    from nova.voice.onecore import SintetizadorOneCore

    destino = tmp_path / "voz.wav"
    destino.write_bytes(b"RIFF" + b"\x00" * 200)      # la frase anterior
    s = SintetizadorOneCore()
    s._puente = _PuenteQueSiempreDiceQueSi()
    assert s.sintetizar("hola", destino) is False
    assert not destino.exists()


class _PuenteQueSiempreDiceQueSi:
    disponible = True
    error = ""

    def mandar(self, orden, espera=3.0):  # noqa: ANN001, ARG002
        return "OK"
