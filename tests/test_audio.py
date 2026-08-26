"""Tratamiento de señal: nivel, silencio y conversiones.

Lo importante aquí no es la aritmética, es distinguir "sala callada" de
"micro apagado". El 26/08 todos los dispositivos de entrada de esta
máquina devolvían pico 0.0000 y NOVA no tenía forma de decirlo: el
síntoma ("no me entiende") es idéntico al de un reconocedor malo.
"""

from __future__ import annotations

import numpy as np
import pytest

from nova.voice.audio import (
    RMS_OBJETIVO,
    a_int16,
    de_int16,
    hay_senal,
    normalizar,
    pico,
    rms,
)


def _tono(segundos=0.5, amplitud=0.2, sr=16000):
    t = np.linspace(0, segundos, int(sr * segundos), endpoint=False)
    return (amplitud * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


# ── Silencio digital vs sala callada ─────────────────────────────────

def test_ceros_no_son_senal():
    """Micro apagado, muteado por hardware o dispositivo equivocado."""
    assert not hay_senal(np.zeros(16000, dtype=np.float32))


def test_sala_callada_si_es_senal():
    """Un micro vivo siempre tiene suelo de ruido: eso no es "sin señal"."""
    suelo = (np.random.default_rng(0).normal(0, 0.0008, 16000)).astype(np.float32)
    assert hay_senal(suelo)


def test_senal_vacia_no_revienta():
    vacia = np.zeros(0, dtype=np.float32)
    assert rms(vacia) == 0.0
    assert pico(vacia) == 0.0
    assert not hay_senal(vacia)


# ── Normalización ────────────────────────────────────────────────────

def test_normalizar_sube_al_objetivo():
    flojo = _tono(amplitud=0.005)
    assert rms(normalizar(flojo)) == pytest.approx(RMS_OBJETIVO, rel=0.05)


def test_normalizar_no_satura():
    """Subir hasta distorsionar hace más daño que el nivel bajo que arregla."""
    fuerte = _tono(amplitud=0.9)
    salida = normalizar(fuerte, objetivo=0.9)
    assert pico(salida) <= 1.0


def test_normalizar_deja_el_silencio_en_paz():
    """Amplificar ceros sólo amplifica ruido de cuantización."""
    ceros = np.zeros(1000, dtype=np.float32)
    assert not np.any(normalizar(ceros))


# ── Conversión ───────────────────────────────────────────────────────

def test_ida_y_vuelta_int16():
    original = _tono(amplitud=0.5)
    recuperada = de_int16(a_int16(original))
    assert np.allclose(original, recuperada, atol=1e-4)


def test_a_int16_recorta_lo_que_se_sale():
    """Sin recortar, el desbordamiento suena a chasquido, no a volumen."""
    fuera = np.array([2.0, -2.0], dtype=np.float32)
    recuperada = de_int16(a_int16(fuera))
    assert np.all(np.abs(recuperada) <= 1.0)


# ── Remuestreo ───────────────────────────────────────────────────────
#
# Hace falta porque WASAPI en modo compartido sólo abre el micro a su
# tasa nativa: pedirle 16 kHz da "Invalid sample rate". Se captura a
# 48 kHz y se baja aquí, que además es mejor que dejárselo a PortAudio.

def _seno(hz, segundos=0.5, sr=48000, amplitud=0.5):
    t = np.linspace(0, segundos, int(sr * segundos), endpoint=False)
    return (amplitud * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_misma_tasa_no_toca_nada():
    from nova.voice.audio import remuestrear

    s = _seno(440, sr=16000)
    assert np.array_equal(remuestrear(s, 16000, 16000), s)


def test_48k_a_16k_deja_un_tercio_de_muestras():
    from nova.voice.audio import SAMPLE_RATE, remuestrear

    s = _seno(440, segundos=1.0, sr=48000)
    salida = remuestrear(s, 48000, SAMPLE_RATE)
    assert abs(len(salida) - SAMPLE_RATE) <= 2


def test_un_tono_que_cabe_sobrevive_al_remuestreo():
    """440 Hz está muy por debajo de la nueva Nyquist: tiene que pasar entero."""
    from nova.voice.audio import remuestrear, rms

    s = _seno(440, sr=48000)
    salida = remuestrear(s, 48000, 16000)
    assert rms(salida) == pytest.approx(rms(s), rel=0.1)


def test_lo_que_no_cabe_se_filtra_en_vez_de_doblarse():
    """10 kHz no cabe en 16 kHz: sin filtro reaparecería como 6 kHz.

    Es el error clásico de "coger una muestra de cada tres". Y donde más
    duele es en la consonante — la /s/ y la /f/ viven arriba —, que es
    justo lo que el reconocedor necesita para distinguir palabras.
    """
    from nova.voice.audio import remuestrear, rms

    s = _seno(10000, sr=48000)
    salida = remuestrear(s, 48000, 16000)
    assert rms(salida) < 0.1 * rms(s), "el tono se ha doblado en vez de filtrarse"


def test_tasa_no_entera_tambien_funciona():
    """44100 → 16000 no es divisible: cae en el resampler de PyAV."""
    from nova.voice.audio import remuestrear

    s = _seno(440, segundos=1.0, sr=44100)
    salida = remuestrear(s, 44100, 16000)
    # Tolerancia amplia: el resampler puede cerrar el último bloque.
    assert 15500 <= len(salida) <= 16500


def test_senal_vacia_se_remuestrea_sin_romperse():
    from nova.voice.audio import remuestrear

    assert remuestrear(np.zeros(0, dtype=np.float32), 48000, 16000).size == 0
