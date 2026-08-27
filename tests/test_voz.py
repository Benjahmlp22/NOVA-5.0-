"""Elegir voz hablando: "ponte voz de hombre", "acento mexicano".

Sin abrir el sintetizador de verdad: lo que puede fallar aquí es la
traducción de lo que dice el usuario a una voz concreta, no PowerShell.
"""

from __future__ import annotations

import pytest

from nova.tools import voz as herramienta
from nova.voice.onecore import Voz
from nova.voice.speaker import elegir_por_defecto

PABLO = Voz("Microsoft Pablo", "es-ES", "Male")
LAURA = Voz("Microsoft Laura", "es-ES", "Female")
HELENA = Voz("Microsoft Helena", "es-ES", "Female")
SABINA = Voz("Microsoft Sabina", "es-MX", "Female")
RAUL = Voz("Microsoft Raul", "es-MX", "Male")
TODAS = [PABLO, LAURA, HELENA, SABINA, RAUL]


class AltavozFalso:
    def __init__(self, actual: str = "Microsoft Helena") -> None:
        self.voz_actual = actual
        self._velocidad = 1.0
        self.cambios: list[str] = []

    def voces(self):
        return TODAS

    def usar_voz(self, nombre: str) -> bool:
        self.cambios.append(nombre)
        self.voz_actual = nombre
        return True

    def set_velocidad(self, factor: float) -> bool:
        self._velocidad = factor
        return True


@pytest.fixture
def altavoz(tmp_path, monkeypatch):
    sp = AltavozFalso()
    herramienta.conectar(sp)
    # Que guardar la elección no toque el data/ real del usuario.
    monkeypatch.setattr(herramienta, "_archivo", lambda: tmp_path / "voz.json")
    yield sp
    herramienta.conectar(None)


# ── Entender cómo la quiere ──────────────────────────────────────────

@pytest.mark.parametrize("pide,esperada", [
    ("ponte voz de hombre", "Microsoft Pablo"),
    ("quiero una voz masculina", "Microsoft Pablo"),
    ("con acento mexicano", "Microsoft Sabina"),
    ("una voz de hombre mexicano", "Microsoft Raul"),
    ("habla como Raul", "Microsoft Raul"),
    ("ponte la de Laura", "Microsoft Laura"),
])
def test_elige_la_voz_que_le_piden(altavoz, pide, esperada):
    r = herramienta.cambiar(pide)
    assert r.ok, r.message
    assert altavoz.voz_actual == esperada


def test_el_nombre_propio_manda_sobre_lo_demas(altavoz):
    """Si dice «Pablo», quiere Pablo, aunque diga otra cosa alrededor."""
    herramienta.cambiar("ponte la voz mexicana de Pablo")
    assert altavoz.voz_actual == "Microsoft Pablo"


def test_si_ya_habla_asi_no_se_cambia_por_cambiar(altavoz):
    """Pedir «voz de mujer» teniendo voz de mujer y que se cambie a OTRA
    mujer es responder a algo que no se ha preguntado."""
    r = herramienta.cambiar("ponte voz de mujer")
    assert r.ok
    assert altavoz.cambios == []
    assert "ya te hablo" in r.message.lower()


def test_lo_que_no_tiene_lo_dice_en_vez_de_inventar(altavoz):
    r = herramienta.cambiar("ponte voz de robot alienígena")
    assert not r.ok
    assert altavoz.cambios == []
    # Y aprovecha para decir qué SÍ puede hacer.
    assert "Pablo" in r.message


def test_sin_decir_como_la_quiere_pregunta(altavoz):
    r = herramienta.cambiar("")
    assert not r.ok
    assert "?" in r.message


def test_sin_altavoz_no_finge_que_ha_cambiado(monkeypatch):
    herramienta.conectar(None)
    assert not herramienta.cambiar("de hombre").ok


# ── Velocidad ────────────────────────────────────────────────────────

def test_mas_despacio_baja_y_mas_rapido_sube(altavoz):
    herramienta.velocidad("habla más despacio")
    assert altavoz._velocidad < 1.0
    herramienta.velocidad("ahora más rápido")
    assert altavoz._velocidad == pytest.approx(1.0)


def test_la_velocidad_tiene_topes(altavoz):
    for _ in range(20):
        herramienta.velocidad("más rápido")
    assert altavoz._velocidad <= 1.6
    r = herramienta.velocidad("más rápido")
    assert "todo lo rápido" in r.message


def test_ni_rapido_ni_lento_pregunta(altavoz):
    assert not herramienta.velocidad("ponte guapa").ok


# ── Guardar la elección ──────────────────────────────────────────────

def test_la_eleccion_sobrevive_al_reinicio(altavoz):
    herramienta.cambiar("ponte voz de hombre")
    herramienta.velocidad("más despacio")

    otro = AltavozFalso()
    herramienta.aplicar_guardado(otro)
    assert otro.voz_actual == "Microsoft Pablo"
    assert otro._velocidad < 1.0


# ── Con cuál empieza ─────────────────────────────────────────────────

def test_por_defecto_sigue_siendo_helena():
    """Estrenar sintetizador no es motivo para cambiarle el sexo a la voz
    de NOVA: Windows lista a Pablo primero, y eso no es una decisión."""
    assert elegir_por_defecto(TODAS) is HELENA


def test_sin_voces_no_revienta():
    assert elegir_por_defecto([]) is None
