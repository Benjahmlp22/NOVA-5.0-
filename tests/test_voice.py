"""La voz: hablar, callarse, y la etapa 1 del oído.

La máquina de estados de la escucha vive en `test_listener.py`.

Los tests de la escucha de NOVA4 se cayeron con el rediseño, y no por
descuido: probaban que "no va" contara como el nombre. Ese era justamente
el parche que causaba 4 falsas alarmas de cada 6 en conversación normal
("no va a funcionar el mando"), y en NOVA5 lo sustituye la confirmación
de la etapa 2. Un test que fija un bug no se conserva por respeto.
"""

from __future__ import annotations

import pytest

from nova.voice.speaker import limpiar_para_voz
from nova.voice.wake import normalizar_texto

# ── Texto que se manda al TTS ────────────────────────────────────────

def test_limpiar_para_voz_quita_lo_que_se_lee_mal():
    sucio = "**Listo**: mira `C:\\Users\\Benja\\cosas` o https://ejemplo.com/x"
    limpio = limpiar_para_voz(sucio)
    assert "**" not in limpio
    assert "C:\\" not in limpio
    assert "https" not in limpio
    assert "Listo" in limpio


def test_limpiar_para_voz_deja_el_texto_normal_en_paz():
    assert limpiar_para_voz("Listo, he abierto Chrome.") == "Listo, he abierto Chrome."


# ── Normalización de lo que se oye ───────────────────────────────────

@pytest.mark.parametrize("crudo,esperado", [
    ("NOVA, abre Discord.", "nova abre discord"),
    ("¿Qué temperatura hace?", "que temperatura hace"),
    ("  Hasta   luego,  NOVA.  ", "hasta luego nova"),
    ("", ""),
])
def test_normalizar_texto_quita_puntuacion_de_los_bordes(crudo, esperado):
    """Whisper puntúa; Vosk no. Comparar palabras exige igualarlos.

    Sin esto, "NOVA," no casaba con "nova" y NOVA no despertaba nunca con
    la salida de Whisper.
    """
    assert normalizar_texto(crudo) == esperado


def test_normalizar_texto_no_rompe_por_dentro():
    """"notas.txt" es contenido de la orden, no puntuación."""
    assert normalizar_texto("Crea Notas.txt") == "crea notas.txt"


# ── Etapa 1: el detector ─────────────────────────────────────────────

def test_el_detector_sin_cargar_no_dispara():
    """Antes de cargar el modelo no puede haber falsos positivos."""
    from pathlib import Path

    from nova.voice.wake import DetectorWake

    d = DetectorWake(Path("/no/existe"), "nova")
    assert d.escucha(b"\x00" * 100) is False


def test_el_detector_avisa_si_el_modelo_no_esta():
    from pathlib import Path

    from nova.voice.wake import DetectorWake

    d = DetectorWake(Path("/no/existe"), "nova")
    assert d.cargar() is False
    assert d.error


def test_el_patron_de_reserva_ya_no_acepta_no_va():
    """La variante "no va" es la que provocaba las falsas alarmas.

    En NOVA4 el patrón la aceptaba porque Vosk transcribía así el nombre.
    Medido el 26/08: 4 falsas alarmas de cada 6 frases normales. Ahora
    quien confirma es la etapa 2, que sí tiene contexto de lenguaje, así
    que la reserva puede permitirse ser estricta.
    """
    from pathlib import Path

    from nova.voice.wake import DetectorWake

    d = DetectorWake(Path("/no/existe"), "nova")
    assert d._patron.search("nova")
    assert d._patron.search("noba")
    assert not d._patron.search("no va a funcionar el mando")
    assert not d._patron.search("la novia de mi hermano")


def test_detector_de_prueba_se_puede_teledirigir():
    """Los tests de la escucha no pueden depender de tener Vosk instalado."""
    from nova.voice.wake import detector_de_prueba

    d = detector_de_prueba(lambda bloque: b"\x01" in bloque)
    assert d.cargar()
    assert d.escucha(b"\x01\x02")
    assert not d.escucha(b"\x00\x00")


# ── Interrumpir a NOVA mientras habla ───────────────────────────────
#
# `Speaker._interrupt` existía en NOVA4: se ponía en `shut_up()` y se
# limpiaba al principio del bucle... y no se consultaba en ningún sitio.
# Era un flag muerto que hacía creer que la interrupción estaba resuelta.

def _hablante():
    from nova.voice.speaker import Speaker

    # enabled=True pero sin `start()`: nadie consume la cola, así que se
    # puede mirar qué queda dentro sin depender de SAPI ni de tiempos.
    return Speaker(enabled=True)


def test_callar_vacia_lo_que_estaba_en_cola():
    voz = _hablante()
    voz.say("primera frase")
    voz.say("segunda frase")
    assert voz._queue.qsize() == 2

    voz.shut_up()
    assert voz._queue.empty()


def test_callar_marca_la_frase_que_ya_iba_en_camino():
    """La frase que ya salió de la cola no la puede parar vaciar la cola."""
    voz = _hablante()
    voz.say("lo que sea")
    voz.shut_up()
    assert voz._interrupt.is_set()


def test_hablar_de_nuevo_cancela_la_interrupcion():
    """Si no, el "Dime." del wake word se perdería.

    Despertar mientras NOVA habla llama a `shut_up()` y justo después
    dice el saludo: con el flag pegajoso, ese saludo se descartaría.
    """
    voz = _hablante()
    voz.shut_up()
    assert voz._interrupt.is_set()

    voz.say("Dime.")
    assert not voz._interrupt.is_set()
    assert voz._queue.qsize() == 1


def test_texto_vacio_no_ocupa_sitio_en_la_cola():
    voz = _hablante()
    voz.say("   ")
    voz.say("**")  # sólo markdown: `limpiar_para_voz` se lo come
    assert voz._queue.empty()
