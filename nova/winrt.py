"""Un proceso de PowerShell que se queda vivo, para hablar con WinRT.

Windows tiene cosas buenas que sólo se ofrecen por WinRT: las voces
OneCore y el motor de OCR, por ahora.  Desde Python haría falta instalar
`winsdk`; desde PowerShell ya están.

Todo el módulo existe por un número: **arrancar `powershell.exe` cuesta
179 ms medidos**.  Un proceso por petición mataría cualquier ventaja,
porque estas cosas se usan frase a frase o pantallazo a pantallazo.  Con
el proceso vivo leyendo de stdin, el arranque se paga una vez.

El protocolo es de líneas y **cada petición lleva su número**.  Eso no
es adorno: sin él, un solo agotamiento de plazo desincroniza la cola
para siempre.  La respuesta que llegó tarde se queda dentro, la
siguiente petición la lee como suya, y a partir de ahí todo va corrido
un puesto.  En las voces eso significaba dar por buena una frase que
todavía no se había escrito: NOVA leía el WAV **anterior a medio
escribir** y decía "S", "EST" y trozos sueltos.

Los textos viajan en base64 en los dos sentidos, porque por
stdin/stdout los acentos se corrompen según la página de códigos de la
consola y «cañón» llega convertido en otra cosa.
"""

from __future__ import annotations

import itertools
import logging
import queue
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("nova.winrt")

# Cuánto se espera a que arranque. Medido: 0.4-1.4 s cargando los tipos
# de WinRT. Diez segundos deja margen incluso con el antivirus mirando, y
# se paga una sola vez.
ARRANQUE_S = 10.0


class PuenteWinRT:
    """Un guion de PowerShell vivo al que se le mandan órdenes."""

    def __init__(self, guion: Path, nombre: str = "") -> None:
        self.guion = guion
        self.nombre = nombre or guion.stem
        self.error = ""
        self._proc: subprocess.Popen | None = None
        self._respuestas: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._numeros = itertools.count(1)

    @property
    def disponible(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Ciclo de vida ────────────────────────────────────────────────

    def start(self) -> bool:
        """Arranca y espera al 'LISTO'. False si no se puede, sin excepción.

        Nunca lanza: quien lo use tiene que poder seguir sin esto. Las
        voces caen a SAPI, el OCR simplemente no está.
        """
        if self.disponible:
            return True
        if not self.guion.exists():
            self.error = f"falta {self.guion.name}"
            return False
        try:
            self._proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", str(self.guion)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                # Sin esto, cada arranque parpadea una consola negra.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.error = f"no pude arrancar powershell: {exc}"
            log.info("%s no disponible: %s", self.nombre, self.error)
            return False

        threading.Thread(target=self._leer, daemon=True,
                         name=f"winrt-{self.nombre}").start()

        if self.esperar(ARRANQUE_S) != "LISTO":
            self.error = f"{self.nombre} no arrancó"
            self.stop()
            return False
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

    # ── Órdenes ──────────────────────────────────────────────────────

    def esperar(self, segundos: float) -> str:
        try:
            return self._respuestas.get(timeout=segundos)
        except queue.Empty:
            return ""

    def mandar(self, orden: str, espera: float = 3.0) -> str:
        """Una orden y SU respuesta. Serializado: hay un solo proceso."""
        with self._lock:
            lineas = self._conversar(orden, espera, una_sola=True)
        return lineas[0] if lineas else ""

    def mandar_varias(self, orden: str, espera: float = ARRANQUE_S) -> list[str]:
        """Una orden que responde varias líneas antes del OK."""
        with self._lock:
            return self._conversar(orden, espera, una_sola=False)

    # ── Internos ─────────────────────────────────────────────────────

    def _escribir(self, orden: str) -> bool:
        if not self.disponible or self._proc is None or self._proc.stdin is None:
            return False
        try:
            self._proc.stdin.write(orden + "\n")
            self._proc.stdin.flush()
            return True
        except OSError as exc:
            log.warning("%s se cayó: %s", self.nombre, exc)
            self._proc = None
            return False

    def _conversar(self, orden: str, espera: float, *, una_sola: bool) -> list[str]:
        """Manda una orden numerada y recoge SÓLO lo que responde a ella.

        Las respuestas con otro número son de una petición que se dio por
        perdida y llegó tarde. Se tiran aquí: si se quedaran en la cola,
        la siguiente petición las leería como suyas y a partir de ese
        momento todo iría corrido un puesto, dando por hechas cosas que
        aún no habían pasado.
        """
        numero = next(self._numeros)
        if not self._escribir(f"{numero} {orden}"):
            return []

        marca = f"{numero} "
        recogidas: list[str] = []
        while True:
            linea = self.esperar(espera)
            if not linea:
                log.warning("%s no contestó a la petición %d", self.nombre, numero)
                return []
            if not linea.startswith(marca):
                log.info("%s: descarto una respuesta atrasada (%r)",
                         self.nombre, linea[:40])
                continue
            cuerpo = linea[len(marca):]
            if cuerpo.startswith("ERROR"):
                log.warning("%s: %s", self.nombre, cuerpo)
                return []
            if cuerpo == "OK":
                return recogidas if not una_sola else ["OK"]
            if una_sola:
                return [cuerpo]
            recogidas.append(cuerpo)

    def _leer(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for linea in proc.stdout:
            self._respuestas.put(linea.rstrip("\r\n"))
