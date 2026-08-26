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
