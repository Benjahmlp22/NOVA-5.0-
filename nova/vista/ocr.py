"""Leer el texto que hay en pantalla, con el OCR que trae Windows.

No hace falta descargar nada ni instalar nada: Windows lleva un motor de
OCR desde Windows 10, con los idiomas que tengas puestos (aquí, español
de España y de México).  Es local y gratis, como todo lo demás.

Sirve para lo que no se puede copiar: un error en un diálogo, un menú de
un juego, un PDF escaneado, un vídeo pausado.  Cuando el texto SÍ se
puede copiar, `portapapeles.leer` es mejor y más barato.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from ..winrt import ARRANQUE_S, PuenteWinRT

log = logging.getLogger("nova.vista.ocr")

GUION = Path(__file__).with_name("ocr.ps1")

# Cuánto se espera por imagen. Medido en una pantalla 1080p entera:
# 0.2-0.5 s. Diez segundos es para una captura de varios monitores en
# una máquina cargada, no para el caso normal.
LECTURA_S = 10.0


@dataclass(frozen=True)
class Idioma:
    etiqueta: str      # "es-ES"
    nombre: str        # "Español (España)"


class LectorDePantalla:
    def __init__(self) -> None:
        self._puente = PuenteWinRT(GUION, "ocr")
        self.idiomas: list[Idioma] = []

    @property
    def disponible(self) -> bool:
        return self._puente.disponible

    @property
    def error(self) -> str:
        return self._puente.error

    def start(self) -> bool:
        if not self._puente.start():
            return False
        self.idiomas = [
            Idioma(p[1], p[2])
            for linea in self._puente.mandar_varias("IDIOMAS")
            if len(p := linea.split("\t")) == 3 and p[0] == "IDIOMA"
        ]
        if not self.idiomas:
            log.info("OCR sin idiomas instalados")
            self._puente.stop()
            return False
        log.info("OCR listo: %s", ", ".join(i.etiqueta for i in self.idiomas))
        return True

    def stop(self) -> None:
        self._puente.stop()

    def leer(self, imagen: Path) -> str:
        """El texto de una imagen. Cadena vacía si no hay o si falla.

        Vacío y fallo se tratan igual a propósito: para quien llama,
        "no pude leerlo" y "no había texto" acaban en la misma frase, y
        el motivo de verdad ya está en el log.
        """
        respuesta = self._puente.mandar(f"LEE {imagen}", espera=LECTURA_S)
        if not respuesta.startswith("TEXTO "):
            return ""
        # El texto vuelve en base64: lleva saltos de línea (y el protocolo
        # es de una línea por respuesta) y acentos que se corromperían.
        try:
            return base64.b64decode(respuesta[6:]).decode("utf-8").strip()
        except (ValueError, UnicodeDecodeError):
            log.warning("respuesta de OCR ilegible", exc_info=True)
            return ""


__all__ = ["ARRANQUE_S", "Idioma", "LectorDePantalla"]
