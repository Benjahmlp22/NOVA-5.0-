"""CLIP: convertir imágenes y frases a números comparables entre sí.

Es lo que permite buscar "una imagen de League of Legends" sin que el
archivo se llame así.  CLIP mete texto e imágenes en el MISMO espacio,
así que buscar es medir ángulos entre vectores.

**Por qué esto y no un modelo de visión describiendo cada foto.**  Hay
16.699 imágenes en este PC. Un modelo de visión tarda de 1 a 3 segundos
por imagen: entre 5 y 14 horas. CLIP tarda unos 50 ms: unos 15 minutos,
una sola vez, y después buscar es instantáneo porque ya son números.

Corre con `onnxruntime`, que ya estaba instalado, en CPU.  No hace falta
torch (2.5 GB) para ejecutar un modelo que ya está entrenado.

Los dos codificadores (607 MB) se bajan una vez de un repositorio
público y después esto no toca la red nunca más: sigue siendo 100% local
y gratis, como todo lo demás.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
from pathlib import Path

import numpy as np

from ..config import ROOT

log = logging.getLogger("nova.vista.clip")

MODELOS = ROOT / "models" / "clip"

# Lo que CLIP ViT-B/32 espera. No son ajustables: si no coinciden con lo
# que se usó al entrenar, el modelo sigue funcionando pero devuelve
# vectores que no significan nada.
LADO = 224
MEDIA = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
DESVIACION = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
CONTEXTO = 77          # tokens que acepta el codificador de texto

# Cuántos hilos usar. Dejar cuatro núcleos libres no es un sacrificio:
# medido en este PC (12 hilos), por imagen —
#
#   6 hilos  33 ms      10 hilos  37 ms
#   8 hilos  31 ms      12 hilos  51 ms
#
# — con ocho va MÁS rápido que con doce y además NOVA sigue pudiendo
# hablar mientras indexa. Con la CPU al tope, sintetizar una frase pasa
# de 11 ms a 1909 ms en el peor caso, y ahí es donde empezaban los
# plazos agotados que le partían el audio.
HILOS = max(2, (os.cpu_count() or 4) - 4)


def hay_modelos() -> bool:
    return all((MODELOS / n).exists()
               for n in ("vision.onnx", "text.onnx", "vocab.json", "merges.txt"))


# ── Tokenizador ──────────────────────────────────────────────────────
#
# CLIP usa BPE sobre bytes. Se implementa aquí en vez de traerse
# `transformers` (que arrastra torch) porque son cuarenta líneas y el
# vocabulario ya viene con el modelo.

@functools.lru_cache(maxsize=1)
def _bpe():
    vocab = json.loads((MODELOS / "vocab.json").read_text(encoding="utf-8"))
    lineas = (MODELOS / "merges.txt").read_text(encoding="utf-8").split("\n")
    # La primera línea es la versión, no una fusión.
    fusiones = [tuple(ln.split()) for ln in lineas[1:] if len(ln.split()) == 2]
    rangos = {par: i for i, par in enumerate(fusiones)}
    return vocab, rangos


def _pares(palabra: tuple[str, ...]) -> set[tuple[str, str]]:
    return {(palabra[i], palabra[i + 1]) for i in range(len(palabra) - 1)}


@functools.lru_cache(maxsize=4096)
def _fusionar(token: str) -> tuple[str, ...]:
    """BPE sobre una palabra suelta. Se cachea: las consultas repiten mucho."""
    _, rangos = _bpe()
    palabra = (*token[:-1], token[-1] + "</w>")
    while True:
        pares = _pares(palabra)
        if not pares:
            break
        mejor = min(pares, key=lambda p: rangos.get(p, float("inf")))
        if mejor not in rangos:
            break
        primero, segundo = mejor
        nueva: list[str] = []
        i = 0
        while i < len(palabra):
            if (i < len(palabra) - 1 and palabra[i] == primero
                    and palabra[i + 1] == segundo):
                nueva.append(primero + segundo)
                i += 2
            else:
                nueva.append(palabra[i])
                i += 1
        palabra = tuple(nueva)
        if len(palabra) == 1:
            break
    return palabra


_PALABRAS = re.compile(
    r"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+"
    .replace(r"\p{L}", "^\\W\\d_").replace(r"\p{N}", "\\d"),
    re.IGNORECASE,
)


def tokenizar(texto: str) -> np.ndarray:
    """La frase, como los 77 enteros que espera el codificador."""
    vocab, _ = _bpe()
    inicio = vocab["<|startoftext|>"]
    fin = vocab["<|endoftext|>"]

    ids = [inicio]
    for palabra in _PALABRAS.findall(texto.lower().strip()):
        for trozo in _fusionar(palabra):
            if (id_ := vocab.get(trozo)) is not None:
                ids.append(id_)
    ids.append(fin)

    # Se corta dejando el token de fin: sin él el modelo no sabe dónde
    # acaba la frase y el vector sale mal.
    ids = ids[:CONTEXTO - 1] + [fin] if len(ids) > CONTEXTO else ids
    salida = np.zeros((1, CONTEXTO), dtype=np.int64)
    salida[0, :len(ids)] = ids
    return salida


# ── Preparar la imagen ───────────────────────────────────────────────

def preparar(ruta: Path) -> np.ndarray | None:
    """Una imagen como el tensor que espera CLIP. None si no se puede leer."""
    import warnings

    from PIL import Image

    try:
        with warnings.catch_warnings(), Image.open(ruta) as img:
            # PIL avisa por cada PNG con paleta y transparencia. Con
            # 16.000 imágenes eso es ruido puro: la conversión a RGB que
            # viene justo después es exactamente lo que pide el aviso.
            warnings.simplefilter("ignore")
            img = img.convert("RGB")
            # Escalar por el lado corto y recortar al centro, que es lo
            # que se hizo al entrenar. Deformarla a 224x224 cambia las
            # proporciones y empeora el resultado.
            ancho, alto = img.size
            escala = LADO / min(ancho, alto)
            nuevo = (max(LADO, round(ancho * escala)), max(LADO, round(alto * escala)))
            img = img.resize(nuevo, Image.BICUBIC)
            izq = (img.width - LADO) // 2
            arr = (img.crop((izq, (img.height - LADO) // 2, izq + LADO,
                             (img.height - LADO) // 2 + LADO)))
            datos = np.asarray(arr, dtype=np.float32) / 255.0
    except Exception:  # noqa: BLE001
        # Un PNG corrupto o un .jpg que en realidad es un HTML de error
        # no puede tumbar un indexado de 16.000 archivos.
        log.debug("no pude abrir %s", ruta, exc_info=True)
        return None

    datos = (datos - MEDIA) / DESVIACION
    return datos.transpose(2, 0, 1)          # alto,ancho,canal -> canal,alto,ancho


# ── El modelo ────────────────────────────────────────────────────────

def _normalizar(v: np.ndarray) -> np.ndarray:
    """A módulo 1, para que comparar sea un producto escalar."""
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-8)


class CodificadorCLIP:
    """Los dos codificadores. Se cargan por separado y sólo si hacen falta."""

    def __init__(self) -> None:
        self._vision = None
        self._texto = None

    @staticmethod
    def _sesion(archivo: str):

        import onnxruntime as ort

        opciones = ort.SessionOptions()
        # Silencio: ORT avisa de optimizaciones en cada carga y eso
        # acabaría en el log de NOVA sin aportar nada.
        opciones.log_severity_level = 3
        # Los hilos, a mano. El "auto" de onnxruntime se queda a menos de
        # la mitad de lo que da la máquina: 72 ms por imagen en auto
        # contra 31 poniéndolos a mano. Ver HILOS arriba.
        opciones.intra_op_num_threads = HILOS
        return ort.InferenceSession(str(MODELOS / archivo), opciones,
                                    providers=["CPUExecutionProvider"])

    def de_imagenes(self, tensores: list[np.ndarray]) -> np.ndarray:
        if self._vision is None:
            self._vision = self._sesion("vision.onnx")
        lote = np.stack(tensores).astype(np.float32)
        entrada = self._vision.get_inputs()[0].name
        salida = self._vision.run(None, {entrada: lote})[0]
        return _normalizar(salida)

    def de_texto(self, frase: str) -> np.ndarray:
        if self._texto is None:
            self._texto = self._sesion("text.onnx")
        ids = tokenizar(frase)
        entradas = {e.name: ids for e in self._texto.get_inputs()
                    if "mask" not in e.name.lower()}
        for e in self._texto.get_inputs():
            if "mask" in e.name.lower():
                entradas[e.name] = (ids != 0).astype(np.int64)
        salida = self._texto.run(None, entradas)[0]
        return _normalizar(salida)[0]
