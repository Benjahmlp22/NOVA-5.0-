"""La métrica del banco de pruebas de STT.

Un WER mal calculado hace peor daño que no medir: da un número, y los
números convencen. Estos tests fijan que mide lo que dice medir.
"""

from __future__ import annotations

import pytest

from bench.bench_stt import _normalizar_texto, wer


def test_transcripcion_perfecta_es_cero():
    assert wer("abre discord", "abre discord") == 0.0


def test_la_puntuacion_y_las_tildes_no_cuentan_como_error():
    """"Discord." y "discord" son el mismo acierto.

    Whisper puntúa y pone tildes; Vosk no. Sin normalizar, el WER
    castigaría a Whisper por escribir mejor.
    """
    assert wer("que temperatura hace", "¿Qué temperatura hace?") == 0.0
    assert wer("hasta luego nova", "Hasta luego, NOVA.") == 0.0


def test_una_palabra_mal_de_dos():
    assert wer("abre discord", "abre discordia") == pytest.approx(0.5)


def test_palabra_de_menos():
    assert wer("abre el bloc de notas", "abre bloc de notas") == pytest.approx(1 / 5)


def test_palabra_de_mas():
    assert wer("que hora es", "y que hora es") == pytest.approx(1 / 3)


def test_transcripcion_vacia_es_todo_error():
    """El caso de Vosk cuando no entiende nada: silencio, no disparate."""
    assert wer("abre discord", "") == 1.0


def test_puede_pasar_de_uno_si_se_inventa_palabras():
    """Un motor que alucina es peor que uno que calla, y el número lo dice."""
    assert wer("que hora es", "no va a ser la hora de comer todavia") > 1.0


def test_referencia_vacia_no_revienta():
    assert wer("", "") == 0.0
    assert wer("", "algo") == 1.0


def test_normalizar_parte_en_palabras():
    assert _normalizar_texto("¡Abre, Discord!") == ["abre", "discord"]
    assert _normalizar_texto("") == []


# ── Los números se comparan por lo que se dijo, no por cómo se escribió ──

from bench.bench_stt import numero_a_palabras  # noqa: E402


@pytest.mark.parametrize("n,esperado", [
    (0, "cero"),
    (7, "siete"),
    (15, "quince"),
    (21, "veintiuno"),
    (30, "treinta"),
    (31, "treinta y uno"),
    (100, "cien"),
    (150, "ciento cincuenta"),
    (750, "setecientos cincuenta"),
    (1000, "mil"),
    (4070, "cuatro mil setenta"),
    (2026, "dos mil veintiseis"),
])
def test_numero_a_palabras(n, esperado):
    assert numero_a_palabras(n) == esperado


def test_cifra_y_palabra_son_el_mismo_acierto():
    """Whisper escribe "4070"; el usuario dijo "cuatro mil setenta".

    Sin esto, la métrica castiga a Whisper por transcribir mejor y la
    conclusión del banco depende de un detalle de formato.
    """
    ref = "busca en internet el precio de la cuatro mil setenta"
    assert wer(ref, "Busca en internet el precio de la 4070.") == 0.0


def test_porcentaje_escrito_de_las_dos_formas():
    ref = "pon el volumen al treinta por ciento"
    assert wer(ref, "Pon el volumen al 30%.") == 0.0


def test_un_numero_mal_sigue_siendo_error():
    """Normalizar formato no puede tapar un número entendido mal."""
    ref = "pon el volumen al treinta por ciento"
    assert wer(ref, "Pon el volumen al 40%.") > 0.0
