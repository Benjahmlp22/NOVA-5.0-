"""Las voces buenas de Windows, las que SAPI no enseña.

Windows tiene dos juegos de voces y no son el mismo.  `pyttsx3` ve SAPI5,
que en español son dos y las dos de mujer (Helena y Sabina "Desktop", de
la época de Windows 7).  OneCore tiene cinco, incluidas **Pablo** y
**Raul**, que son de hombre.

A OneCore sólo se llega por WinRT.  Desde Python haría falta instalar
`winsdk`, y este proyecto no instala nada que no haga falta; desde
PowerShell ya está en el sistema.

Lo que hace viable la idea es que el proceso se queda VIVO: arrancar
`powershell.exe` cuesta 179 ms medidos, y NOVA sintetiza frase a frase
mientras el modelo sigue escribiendo.  Pagando el arranque una vez, cada
frase sale por 11 ms.

Si algo de esto falla — no hay PowerShell, WinRT no responde, el proceso
se muere — no pasa nada: `disponible` se queda en False y NOVA sigue con
SAPI, que siempre está.
"""

from __future__ import annotations

import base64
import logging
import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("nova.voice.onecore")

GUION = Path(__file__).with_name("onecore.ps1")

# Cuánto se espera a que arranque. Medido: 0.9-1.4 s cargando los tipos
# de WinRT. Con 10 s hay margen de sobra incluso con el antivirus
# mirando, y se paga una sola vez al abrir NOVA.
ARRANQUE_S = 10.0

# Cuánto se espera por frase. Medido: 11 ms en caliente. Tres segundos es
# absurdamente generoso; existe para no colgar a NOVA para siempre si el
# proceso se queda tonto.
FRASE_S = 3.0


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
        self._proc: subprocess.Popen | None = None
        self._respuestas: queue.Queue[str] = queue.Queue()
        self._lector: threading.Thread | None = None
        self._lock = threading.Lock()
        self.voces: list[Voz] = []
        self.error = ""

    @property
    def disponible(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Ciclo de vida ────────────────────────────────────────────────

    def start(self) -> bool:
        if self.disponible:
            return True
        if not GUION.exists():
            self.error = "falta onecore.ps1"
            return False
        try:
            self._proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(GUION)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                # Sin esto, abrir NOVA parpadea una consola negra.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.error = f"no pude arrancar powershell: {exc}"
            log.info("voces OneCore no disponibles: %s", self.error)
            return False

        self._lector = threading.Thread(target=self._leer, daemon=True)
        self._lector.start()

        if self._esperar(ARRANQUE_S) != "LISTO":
            self.error = "el sintetizador no arrancó"
            self.stop()
            return False

        self.voces = self._pedir_voces()
        if not self.voces:
            self.error = "no encontré voces OneCore"
            self.stop()
            return False
        log.info("voces OneCore listas: %s", ", ".join(v.nombre for v in self.voces))
        return True

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write("SALIR\n")
                proc.stdin.flush()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            proc.kill()

    # ── Uso ──────────────────────────────────────────────────────────

    def elegir(self, nombre: str) -> bool:
        return self._mandar(f"VOZ {nombre}") == "OK"

    def velocidad(self, factor: float) -> bool:
        """1.0 es la normal. Windows admite de 0.5 a 2.0 aquí."""
        return self._mandar(f"VELOCIDAD {factor:.2f}") == "OK"

    def sintetizar(self, texto: str, destino: Path) -> bool:
        """Escribe el WAV. False si algo falló, para poder caer a SAPI."""
        if not texto.strip():
            return False
        # Base64 y no el texto pelado: por stdin los acentos se corrompen
        # según la página de códigos de la consola, y "cañón" llegaba
        # convertido en otra cosa. Así no hay nada que interpretar.
        cifrado = base64.b64encode(texto.encode("utf-8")).decode("ascii")
        return self._mandar(f"DI {cifrado} {destino}", espera=FRASE_S) == "OK"

    # ── Internos ─────────────────────────────────────────────────────

    def _leer(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for linea in proc.stdout:
            self._respuestas.put(linea.rstrip("\r\n"))

    def _esperar(self, segundos: float) -> str:
        try:
            return self._respuestas.get(timeout=segundos)
        except queue.Empty:
            return ""

    def _mandar(self, orden: str, espera: float = FRASE_S) -> str:
        """Una orden y su respuesta. Serializado: hay un solo proceso."""
        with self._lock:
            if not self.disponible or self._proc is None or self._proc.stdin is None:
                return ""
            try:
                self._proc.stdin.write(orden + "\n")
                self._proc.stdin.flush()
            except OSError as exc:
                log.warning("el sintetizador se cayó: %s", exc)
                self._proc = None
                return ""
            respuesta = self._esperar(espera)
            if respuesta.startswith("ERROR"):
                log.warning("sintetizador: %s", respuesta)
            return respuesta

    def _pedir_voces(self) -> list[Voz]:
        with self._lock:
            if self._proc is None or self._proc.stdin is None:
                return []
            self._proc.stdin.write("VOCES\n")
            self._proc.stdin.flush()
            encontradas: list[Voz] = []
            while True:
                linea = self._esperar(ARRANQUE_S)
                if linea in ("OK", ""):
                    break
                partes = linea.split("\t")
                if len(partes) == 4 and partes[0] == "VOZ":
                    encontradas.append(Voz(partes[1], partes[2], partes[3]))
            return encontradas
