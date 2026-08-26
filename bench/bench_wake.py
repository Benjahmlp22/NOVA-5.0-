"""Wake word: ¿aguanta el Vosk pequeño, o hace falta el de 2.3 GB?

    python bench/grabar.py --frases bench/frases_wake.txt --sufijo wake --device 28
    python bench/bench_wake.py --sufijo wake

Lo que se decide aquí NO es el WER. Al detector de "NOVA" le da igual
equivocarse en el resto de la frase; sólo tiene que hacer dos cosas:

    despertar cuando dices "nova"          (aciertos)
    NO despertar cuando no lo dices        (falsas alarmas)

Y las falsas alarmas importan tanto como los aciertos: NOVA vive
escuchando todo el día, así que un detector que salta con cualquier cosa
es peor que uno un poco sordo. Por eso `frases_wake.txt` mezcla frases
con "nova" y trampas fonéticas —"no va a funcionar el mando", "la novia
de mi hermano"— que son justo lo que el patrón de NOVA4 se juega: acepta
"no va" como variante de "nova" porque Vosk transcribía así el nombre.

El coste que hay en juego, medido el 26/08:

    vosk-model-es-0.42        2.3 GB   carga 47.2 s
    vosk-model-small-es-0.42   58 MB   carga  0.4 s

48x. Pero Benja ya cambió del pequeño al grande porque el pequeño no
detectaba bien, así que no se vuelve al pequeño sin números — de ahí
este banco.

Se alimenta el audio en bloques, igual que hace `VoiceListener`, y se
mira el resultado PARCIAL: NOVA despierta con el parcial, no esperando al
final de la frase (esperar añadía ~1 s de reacción). Medir sobre el
resultado final daría un número que no describe lo que pasa en marcha.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

import numpy as np  # noqa: E402

from nova.config import CONFIG  # noqa: E402
from nova.voice.audio import SAMPLE_RATE, a_int16, normalizar  # noqa: E402
from nova.voice.listener import _norm  # noqa: E402

FRASES = RAIZ / "frases_wake.txt"
AUDIO = RAIZ / "audio"
BLOQUE = 4000  # el mismo que usa VoiceListener


@dataclass
class Resultado:
    modelo: str
    carga_s: float = 0.0
    aciertos: int = 0
    fallos: list[str] = field(default_factory=list)
    falsas_alarmas: list[str] = field(default_factory=list)
    correctos_negativos: int = 0
    error: str = ""


def _cargar(ruta: Path) -> np.ndarray:
    with wave.open(str(ruta)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0


def despierta(rec, senal: np.ndarray, patron) -> bool:  # noqa: ANN001
    """Alimenta el audio en bloques y dice si el patrón salta en algún parcial.

    Copia deliberada del camino real de `VoiceListener._feed`: bloques del
    mismo tamaño y decisión sobre el parcial, no sobre el final.
    """
    datos = a_int16(senal)
    ancho = BLOQUE * 2  # int16
    for i in range(0, len(datos), ancho):
        trozo = datos[i:i + ancho]
        if not trozo:
            continue
        if rec.AcceptWaveform(trozo):
            texto = _norm(json.loads(rec.Result() or "{}").get("text", ""))
        else:
            texto = _norm(json.loads(rec.PartialResult() or "{}").get("partial", ""))
        if texto and patron.search(texto):
            return True
    final = _norm(json.loads(rec.FinalResult() or "{}").get("text", ""))
    return bool(final and patron.search(final))


def probar(modelo: Path, casos: list[tuple[str, np.ndarray, bool]], patron) -> Resultado:  # noqa: ANN001
    res = Resultado(modelo=modelo.name)
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        t0 = time.monotonic()
        m = Model(str(modelo))
        res.carga_s = time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001
        res.error = f"no pude cargar {modelo.name}: {exc}"
        return res

    for frase, senal, deberia in casos:
        rec = KaldiRecognizer(m, SAMPLE_RATE)
        rec.SetWords(False)
        salto = despierta(rec, senal, patron)
        if deberia and salto:
            res.aciertos += 1
        elif deberia and not salto:
            res.fallos.append(frase)
        elif not deberia and salto:
            res.falsas_alarmas.append(frase)
        else:
            res.correctos_negativos += 1
    return res


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compara detectores de wake word.")
    p.add_argument("--sufijo", default="wake")
    p.add_argument("--extra", type=Path, action="append", default=[],
                   help="otro modelo de Vosk a comparar (se puede repetir)")
    args = p.parse_args(argv)

    frases = [ln.strip() for ln in FRASES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    casos = []
    for i, frase in enumerate(frases, start=1):
        ruta = AUDIO / f"{i:02d}_{args.sufijo}.wav"
        if not ruta.exists():
            continue
        # La verdad la da el guion, no la transcripción: sabemos qué se dijo.
        casos.append((frase, normalizar(_cargar(ruta)), "nova" in frase.lower().split()))

    if not casos:
        print(f"✗ No hay ningún *_{args.sufijo}.wav en {AUDIO}")
        print("  Grábalos con:")
        print(f"    python bench/grabar.py --frases {FRASES} --sufijo {args.sufijo}")
        return 1

    positivos = sum(1 for _, _, d in casos if d)
    print(f"Casos: {len(casos)} ({positivos} con «nova», {len(casos) - positivos} trampas)\n")

    # El patrón es el mismo que decide en marcha, incluida la variante
    # "no va" — sin ella el detector es otro y la comparación no valdría.
    from nova.voice.listener import VoiceListener

    patron = VoiceListener(CONFIG.vosk_model, CONFIG.wake_word).wake_re
    print(f"Patrón: {patron.pattern}\n")

    modelos = [CONFIG.vosk_model, *args.extra]
    pequeno = CONFIG.vosk_model.parent / "vosk-model-small-es-0.42"
    if pequeno.exists() and pequeno not in modelos:
        modelos.append(pequeno)

    resultados = [probar(m, casos, patron) for m in modelos]

    print(f"{'modelo':<32}{'despierta':>11}{'falsas':>9}{'carga':>9}")
    print("-" * 62)
    for r in resultados:
        if r.error:
            print(f"{r.modelo:<32} {r.error}")
            continue
        print(f"{r.modelo:<32}{r.aciertos:>6}/{positivos:<4}"
              f"{len(r.falsas_alarmas):>6}/{len(casos) - positivos:<3}{r.carga_s:>8.1f}s")

    for r in resultados:
        if r.error:
            continue
        if r.fallos:
            print(f"\n{r.modelo} — NO despertó con:")
            for f in r.fallos:
                print(f"    «{f}»")
        if r.falsas_alarmas:
            print(f"\n{r.modelo} — despertó sin motivo con:")
            for f in r.falsas_alarmas:
                print(f"    «{f}»")

    print("\nUna falsa alarma cuesta más que un fallo: NOVA escucha todo el día.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
