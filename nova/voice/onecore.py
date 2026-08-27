"""Las voces buenas de Windows, las que SAPI no enseña.

Windows tiene dos juegos de voces y no son el mismo.  `pyttsx3` ve SAPI5,
que en español son dos y las dos de mujer (Helena y Sabina "Desktop", de
la época de Windows 7).  OneCore tiene cinco, incluidas **Pablo** y
**Raul**, que son de hombre.

A OneCore sólo se llega por WinRT.  Desde Python haría falta instalar
`winsdk`, y este proyecto no instala nada que no haga falta; desde
PowerShell ya está en el sistema.

Lo que hace viable la idea es que el proceso se queda VIVO (ver
`nova/winrt.py`): arrancar `powershell.exe` cuesta 179 ms medidos, y
NOVA sintetiza frase a frase mientras el modelo sigue escribiendo.
Pagando el arranque una vez, cada frase sale por 11 ms.

Si algo de esto falla — no hay PowerShell, WinRT no responde, el proceso
se muere — no pasa nada: `disponible` se queda en False y NOVA sigue con
SAPI, que siempre está.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from pathlib import Path

from ..winrt import PuenteWinRT

log = logging.getLogger("nova.voice.onecore")

GUION = Path(__file__).with_name("onecore.ps1")

# Cuánto se espera por frase.
#
# Once milisegundos con la CPU tranquila... y 1909 ms en el peor caso con
# los doce hilos al tope, medido. Con un juego encima, los tres segundos
# de antes se agotaban de verdad, y cada plazo agotado partía el audio.
#
# Seis segundos dan tres veces margen sobre el peor caso medido. Esperar
# de más ya no cuesta nada: con las peticiones numeradas, un plazo
# agotado sólo hace caer a SAPI, no descoloca la cola.
FRASE_S = 6.0


@dataclass(frozen=True)
class Voz:
    nombre: str
    idioma: str
    genero: str      # "Male" / "Female", tal cual lo dice Windows

    @property
    def es_hombre(self) -> bool:
        return self.genero.lower().startswith("m")

    @property
    def es_espanol(self) -> bool:
        return self.idioma.lower().startswith("es")


class SintetizadorOneCore:
    def __init__(self) -> None:
        self._puente = PuenteWinRT(GUION, "voces")
        self.voces: list[Voz] = []

    @property
    def disponible(self) -> bool:
        return self._puente.disponible

    @property
    def error(self) -> str:
        return self._puente.error

    # ── Ciclo de vida ────────────────────────────────────────────────

    def start(self) -> bool:
        if not self._puente.start():
            return False
        self.voces = [
            Voz(p[1], p[2], p[3])
            for linea in self._puente.mandar_varias("VOCES")
            if len(p := linea.split("	")) == 4 and p[0] == "VOZ"
        ]
        if not self.voces:
            self._puente.error = "no encontré voces OneCore"
            self._puente.stop()
            return False
        log.info("voces OneCore listas: %s", ", ".join(v.nombre for v in self.voces))
        return True

    def stop(self) -> None:
        self._puente.stop()

    # ── Uso ──────────────────────────────────────────────────────────

    def elegir(self, nombre: str) -> bool:
        return self._puente.mandar(f"VOZ {nombre}") == "OK"

    def velocidad(self, factor: float) -> bool:
        """1.0 es la normal. Windows admite de 0.5 a 2.0 aquí."""
        return self._puente.mandar(f"VELOCIDAD {factor:.2f}") == "OK"

    def sintetizar(self, texto: str, destino: Path) -> bool:
        """Escribe el WAV. False si algo falló, para poder caer a SAPI."""
        if not texto.strip():
            return False
        # El archivo anterior se quita ANTES. Así, si algo saliera mal
        # sin decirlo, lo que hay abajo falla en vez de reproducir la
        # frase de antes: es lo que se oía como "S", "EST" y trozos.
        try:
            destino.unlink(missing_ok=True)
        except OSError:
            pass

        # Base64 y no el texto pelado: por stdin los acentos se corrompen
        # según la página de códigos de la consola, y "cañón" llegaba
        # convertido en otra cosa. Así no hay nada que interpretar.
        cifrado = base64.b64encode(texto.encode("utf-8")).decode("ascii")
        if self._puente.mandar(f"DI {cifrado} {destino}", espera=FRASE_S) != "OK":
            return False

        # Y se comprueba que está de verdad. Un WAV son 44 bytes de
        # cabecera: menos que eso no es audio, es un archivo a medias.
        try:
            if destino.stat().st_size > 44:
                return True
        except OSError:
            pass
        log.warning("me dijeron que sí pero %s no está escrito", destino.name)
        return False
