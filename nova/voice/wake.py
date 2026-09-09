"""Etapa 1: la puerta. Sólo decide si MERECE LA PENA escuchar mejor.

No decide si NOVA despierta — eso lo hace la etapa 2, que sí tiene
contexto de lenguaje.  Aquí sólo hace falta ser barato y no perderse
ninguna llamada; equivocarse de más está permitido y es incluso lo
buscado.

Por qué, medido el 26/08 sobre 14 frases (8 con "nova", 6 trampas del
tipo "no va a funcionar el mando", "la novia de mi hermano"):

    Vosk es-0.42, patrón de NOVA4     despierta 4/8   falsas 4/6
    Vosk small,   patrón de NOVA4     despierta 5/8   falsas 3/6
    Vosk small,   gramática ["nova"]  despierta 8/8   falsas 5/6

La gramática restringida obliga al decodificador a elegir entre "nova" y
"lo que sea", y eso sube el recall a 8/8 — que es lo único que esta etapa
no puede fallar, porque lo que no pase de aquí no lo escucha nadie.  Las
falsas cuestan una pasada de Whisper de medio segundo sin efecto visible:
el chime no suena hasta que la etapa 2 confirma.

El modelo grande no sirve aquí, y no por tamaño: **no admite gramática**
("Runtime graphs are not supported by this model").

Y el porqué de fondo: "nova" y "no va" son la MISMA secuencia de fonemas
en español. Ningún modelo acústico las separa. NOVA4 lo intentaba con un
regex que aceptaba "no va" como variante del nombre, y de ahí salían 4
falsas alarmas de cada 6 en conversación normal.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("nova.voice.wake")

# Palabras que se le ofrecen al decodificador ADEMÁS del nombre, para que
# tenga dónde poner lo que no es el nombre.
#
# Sale de un fallo real: un "nooo" alargado se colaba como "nova". Sin
# alternativas, la gramática sólo puede elegir entre el nombre y "no sé
# qué es", y un "noo" se le parece más al nombre que a la nada.
#
# Y no vale meter cualquier señuelo: probado el 26/08 sobre 14 frases de
# Benjahmlp22, con señuelos del tipo "no va" o "la novia" el recall se hundía
# de 8/8 a 4/8 — esos SÍ son los mismos fonemas que el nombre. "no" y
# "noo" se diferencian del nombre en una sílaba entera, que Vosk sí oye:
#
#     gramática                  despierta   falsas
#     ["nova"]                      8/8        5/6
#     ["nova","no"]                 8/8        5/6
#     ["nova","no","noo"]           8/8        4/6
SEÑUELOS = ("no", "noo", "nooo")


# Puntuación que se quita de los BORDES de cada palabra, nunca de dentro.
# Whisper puntúa —"NOVA, cierra Chrome."— y sin esto el nombre no casa
# con "nova" al comparar palabra a palabra. Pero quitarla también por
# dentro rompería "notas.txt", que es contenido de la orden.
_BORDES = ".,;:!?¡¿\"'«»()[]{}…-–—"


def normalizar_texto(texto: str) -> str:
    """Minúsculas, sin tildes, sin puntuación de borde, espacios colapsados."""
    out = []
    for ch in unicodedata.normalize("NFD", (texto or "").lower()):
        if unicodedata.category(ch) != "Mn":
            out.append(ch)
    palabras = ("".join(out)).split()
    limpias = [p.strip(_BORDES) for p in palabras]
    return " ".join(p for p in limpias if p)


class DetectorWake:
    """Vosk pequeño con gramática restringida al nombre."""

    def __init__(self, model_path: Path, wake_word: str = "nova") -> None:
        self.model_path = Path(model_path)
        self.wake_word = normalizar_texto(wake_word)
        self._modelo = None
        self._rec = None
        self.usa_gramatica = False
        self.error = ""
        # Reserva por si el modelo no admite gramática: el patrón de
        # siempre, SIN la variante "no va". Esa variante es la que
        # provocaba las falsas alarmas, y aquí ya no hace falta porque
        # quien decide de verdad es la etapa 2.
        self._patron = re.compile(rf"\b({re.escape(self.wake_word)}|noba)\b")

    # ── Ciclo de vida ────────────────────────────────────────────────

    def cargar(self) -> bool:
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            self._modelo = Model(str(self.model_path))
        except Exception as exc:  # noqa: BLE001
            self.error = f"no pude cargar el detector de wake word: {exc}"
            log.error(self.error)
            return False

        gramatica = json.dumps([self.wake_word, *SEÑUELOS, "[unk]"])
        try:
            self._rec = KaldiRecognizer(self._modelo, 16000, gramatica)
            self.usa_gramatica = True
        except Exception:  # noqa: BLE001
            log.warning(
                "%s no admite gramática restringida; voy con reconocimiento libre "
                "(menos recall)", self.model_path.name,
            )
            self._rec = KaldiRecognizer(self._modelo, 16000)
        self._rec.SetWords(False)
        return True

    def reiniciar(self) -> None:
        """Olvida lo oído hasta ahora.

        Se llama tras cada disparo: si no, el "nova" que acaba de sonar
        sigue en el búfer del reconocedor y vuelve a saltar con el
        siguiente bloque.
        """
        if self._rec is not None:
            self._rec.Reset()

    # ── Uso ──────────────────────────────────────────────────────────

    def escucha(self, bloque: bytes) -> bool:
        """¿Suena a que han dicho el nombre? Bloque de PCM int16 a 16 kHz."""
        if self._rec is None:
            return False
        try:
            if self._rec.AcceptWaveform(bloque):
                texto = json.loads(self._rec.Result() or "{}").get("text", "")
            else:
                # Se mira el PARCIAL, no el final: esperar a que la frase
                # termine añadía ~1 s antes de que NOVA reaccionara.
                texto = json.loads(self._rec.PartialResult() or "{}").get("partial", "")
        except (ValueError, TypeError):
            return False

        texto = normalizar_texto(texto)
        if not texto:
            return False
        if self.usa_gramatica:
            palabras = texto.split()
            # Con señuelos, que el decodificador elija "no" en vez del
            # nombre ES la respuesta: significa que no te llamaban.
            return self.wake_word in palabras
        return bool(self._patron.search(texto))


def detector_de_prueba(disparos: Callable[[bytes], bool]) -> DetectorWake:
    """Un detector que hace lo que le digas. Para tests, sin Vosk."""
    d = DetectorWake(Path("."), "nova")
    d.escucha = disparos  # type: ignore[method-assign]
    d.cargar = lambda: True  # type: ignore[method-assign]
    d.reiniciar = lambda: None  # type: ignore[method-assign]
    return d
