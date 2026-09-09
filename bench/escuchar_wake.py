"""Aislar el coste del wake word con el mismo micrófono, modelo y remuestreo.

Imprime su PID para bench.medir_energia. No abre Qt, Whisper, CLIP ni Ollama.
--sin-reconocedor permite medir audio/RMS como referencia; --vad compara
la puerta experimental. No se guardan grabaciones.
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np

from nova.config import CONFIG
from nova.voice.audio import a_int16, remuestrear, rms
from nova.voice.listener import VoiceListener, medir_ruido, umbral_de_voz
from nova.voice.puerta_energia import PuertaEnergia


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--segundos", type=float, default=660)
    parser.add_argument("--bloque-ms", type=int, choices=(30, 60, 90), default=30)
    parser.add_argument("--sin-reconocedor", action="store_true")
    parser.add_argument("--vad", action="store_true")
    args = parser.parse_args()
    if args.segundos <= 0:
        parser.error("La duración debe ser positiva")
    if args.vad and args.sin_reconocedor:
        parser.error("Compara VAD con el reconocedor activado")

    import sounddevice as sd

    oyente = VoiceListener(CONFIG.wake_model, CONFIG.wake_word,
                           device=CONFIG.mic_device, exclusivo=CONFIG.mic_exclusive)
    camino = oyente._elegir_camino()
    if camino is None:
        parser.error("No encuentro un micrófono compatible")
    if not args.sin_reconocedor and not oyente.detector.cargar():
        parser.error(oyente.detector.error)
    umbral = umbral_de_voz(medir_ruido(camino, segundos=1.0))
    puerta = PuertaEnergia(oyente.detector, args.bloque_ms) if args.vad else None
    frames = int(camino.tasa * args.bloque_ms / 1000)
    disparos = perdidos = 0
    # read bloquea hasta recibir audio. Un sleep adicional perdería sonido;
    # el objetivo aquí es medir la escucha, no lograr un 0% dejando de oír.
    with sd.InputStream(samplerate=camino.tasa, channels=1, dtype="int16",
                        device=camino.device, blocksize=frames,
                        extra_settings=camino.extra()) as entrada:
        print(f"Listo. PID={os.getpid()}; bloque={args.bloque_ms} ms; inicia el medidor externo.", flush=True)
        fin = time.monotonic() + args.segundos
        try:
            while time.monotonic() < fin:
                bruto, desborde = entrada.read(frames)
                perdidos += int(desborde)
                bloque = remuestrear(bruto.reshape(-1).astype(np.float32) / 32768, camino.tasa)
                nivel = rms(bloque)
                if args.sin_reconocedor:
                    continue
                pcm = a_int16(bloque)
                salto = (puerta.escucha(pcm, nivel, umbral) if puerta
                         else oyente.detector.escucha(pcm))
                if salto:
                    disparos += 1
                    oyente.detector.reiniciar()
        except KeyboardInterrupt:
            pass
    print(f"Disparos: {disparos}; desbordes de audio: {perdidos}.")


if __name__ == "__main__":
    main()
