"""Genera audio de prueba con la voz SAPI5 del propio Windows.

    python bench/sintetizar.py

Para qué sirve: comprobar que faster-whisper carga y transcribe en la
GPU **sin necesitar micrófono**, y medir su latencia real en esta
máquina. Eso es lo único que puede tumbar el plan de la Fase 2, y se
puede saber hoy.

Para qué NO sirve: para el WER de verdad. Una voz sintética es limpia,
sin ruido de sala, sin recortes de códec y sin el remuestreo del micro —
justo las tres cosas que hacen que Vosk se equivoque en la práctica.
Cualquier número de precisión que salga de aquí es un techo optimista,
no una medida. Las cifras que valen salen de `bench/audio/*_benja.wav`,
grabadas hablando.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
FRASES = RAIZ / "frases.txt"
DESTINO = RAIZ / "audio"


def _motor():
    # Motor nuevo por frase, igual que en `nova/voice/speaker.py`: SAPI5
    # sólo habla de verdad la primera vez que se reutiliza un motor, y
    # `pyttsx3.init()` recicla el anterior (ver el docstring de speaker).
    from pyttsx3.engine import Engine

    motor = Engine()
    motor.setProperty("rate", 175)  # algo más lento que la voz de NOVA: se articula mejor
    for v in motor.getProperty("voices"):
        blob = f"{v.id} {getattr(v, 'name', '')}".lower()
        if "es-" in blob or "spanish" in blob or "español" in blob or "helena" in blob:
            motor.setProperty("voice", v.id)
            break
    return motor


def main() -> int:
    if not FRASES.exists():
        print(f"✗ No encuentro {FRASES}")
        return 1

    frases = [ln.strip() for ln in FRASES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    DESTINO.mkdir(parents=True, exist_ok=True)

    print(f"Sintetizando {len(frases)} frases en {DESTINO}\n")
    t0 = time.monotonic()
    for i, frase in enumerate(frases, start=1):
        salida = DESTINO / f"{i:02d}_sintetico.wav"
        motor = _motor()
        motor.save_to_file(frase, str(salida))
        motor.runAndWait()
        del motor
        kb = salida.stat().st_size / 1024 if salida.exists() else 0
        estado = f"{kb:6.0f} KB" if kb else "   FALLÓ"
        print(f"  {i:02d}  {estado}  «{frase}»")

    print(f"\nListo en {time.monotonic() - t0:.1f}s.")
    print("Recuerda: esto es un techo optimista, no un WER real (ver docstring).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
