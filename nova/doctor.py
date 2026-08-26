"""Diagnóstico del oído de NOVA.

    python -m nova.doctor              graba 5 s y transcribe con las dos etapas
    python -m nova.doctor --listar     sólo la lista de micrófonos
    python -m nova.doctor --device 28 --segundos 8
    python -m nova.doctor --guardar prueba.wav

Existe porque "NOVA no me entiende" tiene al menos cuatro causas que
producen exactamente el mismo síntoma: el micro está apagado o muteado
por hardware, el dispositivo por defecto no es el que crees (un cable
virtual, la webcam), el nivel de entrada es tan bajo que la consonante se
pierde, o el reconocedor es malo.  Sin separar esas cuatro, cualquier
trabajo sobre el modelo es a ciegas.

Caso real que lo motivó: el 26/08 todos los dispositivos de entrada de
esta máquina devolvían pico 0.0000 — silencio digital, no sala callada —
y nada en NOVA lo decía.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# La consola de Windows llega en cp1252 y este informe usa flechas y
# vistos: sin esto reventaría al imprimir, no al diagnosticar.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .config import CONFIG  # noqa: E402
from .voice.audio import SAMPLE_RATE, a_int16, hay_senal, normalizar, pico, rms  # noqa: E402

BIEN, MAL, AVISO = "✓", "✗", "!"


def _titulo(texto: str) -> None:
    print(f"\n{texto}")
    print("─" * max(len(texto), 60))


# ── Entorno ──────────────────────────────────────────────────────────

def revisar_entorno() -> None:
    _titulo("Entorno")
    print(f"  Python      {sys.version.split()[0]}")

    try:
        import sounddevice  # noqa: F401
        print(f"  {BIEN} sounddevice disponible")
    except ImportError:
        print(f"  {MAL} falta sounddevice (pip install sounddevice)")

    try:
        import vosk  # noqa: F401
        estado = BIEN if CONFIG.voice_available else MAL
        print(f"  {estado} Vosk        modelo: {CONFIG.vosk_model}")
        if not CONFIG.voice_available:
            print("      No hay modelo ahí. Mira NOVA_VOSK_MODEL en .env.")
    except ImportError:
        print(f"  {MAL} falta vosk")

    try:
        from .voice.cuda import hay_gpu

        if hay_gpu():
            import ctranslate2

            print(f"  {BIEN} CUDA        CTranslate2 {ctranslate2.__version__}, "
                  f"{ctranslate2.get_cuda_device_count()} GPU")
        else:
            print(f"  {AVISO} CUDA        no disponible: faster-whisper iría por CPU")
    except ImportError:
        print(f"  {AVISO} falta faster-whisper: sólo estaría Vosk")

    from .llm.ollama import OllamaClient

    llm = OllamaClient(CONFIG.ollama_url, CONFIG.model)
    estado = BIEN if llm.available() else MAL
    print(f"  {estado} Ollama      {CONFIG.model} en {CONFIG.ollama_url}")
    llm.close()


# ── Dispositivos ─────────────────────────────────────────────────────

def listar_dispositivos() -> None:
    _titulo("Micrófonos")
    try:
        import sounddevice as sd
    except ImportError:
        print("  No puedo listar nada sin sounddevice.")
        return

    apis = {i: h["name"] for i, h in enumerate(sd.query_hostapis())}
    por_defecto = sd.default.device[0]
    configurado = CONFIG.mic_device

    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        marcas = []
        if i == por_defecto:
            marcas.append("predeterminado")
        if configurado is not None and i == configurado:
            marcas.append("NOVA_MIC_DEVICE")
        sufijo = f"   ← {', '.join(marcas)}" if marcas else ""
        print(f"  {i:3d}  {d['name'][:44]:<44} {apis.get(d['hostapi'], '?'):<10}"
              f" {int(d['default_samplerate']):>6} Hz{sufijo}")

    print("\n  Elige uno con:  python -m nova.doctor --device N")
    print("  Y fíjalo en .env con:  NOVA_MIC_DEVICE=N")


# ── Grabación ────────────────────────────────────────────────────────

def grabar(device: int | None, segundos: float):
    import numpy as np
    import sounddevice as sd

    print(f"\n  Grabando {segundos:.0f}s... habla ahora.")
    for queda in range(int(segundos), 0, -1):
        print(f"    {queda}...", end="\r", flush=True)
        time.sleep(1)
    datos = sd.rec(int(segundos * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="int16", device=device)
    sd.wait()
    print("    listo.      ")
    return (datos.reshape(-1).astype(np.float32) / 32768.0)


def informar_nivel(senal) -> bool:
    """Imprime el nivel y dice si merece la pena seguir."""
    nivel, tope = rms(senal), pico(senal)
    print(f"\n  Nivel: RMS {nivel:.5f}   pico {tope:.4f}")

    if not hay_senal(senal):
        print(f"  {MAL} SILENCIO DIGITAL: el micro no entrega nada.")
        print("      Un micro vivo siempre tiene suelo de ruido; un pico de")
        print("      0.0000 exacto significa apagado, muteado por hardware o")
        print("      dispositivo equivocado. Comprueba, por este orden:")
        print("        · que el micro esté encendido y sin mutear")
        print("          (algunos cascos se mutean al subir la varilla)")
        print("        · que sea el dispositivo de la lista de arriba")
        print("        · el volumen de entrada en Configuración de Windows")
        return False

    if nivel < 0.005:
        print(f"  {AVISO} Nivel muy bajo. Se puede normalizar, pero con tan poca")
        print("      señal la consonante se pierde bajo el ruido y el")
        print("      reconocedor falla sin motivo aparente. Sube la ganancia.")
    else:
        print(f"  {BIEN} Nivel razonable.")
    return True


# ── Transcripción ────────────────────────────────────────────────────

def transcribir_vosk(senal) -> tuple[str, float]:
    import json

    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    t0 = time.monotonic()
    modelo = Model(str(CONFIG.vosk_model))
    carga = time.monotonic() - t0

    rec = KaldiRecognizer(modelo, SAMPLE_RATE)
    rec.SetWords(False)
    t0 = time.monotonic()
    rec.AcceptWaveform(a_int16(senal))
    texto = json.loads(rec.FinalResult() or "{}").get("text", "")
    print(f"      (modelo cargado en {carga:.1f}s)")
    return texto, time.monotonic() - t0


def transcribir_whisper(senal, tamano: str) -> tuple[str, float]:
    from .voice.cuda import hay_gpu, preparar_dlls

    preparar_dlls()
    from faster_whisper import WhisperModel

    device = "cuda" if hay_gpu() else "cpu"
    tipo = "int8_float16" if device == "cuda" else "int8"
    t0 = time.monotonic()
    modelo = WhisperModel(tamano, device=device, compute_type=tipo)
    carga = time.monotonic() - t0

    t0 = time.monotonic()
    segmentos, _ = modelo.transcribe(senal, language="es", beam_size=5)
    texto = " ".join(s.text for s in segmentos).strip()
    print(f"      ({tamano} en {device}, cargado en {carga:.1f}s)")
    return texto, time.monotonic() - t0


def comparar(senal, *, whisper: bool, tamano: str) -> None:
    _titulo("Qué entiende cada etapa")
    normalizada = normalizar(senal)

    resultados: list[tuple[str, str, float]] = []

    print(f"\n  Etapa 1 — Vosk ({CONFIG.vosk_model.name}), la que oye «NOVA»:")
    try:
        texto, dt = transcribir_vosk(normalizada)
        resultados.append(("Vosk", texto, dt))
    except Exception as exc:
        print(f"      {MAL} falló: {exc}")

    if whisper:
        print("\n  Etapa 2 — faster-whisper, la que entiende la orden:")
        try:
            texto, dt = transcribir_whisper(normalizada, tamano)
            resultados.append((f"whisper {tamano}", texto, dt))
        except Exception as exc:
            print(f"      {MAL} falló: {exc}")

    print()
    print("  " + "─" * 68)
    for nombre, texto, dt in resultados:
        print(f"  {nombre:<16} {dt:5.2f}s   «{texto}»")
    print("  " + "─" * 68)

    hay_dos = len(resultados) == 2 and all(texto for _, texto, _ in resultados)
    if hay_dos and resultados[0][1].strip().lower() != resultados[1][1].strip().lower():
        print("\n  Los dos entienden cosas distintas: eso es exactamente el")
        print("  motivo de la etapa 2. Whisper es el que manda para la orden.")


def guardar_wav(senal, ruta: Path) -> None:
    import wave

    ruta.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(a_int16(senal))
    print(f"\n  Grabación guardada en {ruta}")


# ── Punto de entrada ─────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m nova.doctor",
        description="Comprueba el micrófono y qué entiende cada reconocedor.",
    )
    p.add_argument("--listar", action="store_true", help="sólo listar micrófonos y salir")
    p.add_argument("--device", type=int, default=None, help="índice del micrófono a probar")
    p.add_argument("--segundos", type=float, default=5.0)
    p.add_argument("--modelo-whisper", default="small")
    p.add_argument("--sin-whisper", action="store_true")
    p.add_argument("--guardar", type=Path, default=None, help="guarda lo grabado en un WAV")
    args = p.parse_args(argv)

    print("NOVA — diagnóstico de audio")
    revisar_entorno()
    listar_dispositivos()
    if args.listar:
        return 0

    device = args.device if args.device is not None else CONFIG.mic_device
    _titulo(f"Prueba de grabación (dispositivo: {device if device is not None else 'predeterminado'})")
    try:
        senal = grabar(device, args.segundos)
    except Exception as exc:
        print(f"  {MAL} no pude grabar: {exc}")
        return 1

    if args.guardar:
        guardar_wav(senal, args.guardar)

    if not informar_nivel(senal):
        print("\n  Sin señal no tiene sentido transcribir. Arregla el micro y repite.")
        return 1

    comparar(senal, whisper=not args.sin_whisper, tamano=args.modelo_whisper)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
