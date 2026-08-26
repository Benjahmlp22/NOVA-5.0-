"""La máquina de estados de la escucha, sin micrófono ni modelos.

Se prueba `_entender`, que es donde se decide todo lo importante: si la
llamada era de verdad, si es un comando o una despedida, y qué texto se
manda al cerebro. La captura de audio y los modelos se inyectan.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nova.voice.listener import VoiceListener


class TranscriptorFalso:
    """Devuelve el guion que se le dé, en orden."""

    def __init__(self, *textos: str) -> None:
        self.textos = list(textos)
        self.recibido: list[int] = []

    def transcribir(self, senal) -> str:  # noqa: ANN001
        self.recibido.append(len(senal))
        return self.textos.pop(0) if self.textos else ""

    def cargar(self) -> bool:
        return True

    def precalentar(self) -> None:
        pass


def _oyente(*textos: str, **kw):
    eventos: list[tuple[str, object]] = []
    oyente = VoiceListener(
        Path("/no/existe"),
        "nova",
        transcriptor=TranscriptorFalso(*textos),
        on_wake=lambda con_comando: eventos.append(("wake", con_comando)),
        on_command=lambda t: eventos.append(("comando", t)),
        on_sleep=lambda m: eventos.append(("dormir", m)),
        **kw,
    )
    return oyente, eventos


def _audio(segundos: float = 1.0) -> np.ndarray:
    """Algo que pase el filtro de "hay señal" y no sean ceros."""
    n = int(16000 * segundos)
    return (0.05 * np.sin(np.linspace(0, 400, n))).astype(np.float32)


# ── La etapa 2 confirma la llamada ───────────────────────────────────

def test_el_nombre_solo_despierta_sin_comando():
    oyente, eventos = _oyente("NOVA.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == [("wake", False)]
    assert oyente.awake


def test_nombre_y_orden_del_tiron_llegan_juntos():
    """El pre-roll hace que la orden entera esté en el audio, no sólo el nombre."""
    oyente, eventos = _oyente("NOVA, abre Discord.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == [("wake", True), ("comando", "abre discord")]


def test_el_aviso_de_wake_dice_si_viene_orden_pegada():
    """Sin ese dato, NOVA saluda y contesta a la vez, hablándose encima."""
    oyente, eventos = _oyente("NOVA, qué hora es.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos[0] == ("wake", True)


def test_falsa_alarma_de_la_etapa_1_se_descarta_en_silencio():
    """«no va a funcionar el mando» suena igual que el nombre.

    La etapa 1 abre la puerta de más a propósito; si la etapa 2 no ve el
    nombre, NOVA vuelve a dormir sin haber hecho ruido — el chime no ha
    sonado todavía.
    """
    oyente, eventos = _oyente("No va a funcionar el mando.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == []
    assert not oyente.awake


def test_la_novia_no_es_el_nombre():
    """Contener las letras no basta: tiene que ser palabra suelta."""
    oyente, eventos = _oyente("La novia de mi hermano viene mañana.")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == []


def test_transcripcion_vacia_no_despierta():
    oyente, eventos = _oyente("")
    oyente._entender(_audio(), exige_nombre=True)
    assert eventos == []


# ── Conversación continua ────────────────────────────────────────────

def test_despierta_no_hace_falta_repetir_el_nombre():
    oyente, eventos = _oyente("cierra chrome")
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == [("comando", "cierra chrome")]


def test_repetir_el_nombre_estando_despierta_no_estorba():
    """«NOVA, cierra chrome» en el segundo turno: el nombre se quita."""
    oyente, eventos = _oyente("NOVA, cierra Chrome.")
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == [("comando", "cierra chrome")]


@pytest.mark.parametrize("despedida", [
    "hasta luego", "adiós", "eso es todo", "nos vemos", "gracias NOVA",
])
def test_las_despedidas_cierran_el_turno_sin_mandar_comando(despedida):
    oyente, eventos = _oyente(despedida)
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos == [("dormir", "despedida")]
    assert not oyente.awake


def test_un_comando_normal_no_se_confunde_con_despedida():
    oyente, eventos = _oyente("dime cuanto queda de bateria")
    oyente._awake = True
    oyente._entender(_audio(), exige_nombre=False)
    assert eventos[0][0] == "comando"


# ── Pre-roll ─────────────────────────────────────────────────────────

def test_el_preroll_guarda_el_ultimo_segundo():
    oyente, _ = _oyente(preroll_s=1.0)
    # 50 ms por bloque → 20 bloques en un segundo.
    assert oyente._preroll.maxlen == 20


def test_el_preroll_nunca_es_cero():
    """Con 0 se perdería el principio de cada orden, que es el bug de NOVA4."""
    oyente, _ = _oyente(preroll_s=0.0)
    assert oyente._preroll.maxlen >= 1


def test_mute_no_para_el_preroll():
    """Mientras NOVA habla se deja de PROCESAR, pero se sigue guardando.

    En NOVA4 el audio se tiraba entero y se comía el principio de lo que
    dijeras justo después de que ella terminara.
    """
    oyente, _ = _oyente()
    oyente.mute()
    assert oyente._muted.is_set()
    # El búfer es independiente del mute: sigue aceptando bloques.
    oyente._preroll.append(_audio(0.05))
    assert len(oyente._preroll) == 1


# ── Quitar el nombre ─────────────────────────────────────────────────

@pytest.mark.parametrize("texto,esperado", [
    ("nova", ""),
    ("nova abre discord", "abre discord"),
    ("oye nova que hora es", "que hora es"),
    ("nova, cierra chrome.", "cierra chrome"),
])
def test_quitar_nombre(texto, esperado):
    oyente, _ = _oyente()
    assert oyente._quitar_nombre(texto) == esperado


@pytest.mark.parametrize("texto", [
    "no va a funcionar el mando",
    "la novia de mi hermano",
    "esto no va a salir bien",
    "abre discord",
])
def test_quitar_nombre_devuelve_none_si_no_esta(texto):
    oyente, _ = _oyente()
    assert oyente._quitar_nombre(texto) is None


# ── Dormirse ─────────────────────────────────────────────────────────

def test_sleep_now_solo_avisa_si_estaba_despierta():
    oyente, eventos = _oyente()
    oyente.sleep_now("silencio")
    assert eventos == []

    oyente._awake = True
    oyente.sleep_now("silencio")
    assert eventos == [("dormir", "silencio")]
