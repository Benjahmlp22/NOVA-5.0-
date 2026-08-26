"""Graba las frases de `frases.txt` hablando, para medir el WER de verdad.

    python bench/grabar.py                       # el camino por defecto
    python bench/grabar.py --device 28 --exclusivo
    python bench/grabar.py --desde 12            # seguir donde lo dejaste

Antes de esto, corre `python bench/comparar_captura.py`: en Windows el
mismo micrófono suena distinto por cada API, y grabar veinte frases por
el camino equivocado ya nos costó un banco entero.

Cada frase se guarda como `audio/NN_<sufijo>.wav`, que es lo que
`bench_stt.py --sufijo <sufijo>` compara contra la línea NN de
`frases.txt`.

Habla como le hablas a NOVA de verdad: mismo sitio, mismo micro, misma
distancia y sin vocalizar de más. Si grabas articulando como un locutor,
el número que salga no describe tu uso real y la decisión que se tome con
él será la equivocada.
"""

from __future__ import annotations

import argparse
import sys
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
    rms,
    tramo_hablado,
)
from nova.voice.captura import (  # noqa: E402
    Camino,
    caminos_para,
    grabar_hasta_silencio,
    medir_ruido,
    umbral_de_voz,
)

FRASES = RAIZ / "frases.txt"
DESTINO = RAIZ / "audio"


def _guardar(senal: np.ndarray, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(a_int16(senal))


def _elegir_camino(args) -> Camino | None:  # noqa: ANN001
    caminos = caminos_para(args.micro)
    if not caminos:
        return None
    if args.device is None:
        return caminos[0]
    for c in caminos:
        if c.device == args.device and c.exclusivo == args.exclusivo:
            return c
    print(f"! El dispositivo {args.device}"
          f"{' en exclusivo' if args.exclusivo else ''} no se puede abrir. Disponibles:")
    for c in caminos:
        print(f"    device {c.device:3d}  {c.etiqueta}")
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Graba el corpus de frases hablando.")
    p.add_argument("--device", type=int, default=None, help="índice del micro (ver nova.doctor)")
    p.add_argument("--exclusivo", action="store_true", help="WASAPI exclusivo: sin efectos de Windows")
    p.add_argument("--micro", default="", help="filtra por nombre, p.ej. G435")
    p.add_argument("--sufijo", default="benja")
    p.add_argument("--desde", type=int, default=1, help="empezar por la frase N")
    p.add_argument("--frases", type=Path, default=FRASES,
                   help="fichero de frases a grabar (por defecto bench/frases.txt)")
    args = p.parse_args(argv)

    camino = _elegir_camino(args)
    if camino is None:
        print("✗ No hay ningún camino de captura utilizable.")
        return 1

    frases = [ln.strip() for ln in args.frases.read_text(encoding="utf-8").splitlines() if ln.strip()]
    DESTINO.mkdir(parents=True, exist_ok=True)

    print(f"Camino: {camino.etiqueta} @ {camino.tasa} Hz → {SAMPLE_RATE} Hz")
    print(f"Voy a grabar {len(frases)} frases en {DESTINO}")
    print("Habla como le hablas a NOVA: mismo sitio, misma distancia, sin vocalizar de más.")
    print("Enter para grabar cada una · 'r' + Enter para repetir la anterior · 'q' para salir.\n")

    ruido = medir_ruido(camino)
    umbral = umbral_de_voz(ruido)
    print(f"Ruido de fondo {ruido:.5f} → umbral de voz {umbral:.5f}\n")

    bandas: list[float] = []
    i = max(1, args.desde)
    while i <= len(frases):
        frase = frases[i - 1]
        destino = DESTINO / f"{i:02d}_{args.sufijo}.wav"
        ya = " (ya grabada, se sobrescribe)" if destino.exists() else ""
        orden = input(
            f"[{i:02d}/{len(frases)}] «{frase}»{ya}\n    Enter para grabar > "
        ).strip().lower()
        if orden == "q":
            break
        if orden == "r":
            i = max(1, i - 1)
            continue

        print("    HABLA AHORA (corta sola cuando te calles)")
        g = grabar_hasta_silencio(camino, umbral)

        if not hay_senal(g.senal):
            print("    ✗ silencio digital: el micro no entrega nada. Repite.\n")
            continue
        if not g.voz_detectada:
            print(f"    ✗ no detecté voz en {g.segundos:.1f}s "
                  f"(bloque más alto {g.pico_bloque:.5f} < umbral {umbral:.5f}). Repite.\n")
            continue
        if g.segundos < 0.4:
            print("    ! demasiado corta, seguramente se cortó. Repite.\n")
            continue

        banda = fraccion_en_banda_de_voz(tramo_hablado(g.senal))
        bandas.append(banda)
        _guardar(g.senal, destino)
        print(f"    ✓ {g.segundos:.1f}s   RMS {rms(g.senal):.4f}   pico {pico(g.senal):.3f}"
              f"   voz {banda * 100:.0f}%   → {destino.name}")
        problema = diagnostico_de_voz(g.senal)
        if problema:
            print(f"    ! {problema}")
        if g.avisos:
            print(f"    ! el driver avisó de {', '.join(g.avisos)} (muestras perdidas = cortes)")
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
            print("    Prueba otro camino:  python bench/comparar_captura.py")
            return 1

    if grabadas:
        print(f"Ahora:  python bench/bench_stt.py --sufijo {args.sufijo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
