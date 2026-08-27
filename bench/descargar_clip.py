"""Baja los modelos de visión, una vez.

CLIP es lo que permite buscar "una imagen de League of Legends" sin que
el archivo se llame así. Son 607 MB de un repositorio público; después
de esto NOVA no vuelve a tocar la red para verlos: sigue siendo 100%
local y gratis.

    .venv\Scripts\python.exe bench\descargar_clip.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

DESTINO = Path(__file__).resolve().parent.parent / "models" / "clip"

# Los dos codificadores (imagen y texto) y el vocabulario del
# tokenizador. Se guardan con nombres cortos porque los dos se llaman
# igual en origen.
ARCHIVOS = [
    ("Qdrant/clip-ViT-B-32-vision", "model.onnx", "vision.onnx"),
    ("Qdrant/clip-ViT-B-32-vision", "preprocessor_config.json", "preprocessor.json"),
    ("Qdrant/clip-ViT-B-32-text", "model.onnx", "text.onnx"),
    ("Qdrant/clip-ViT-B-32-text", "vocab.json", "vocab.json"),
    ("Qdrant/clip-ViT-B-32-text", "merges.txt", "merges.txt"),
]


def main() -> int:
    DESTINO.mkdir(parents=True, exist_ok=True)
    for repo, remoto, local in ARCHIVOS:
        salida = DESTINO / local
        if salida.exists():
            print(f"ya estaba: {local} ({salida.stat().st_size / 1e6:.0f} MB)")
            continue
        url = f"https://huggingface.co/{repo}/resolve/main/{remoto}"
        t0 = time.perf_counter()
        try:
            # A un archivo temporal primero: un corte de red a mitad no
            # puede dejar un .onnx a medias que luego falle al cargar sin
            # que se entienda por qué.
            temporal = salida.with_suffix(salida.suffix + ".parcial")
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                with temporal.open("wb") as f:
                    for trozo in r.iter_bytes(1 << 20):
                        f.write(trozo)
            temporal.replace(salida)
        except Exception as exc:  # noqa: BLE001
            print(f"fallo bajando {local}: {exc}")
            return 1
        print(f"{local:20s} {salida.stat().st_size / 1e6:7.1f} MB "
              f"en {time.perf_counter() - t0:5.1f} s")

    total = sum(f.stat().st_size for f in DESTINO.iterdir()) / 1e6
    print(f"\nlisto: {total:.0f} MB en {DESTINO}")
    print("Ahora dile a NOVA que repase tus imágenes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
