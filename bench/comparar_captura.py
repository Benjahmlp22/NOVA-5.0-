"""La misma frase por cada camino de captura, para elegir con datos.

    python bench/comparar_captura.py
    python bench/comparar_captura.py --frase "abre discord y pon musica"

En Windows el mismo micrófono se abre por varias APIs y no suenan igual.
Aquí se dice la misma frase una vez por camino y se comparan las tres
cosas que importan: cómo suena (quedan los WAV para escucharlos), cuánta
energía cae en la banda de voz, y qué entiende cada reconocedor.

Existe porque la alternativa es lo que ya pasó una vez: grabar veinte
frases por un camino, descubrir al final que el audio no servía, y
repetirlo. Un minuto aquí ahorra eso.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

from nova.voice.audio import (  # noqa: E402
    SAMPLE_RATE,
    a_int16,
    fraccion_en_banda_de_voz,
    hay_senal,
    normalizar,
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

DESTINO = RAIZ / "audio" / "comparacion"


def _guardar(senal, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(ruta), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(a_int16(senal))


def _nombre_fichero(camino: Camino) -> str:
    limpio = "".join(c if c.isalnum() else "_" for c in camino.etiqueta)
    return f"{limpio[:60]}.wav"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compara caminos de captura del micrófono.")
    p.add_argument("--frase", default="abre discord y busca el precio de la cuatro mil setenta")
    p.add_argument("--micro", default="", help="filtra por nombre, p.ej. G435")
    p.add_argument("--sin-whisper", action="store_true")
    args = p.parse_args(argv)

    caminos = caminos_para(args.micro)
    if not caminos:
        print("✗ No encuentro ningún micrófono que se pueda abrir.")
        return 1

    print(f"Caminos que se pueden abrir ({len(caminos)}):")
    for c in caminos:
        print(f"  · {c.etiqueta}  @ {c.tasa} Hz")
    print(f"\nVas a decir la MISMA frase una vez por camino:\n\n    «{args.frase}»\n")
    print("Mismo sitio, misma distancia, mismo tono en las tres. Si cambias")
    print("cómo hablas, la comparación mide eso en vez del micrófono.\n")

    resultados = []
    for c in caminos:
        while True:
            input(f"[{c.etiqueta}]  Enter, y di la frase cuando salga «habla ahora» > ")
            try:
                ruido = medir_ruido(c)
                umbral = umbral_de_voz(ruido)
                print(f"    ruido {ruido:.5f} · umbral {umbral:.5f} · HABLA AHORA")
                g = grabar_hasta_silencio(c, umbral)
            except Exception as exc:  # noqa: BLE001
                print(f"    ✗ falló: {exc}\n")
                break

            if not hay_senal(g.senal):
                print("    ✗ silencio digital: este camino no entrega nada.\n")
                break

            # Un fichero de la duración máxima sin voz detectada es el
            # fallo silencioso que ya nos costó tres grabaciones mudas.
            if not g.voz_detectada:
                print(f"    ✗ no detecté voz en {g.segundos:.1f}s "
                      f"(bloque más alto {g.pico_bloque:.5f} < umbral {umbral:.5f})")
                if g.pico_bloque < umbral / 3:
                    print("      Casi no entró señal: ¿hablaste antes de «HABLA AHORA»,")
                    print("      o este camino no está cogiendo el micro?")
                else:
                    print("      Estuvo cerca: habla algo más alto o más cerca del micro.")
                if input("      ¿Repetir este camino? [S/n] > ").strip().lower() in ("", "s", "si", "sí"):
                    continue
                break

            ruta = DESTINO / _nombre_fichero(c)
            _guardar(g.senal, ruta)
            banda = fraccion_en_banda_de_voz(tramo_hablado(g.senal))
            print(f"    ✓ {g.segundos:.1f}s   RMS {rms(g.senal):.4f}   "
                  f"pico {pico(g.senal):.3f}   voz {banda * 100:.0f}%   → {ruta.name}")
            if g.avisos:
                print(f"    ! el driver avisó de {', '.join(g.avisos)} "
                      "(muestras perdidas = cortes)")
            print()
            resultados.append((c, g.senal, banda, ruta))
            break

    if not resultados:
        print("No se grabó nada.")
        return 1

    # ── Qué entiende cada reconocedor ────────────────────────────────
    print("Transcribiendo...\n")
    from nova.config import CONFIG

    transcripciones: dict[str, dict[str, str]] = {}

    import json

    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    vosk_modelo = Model(str(CONFIG.vosk_model))
    for c, senal, _, _ in resultados:
        rec = KaldiRecognizer(vosk_modelo, SAMPLE_RATE)
        rec.SetWords(False)
        rec.AcceptWaveform(a_int16(normalizar(senal)))
        transcripciones.setdefault(c.etiqueta, {})["Vosk"] = json.loads(
            rec.FinalResult() or "{}"
        ).get("text", "")

    if not args.sin_whisper:
        from nova.voice.cuda import hay_gpu, preparar_dlls

        preparar_dlls()
        from faster_whisper import WhisperModel

        device = "cuda" if hay_gpu() else "cpu"
        w = WhisperModel("small", device=device,
                         compute_type="int8_float16" if device == "cuda" else "int8")
        for c, senal, _, _ in resultados:
            segmentos, _info = w.transcribe(normalizar(senal), language="es", beam_size=5)
            transcripciones.setdefault(c.etiqueta, {})["whisper"] = " ".join(
                s.text for s in segmentos
            ).strip()

    print("=" * 78)
    print(f"esperado: «{args.frase}»")
    print("=" * 78)
    for c, _senal, banda, ruta in resultados:
        print(f"\n{c.etiqueta}   ·   voz {banda * 100:.0f}%   ·   {ruta.name}")
        for motor, texto in transcripciones.get(c.etiqueta, {}).items():
            print(f"    {motor:<9} «{texto}»")

    print(f"\nLos WAV están en {DESTINO}")
    print("Escúchalos: el que suene mejor al oído casi siempre es el que mide mejor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
