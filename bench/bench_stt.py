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

# La consola de Windows llega en cp1252 y este informe usa flechas y
# vistos. Sin esto, el banco revienta al imprimir en vez de al medir.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

from bench.audio_io import SAMPLE_RATE, a_int16, leer_mono_16k, normalizar, rms  # noqa: E402

FRASES = RAIZ / "frases.txt"
AUDIO = RAIZ / "audio"


# ── Métrica ──────────────────────────────────────────────────────────

_UNIDADES = ["cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",
             "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",
             "dieciseis", "diecisiete", "dieciocho", "diecinueve", "veinte",
             "veintiuno", "veintidos", "veintitres", "veinticuatro", "veinticinco",
             "veintiseis", "veintisiete", "veintiocho", "veintinueve"]
_DECENAS = {30: "treinta", 40: "cuarenta", 50: "cincuenta", 60: "sesenta",
            70: "setenta", 80: "ochenta", 90: "noventa"}
_CENTENAS = {1: "ciento", 2: "doscientos", 3: "trescientos", 4: "cuatrocientos",
             5: "quinientos", 6: "seiscientos", 7: "setecientos", 8: "ochocientos",
             9: "novecientos"}


def numero_a_palabras(n: int) -> str:
    """Deletrea un entero 0-9999 en español, sin tildes.

    Hace falta porque Whisper escribe "4070" donde el usuario dijo
    "cuatro mil setenta", y Vosk escribe las palabras. Sin esto el WER
    castiga a Whisper por transcribir MEJOR — que es lo contrario de lo
    que la métrica debe premiar. Medido: subía su WER del 1.6% al 6.1%,
    o sea que la conclusión del banco dependía de un detalle de formato.
    """
    if n < 0 or n > 9999:
        return str(n)
    if n < 30:
        return _UNIDADES[n]
    if n < 100:
        decena, resto = (n // 10) * 10, n % 10
        return _DECENAS[decena] if not resto else f"{_DECENAS[decena]} y {_UNIDADES[resto]}"
    if n < 1000:
        centena, resto = n // 100, n % 100
        if n == 100:
            return "cien"
        cabeza = _CENTENAS[centena]
        return cabeza if not resto else f"{cabeza} {numero_a_palabras(resto)}"
    millar, resto = n // 1000, n % 1000
    cabeza = "mil" if millar == 1 else f"{numero_a_palabras(millar)} mil"
    return cabeza if not resto else f"{cabeza} {numero_a_palabras(resto)}"


def _normalizar_texto(texto: str) -> list[str]:
    """Minúsculas, sin tildes, sin puntuación y con los números deletreados.

    "Discord." y "discord" son el mismo acierto; contarlas como error
    infla el WER de los dos motores por igual y tapa la diferencia real.

    Y los números se deletrean SIEMPRE, vengan como cifra o como palabra:
    lo que se mide es si el reconocedor entendió lo que se dijo, no cómo
    decidió escribirlo.
    """
    t = (texto or "").lower().replace("%", " por ciento ")
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", t)
        if unicodedata.category(c) != "Mn"
    )
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in sin_tildes)
    palabras: list[str] = []
    for p in limpio.split():
        if p.isdigit() and len(p) <= 4:
            palabras.extend(numero_a_palabras(int(p)).split())
        else:
            palabras.append(p)
    return palabras


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


# Vocabulario que NOVA oye todos los días y Whisper no espera. Se le pasa
# como `initial_prompt`: el decodificador lo ve como contexto previo, así
# que estas grafías dejan de ser sorpresas.
#
# No es hacer trampa al banco: son las palabras que el usuario dice de
# verdad a este asistente, y un asistente que no conoce los nombres de
# las apps que abre no sirve. Lo que sí sería trampa es meter aquí las
# frases del corpus, y no está ninguna.
#
# Lleva además el nombre en contexto de nombre ("NOVA, abre Discord"), y
# eso no es adorno: "nova" y "no va" son la MISMA secuencia de fonemas en
# español, así que ningún modelo acústico puede separarlas — sólo el
# contexto. Con este prompt, Whisper small distingue las 8 frases con
# "nova" de las 6 trampas ("no va a funcionar el mando", "la novia de mi
# hermano") sin fallar ninguna. Vosk, que no tiene contexto de lenguaje,
# no pasa de 5/8 con 3 falsas alarmas.
PROMPT_DOMINIO = (
    "Órdenes habladas al asistente NOVA en español. Se le llama por su "
    "nombre: NOVA, abre Discord. NOVA, qué hora es. Oye, NOVA. "
    "Vocabulario habitual: Discord, Chrome, Spotify, Steam, WhatsApp, "
    "Visual Studio Code, bloc de notas, captura de pantalla, memoria RAM, "
    "volumen, RTX 4070, vatios, fuente de alimentación, carpeta, archivo."
)


def correr_whisper(
    senales: list,
    referencias: list[str],
    *,
    tamano: str,
    compute_type: str,
    device: str,
    prompt: str = "",
) -> Resultado:
    etiqueta_prompt = " +vocab" if prompt else ""
    res = Resultado(motor=f"faster-whisper {tamano}{etiqueta_prompt} ({device}/{compute_type})")
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
            segmentos, _ = modelo.transcribe(
                senal,
                language="es",
                beam_size=5,
                initial_prompt=prompt or None,
                # Cada orden es independiente: arrastrar la anterior como
                # contexto hace que una transcripción mala contamine la
                # siguiente, y en un asistente los turnos no son un texto
                # continuo.
                condition_on_previous_text=False,
            )
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
    p.add_argument("--con-vocabulario", action="store_true",
                   help="prueba tambien Whisper con el vocabulario de NOVA como contexto")
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
        if args.con_vocabulario:
            print(f"→ faster-whisper {args.whisper_modelo} + vocabulario de NOVA...")
            resultados.append(correr_whisper(
                senales, usadas,
                tamano=args.whisper_modelo,
                compute_type=args.compute_type if device == "cuda" else "int8",
                device=device,
                prompt=PROMPT_DOMINIO,
            ))

    informe(resultados, usadas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
