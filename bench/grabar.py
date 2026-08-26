"""Graba las frases de `frases.txt` hablando, para medir el WER de verdad.

    python bench/grabar.py
    python bench/grabar.py --device 28        # el micro por WASAPI
    python bench/grabar.py --desde 12         # seguir donde lo dejaste

Cada frase se guarda como `audio/NN_benja.wav`, que es lo que
`bench_stt.py --sufijo benja` compara contra la línea NN de `frases.txt`.

Habla como le hablas a NOVA de verdad: mismo sitio, mismo micro, misma
distancia y sin vocalizar de más. Si grabas articulando como un locutor,
el número que salga no describe tu uso real y la decisión que se tome con
él será la equivocada.

El corte de cada frase lo decide el silencio, no un cronómetro — es el
mismo criterio que va a usar NOVA en la Fase 2, así que de paso se ve si
el umbral está bien puesto para tu micro y tu sala.
"""

from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

from nova.voice.audio import (  # noqa: E402
    FRACCION_VOZ_MINIMA,
    SAMPLE_RATE,
    a_int16,
    diagnostico_de_voz,
    fraccion_en_banda_de_voz,
    hay_senal,
    pico,
    remuestrear,
    rms,
    tasa_de_captura,
    tramo_hablado,
)

FRASES = RAIZ / "frases.txt"
DESTINO = RAIZ / "audio"

BLOQUE_MS = 100         # tamaño de bloque en milisegundos
SILENCIO_FIN = 0.8      # s de silencio que cierran la frase
MAX_FRASE = 12.0        # tope duro, por si el umbral no salta nunca
ESPERA_INICIO = 6.0     # s esperando a que empieces a hablar


def _umbral(device: int | None, captura: int) -> float:
    """Mide el ruido de fondo y pone el umbral por encima.

    Un umbral fijo no vale: la misma cifra que en una habitación callada
    corta a media palabra con un ventilador o un PC ruidoso al lado.
    """
    import sounddevice as sd

    print("  Calibrando el ruido de fondo — no hables durante 2 s...")
    fondo = sd.rec(int(2 * captura), samplerate=captura,
                   channels=1, dtype="int16", device=device)
    sd.wait()
    senal = remuestrear(fondo.reshape(-1).astype(np.float32) / 32768.0, captura)
    ruido = rms(senal)
    # x4 sobre el ruido, con un suelo por si la sala está muy callada.
    umbral = max(ruido * 4, 0.008)
    print(f"  Ruido de fondo: RMS {ruido:.5f} → umbral de voz {umbral:.5f}\n")
    return umbral


def _grabar_frase(device: int | None, umbral: float, captura: int) -> np.ndarray:
    """Graba hasta que te calles. Devuelve la frase entera, con margen.

    Se captura a la tasa nativa del dispositivo y se baja a 16 kHz al
    final, de una pasada: WASAPI en modo compartido no abre el micro a
    otra tasa que la suya, y remuestrear nosotros es mejor que dejárselo
    a PortAudio (ver `nova/voice/audio.py`).
    """
    import sounddevice as sd

    bloque_n = int(captura * BLOQUE_MS / 1000)
    trozos: list[np.ndarray] = []
    hablando = False
    silencio_seguido = 0.0
    t0 = time.monotonic()

    with sd.InputStream(samplerate=captura, blocksize=bloque_n, channels=1,
                        dtype="int16", device=device) as flujo:
        while True:
            datos, _ = flujo.read(bloque_n)
            bloque = datos.reshape(-1).astype(np.float32) / 32768.0
            trozos.append(bloque)
            duracion = time.monotonic() - t0

            if rms(bloque) >= umbral:
                hablando = True
                silencio_seguido = 0.0
            elif hablando:
                silencio_seguido += BLOQUE_MS / 1000
                if silencio_seguido >= SILENCIO_FIN:
                    break

            if not hablando and duracion > ESPERA_INICIO:
                break
            if duracion > MAX_FRASE:
                break

    if not trozos:
        return np.zeros(0, dtype=np.float32)
    return remuestrear(np.concatenate(trozos), captura)


def _guardar(senal: np.ndarray, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(a_int16(senal))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Graba el corpus de frases hablando.")
    p.add_argument("--device", type=int, default=None, help="índice del micro (ver nova.doctor)")
    p.add_argument("--sufijo", default="benja")
    p.add_argument("--desde", type=int, default=1, help="empezar por la frase N")
    args = p.parse_args(argv)

    frases = [ln.strip() for ln in FRASES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    DESTINO.mkdir(parents=True, exist_ok=True)

    print(f"Voy a grabar {len(frases)} frases en {DESTINO}")
    print("Habla como le hablas a NOVA: mismo sitio, misma distancia, sin vocalizar de más.")
    print("Enter para grabar cada una · 'r' + Enter para repetir la anterior · 'q' para salir.\n")

    captura = tasa_de_captura(args.device)
    if captura != SAMPLE_RATE:
        print(f"  Capturando a {captura} Hz y bajando a {SAMPLE_RATE} Hz aquí.")
    umbral = _umbral(args.device, captura)
    bandas: list[float] = []

    i = max(1, args.desde)
    while i <= len(frases):
        frase = frases[i - 1]
        destino = DESTINO / f"{i:02d}_{args.sufijo}.wav"
        ya = " (ya grabada, se sobrescribe)" if destino.exists() else ""
        orden = input(f"[{i:02d}/{len(frases)}] «{frase}»{ya}\n    Enter para grabar > ").strip().lower()
        if orden == "q":
            break
        if orden == "r":
            i = max(1, i - 1)
            continue

        print("    grabando... (para cuando te calles)")
        senal = _grabar_frase(args.device, umbral, captura)

        if not hay_senal(senal):
            print("    ✗ silencio digital: el micro no entrega nada. Repite esta frase.\n")
            continue
        segundos = len(senal) / SAMPLE_RATE
        if segundos < 0.4:
            print("    ! demasiado corta, seguramente se cortó. Repite.\n")
            continue

        problema = diagnostico_de_voz(senal)
        banda = fraccion_en_banda_de_voz(tramo_hablado(senal))
        bandas.append(banda)

        _guardar(senal, destino)
        print(f"    ✓ {segundos:.1f}s   RMS {rms(senal):.4f}   pico {pico(senal):.3f}"
              f"   voz {banda * 100:.0f}%   → {destino.name}")
        if problema:
            print(f"    ! {problema}")
        print()
        i += 1

    grabadas = len(list(DESTINO.glob(f"*_{args.sufijo}.wav")))
    print(f"\n{grabadas}/{len(frases)} frases grabadas.")

    # La señal fuerte es la mediana de la sesión, no una frase suelta:
    # las colas de las dos distribuciones se solapan (ver audio.py).
    if bandas:
        mediana = float(np.median(bandas))
        print(f"Energía en la banda de voz: mediana {mediana * 100:.0f}%")
        if mediana < FRACCION_VOZ_MINIMA:
            print()
            print("  ✗ ESTE CORPUS NO SIRVE. Casi toda la energía está por debajo")
            print("    de la banda de voz: sale retumbe, no habla, y los")
            print("    reconocedores devolverán basura por mucho que el nivel")
            print("    parezca correcto.")
            print("    Prueba otro dispositivo de la lista de `nova.doctor`: el")
            print("    mismo micro por otra API de audio puede dar otra cosa.")
            return 1

    if grabadas:
        print(f"Ahora:  python bench/bench_stt.py --sufijo {args.sufijo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
