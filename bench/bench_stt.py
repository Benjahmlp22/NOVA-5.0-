"""Vosk contra faster-whisper: WER y latencia sobre el mismo audio.

    python bench/sintetizar.py            # audio de prueba con la voz de Windows
    python bench/bench_stt.py             # los dos motores, mismo audio
    python bench/bench_stt.py --sufijo benja --whisper-modelo medium

El audio se lee una sola vez y se remuestrea una sola vez (ver
`audio_io`): si cada motor recibiera el suyo, la comparación mediría el
remuestreo tanto como el modelo.

Los ficheros se llaman `NN_<sufijo>.wav` y la línea NN de `frases.txt` es
la referencia. Así, grabar las mismas frases hablando (`NN_benja.wav`) no
necesita tocar nada del script.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

from bench.audio_io import SAMPLE_RATE, a_int16, leer_mono_16k, normalizar, rms  # noqa: E402

FRASES = RAIZ / "frases.txt"
AUDIO = RAIZ / "audio"


# ── Métrica ──────────────────────────────────────────────────────────

def _normalizar_texto(texto: str) -> list[str]:
    """Minúsculas, sin tildes y sin puntuación: el WER mide palabras.

    "Discord." y "discord" son el mismo acierto; contarlas como error
    infla el WER de los dos motores por igual y tapa la diferencia real.
    """
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in sin_tildes)
    return limpio.split()


def wer(referencia: str, hipotesis: str) -> float:
    """Word Error Rate: (sustituciones + inserciones + borrados) / palabras.

    Levenshtein sobre palabras. 0.0 es perfecto; puede pasar de 1.0 si el
    motor se inventa más palabras de las que había.
    """
    ref, hip = _normalizar_texto(referencia), _normalizar_texto(hipotesis)
    if not ref:
        return 0.0 if not hip else 1.0

    previa = list(range(len(hip) + 1))
    for i, palabra_ref in enumerate(ref, start=1):
        actual = [i]
        for j, palabra_hip in enumerate(hip, start=1):
            coste = 0 if palabra_ref == palabra_hip else 1
            actual.append(min(
                previa[j] + 1,        # borrado
                actual[j - 1] + 1,    # inserción
                previa[j - 1] + coste,  # sustitución
            ))
        previa = actual
    return previa[-1] / len(ref)


@dataclass
class Resultado:
    motor: str
    carga_s: float = 0.0
    wers: list[float] = field(default_factory=list)
    latencias: list[float] = field(default_factory=list)
    transcripciones: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def wer_medio(self) -> float:
        return statistics.mean(self.wers) if self.wers else float("nan")

    @property
    def latencia_media(self) -> float:
        return statistics.mean(self.latencias) if self.latencias else float("nan")

    @property
    def aciertos_exactos(self) -> int:
        return sum(1 for w in self.wers if w == 0.0)


# ── Motores ──────────────────────────────────────────────────────────

def correr_vosk(senales: list, referencias: list[str], modelo: Path) -> Resultado:
    import json

    res = Resultado(motor=f"Vosk ({modelo.name})")
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        t0 = time.monotonic()
        m = Model(str(modelo))
        res.carga_s = time.monotonic() - t0
    except Exception as exc:
        res.error = f"no pude cargar Vosk: {exc}"
        return res

    for senal, referencia in zip(senales, referencias, strict=True):
        rec = KaldiRecognizer(m, SAMPLE_RATE)
        rec.SetWords(False)
        datos = a_int16(senal)
        t0 = time.monotonic()
        rec.AcceptWaveform(datos)
        texto = json.loads(rec.FinalResult() or "{}").get("text", "")
        res.latencias.append(time.monotonic() - t0)
        res.transcripciones.append(texto)
        res.wers.append(wer(referencia, texto))
    return res


def correr_whisper(
    senales: list,
    referencias: list[str],
    *,
    tamano: str,
    compute_type: str,
    device: str,
) -> Resultado:
    res = Resultado(motor=f"faster-whisper {tamano} ({device}/{compute_type})")
    try:
        from nova.voice.cuda import preparar_dlls

        preparar_dlls()
        from faster_whisper import WhisperModel

        t0 = time.monotonic()
        modelo = WhisperModel(tamano, device=device, compute_type=compute_type)
        res.carga_s = time.monotonic() - t0
    except Exception as exc:
        res.error = f"no pude cargar faster-whisper: {exc}"
        return res

    for senal, referencia in zip(senales, referencias, strict=True):
        t0 = time.monotonic()
        try:
            # language="es" fijo: dejar que lo detecte cuesta una pasada
            # extra y, en órdenes de dos palabras, se equivoca.
            segmentos, _ = modelo.transcribe(senal, language="es", beam_size=5)
            texto = " ".join(s.text for s in segmentos).strip()
        except Exception as exc:
            res.error = f"falló transcribiendo: {exc}"
            return res
        res.latencias.append(time.monotonic() - t0)
        res.transcripciones.append(texto)
        res.wers.append(wer(referencia, texto))
    return res


# ── Informe ──────────────────────────────────────────────────────────

def informe(resultados: list[Resultado], referencias: list[str]) -> None:
    print("\n" + "=" * 78)
    print("RESUMEN")
    print("=" * 78)
    print(f"{'motor':<44} {'WER':>7} {'exactas':>9} {'latencia':>10} {'carga':>8}")
    print("-" * 78)
    for r in resultados:
        if r.error:
            print(f"{r.motor:<44} {'—':>7} {'—':>9} {'—':>10} {'—':>8}   {r.error}")
            continue
        print(
            f"{r.motor:<44} {r.wer_medio * 100:6.1f}% "
            f"{r.aciertos_exactos:>4}/{len(r.wers):<4} "
            f"{r.latencia_media:9.2f}s {r.carga_s:7.1f}s"
        )

    print("\n" + "=" * 78)
    print("FRASE A FRASE")
    print("=" * 78)
    for i, referencia in enumerate(referencias):
        print(f"\n{i + 1:02d}. esperado : «{referencia}»")
        for r in resultados:
            if r.error or i >= len(r.transcripciones):
                continue
            marca = "✓" if r.wers[i] == 0.0 else f"{r.wers[i] * 100:.0f}%"
            print(f"    {marca:>5}  {r.motor.split(' (')[0]:<18} «{r.transcripciones[i]}»")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Vosk vs faster-whisper sobre el mismo audio.")
    p.add_argument("--sufijo", default="sintetico",
                   help="qué juego de WAV usar: NN_<sufijo>.wav (por defecto: sintetico)")
    p.add_argument("--whisper-modelo", default="small",
                   help="tiny, base, small, medium, large-v3-turbo...")
    p.add_argument("--compute-type", default="int8_float16")
    p.add_argument("--device", default="auto", help="cuda, cpu o auto")
    p.add_argument("--sin-vosk", action="store_true")
    p.add_argument("--sin-whisper", action="store_true")
    args = p.parse_args(argv)

    if not FRASES.exists():
        print(f"✗ No encuentro {FRASES}")
        return 1
    referencias = [ln.strip() for ln in FRASES.read_text(encoding="utf-8").splitlines() if ln.strip()]

    rutas, usadas = [], []
    for i, referencia in enumerate(referencias, start=1):
        wav = AUDIO / f"{i:02d}_{args.sufijo}.wav"
        if wav.exists():
            rutas.append(wav)
            usadas.append(referencia)
    if not rutas:
        print(f"✗ No hay ningún WAV *_{args.sufijo}.wav en {AUDIO}")
        print("  Genera audio de prueba con:  python bench/sintetizar.py")
        return 1

    print(f"Audio    : {len(rutas)} ficheros *_{args.sufijo}.wav")
    senales = []
    for ruta in rutas:
        senal = leer_mono_16k(ruta)
        senales.append(normalizar(senal))
    duracion = sum(len(s) for s in senales) / SAMPLE_RATE
    niveles = [rms(s) for s in senales]
    print(f"Duración : {duracion:.1f}s en total ({duracion / len(senales):.1f}s de media)")
    print(f"Nivel    : RMS {min(niveles):.4f} – {max(niveles):.4f} tras normalizar")

    device = args.device
    if device == "auto":
        from nova.voice.cuda import hay_gpu

        device = "cuda" if hay_gpu() else "cpu"
        print(f"Dispositivo: {device} (auto)")

    resultados: list[Resultado] = []
    if not args.sin_vosk:
        from nova.config import CONFIG

        print(f"\n→ Vosk ({CONFIG.vosk_model.name})...")
        resultados.append(correr_vosk(senales, usadas, CONFIG.vosk_model))
    if not args.sin_whisper:
        print(f"→ faster-whisper {args.whisper_modelo} en {device}...")
        resultados.append(correr_whisper(
            senales, usadas,
            tamano=args.whisper_modelo,
            compute_type=args.compute_type if device == "cuda" else "int8",
            device=device,
        ))

    informe(resultados, usadas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
