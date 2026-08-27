"""Genera la misma frase con todas las voces, para poder compararlas.

La calidad de una voz no se mide, se escucha. Esto deja los WAV en
`muestras_voz/` para que decidas tú cuál quieres, incluyendo la de SAPI
que se usaba antes.

    .venv\Scripts\python.exe bench\muestras_voz.py
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nova.voice.onecore import SintetizadorOneCore  # noqa: E402
from nova.voice.speaker import Speaker  # noqa: E402

FRASE = (
    "Hola Benja. Te he bajado el volumen de Spotify y he apuntado "
    "lo de la cena. ¿Quieres que te avise en media hora?"
)


def main() -> int:
    salida = Path(__file__).resolve().parent.parent / "muestras_voz"
    salida.mkdir(exist_ok=True)

    sinte = SintetizadorOneCore()
    if sinte.start():
        for v in sinte.voces:
            sinte.elegir(v.nombre)
            corto = v.nombre.replace("Microsoft ", "")
            genero = "hombre" if v.es_hombre else "mujer"
            sinte.sintetizar(FRASE, salida / f"onecore-{corto}-{genero}-{v.idioma}.wav")
        sinte.stop()
    else:
        print(f"OneCore no disponible: {sinte.error}")

    # Y la vieja, que es la única forma de saber si el cambio suma.
    altavoz = Speaker(enabled=True)
    altavoz._onecore = SintetizadorOneCore()      # sin arrancar: fuerza SAPI
    senal, sr = altavoz._sintetizar(FRASE)
    if senal is not None:
        with wave.open(str(salida / "ANTES-sapi-helena-desktop.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((senal * 32767).astype("<i2").tobytes())

    for f in sorted(salida.iterdir()):
        print(f"  {f.name:46s} {f.stat().st_size / 1024:6.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
