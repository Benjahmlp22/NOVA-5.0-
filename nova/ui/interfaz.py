"""Une panel, orbe y glow, y recuerda dónde los dejaste.

El panel y el orbe no son dos interfaces: son el mismo sitio en dos
tamaños.  Colapsar cambia el widget pero no la esquina, y al volver a
abrir NOVA aparece donde la dejaste — NOVA4 la ponía siempre en la misma
esquina por muchas veces que la movieras.

Anclaje por la esquina inferior derecha y no por la superior izquierda:
el panel y el orbe tienen tamaños muy distintos, y anclando arriba a la
izquierda el bloque "saltaría" al colapsarlo. Anclando por abajo a la
derecha, el borde que el usuario tiene en el ojo se queda quieto.
"""

from __future__ import annotations

import json
import logging

from PyQt5.QtCore import QObject

from ..config import CONFIG
from .actividad import describir
from .glow import GlowBorder
from .orb import Orb
from .panel import Panel

log = logging.getLogger("nova.ui")


class Interfaz(QObject):
    """Todo lo que NOVA enseña. La app habla con esto, no con los widgets."""

    def __init__(self, *, on_quit=None, on_toggle_mute=None) -> None:  # noqa: ANN001
        super().__init__()
        self.panel = Panel(on_quit=on_quit, on_toggle_mute=on_toggle_mute)
        self.orb = Orb(on_quit=on_quit, on_toggle_mute=on_toggle_mute)
        self.glow = GlowBorder()

        self._colapsada = False
        self._estado = "preparando"

        self.panel.minimizar.connect(self.colapsar)
        self.orb.expandir.connect(self.expandir)

    # ── Ciclo de vida ────────────────────────────────────────────────

    def mostrar(self) -> None:
        guardado = self._leer_estado_guardado()
        self._colapsada = bool(guardado.get("colapsada", False))

        self.panel.colocar(CONFIG.orb_corner)
        self.orb.colocar(CONFIG.orb_corner)
        if "esquina" in guardado:
            self._mover_a(*guardado["esquina"])

        (self.orb if self._colapsada else self.panel).show()

    def cerrar(self) -> None:
        self._guardar_estado()
        self.panel.hide()
        self.orb.hide()
        self.glow.apagar()

    # ── Colapsar / expandir ──────────────────────────────────────────

    def colapsar(self) -> None:
        if self._colapsada:
            return
        esquina = self._esquina_actual(self.panel)
        self._colapsada = True
        self.panel.hide()
        self.orb.set_estado(self._estado)
        self.orb.show()
        self._mover_a(*esquina)
        self._guardar_estado()

    def expandir(self) -> None:
        if not self._colapsada:
            return
        esquina = self._esquina_actual(self.orb)
        self._colapsada = False
        self.orb.hide()
        self.panel.set_estado(self._estado)
        self.panel.show()
        self._mover_a(*esquina)
        self._guardar_estado()

    # ── Lo que la app le cuenta ──────────────────────────────────────

    def set_estado(self, estado: str) -> None:
        self._estado = estado
        self.panel.set_estado(estado)
        self.orb.set_estado(estado)
        # El borde también: a pantalla completa es lo ÚNICO que se ve, y
        # en NOVA4 se quedaba azul mientras ella hablaba o pensaba.
        self.glow.set_estado(estado)

    def set_nivel(self, nivel: float) -> None:
        self.panel.set_nivel(nivel)
        self.orb.set_nivel(nivel)

    def set_dicho(self, texto: str) -> None:
        self.panel.set_dicho(texto)

    def set_respondido(self, texto: str) -> None:
        self.panel.set_respondido(texto)

    def accion(self, herramienta: str, dato: str = "") -> None:
        tipo, texto = describir(herramienta, dato)
        self.panel.añadir_accion(tipo, texto)
        log.debug("actividad: %s", texto)

    def set_pendientes(self, cuantos: int) -> None:
        """Recordatorios vencidos esperando a que le hables."""
        self.panel.set_pendientes(cuantos)
        self.orb.set_pendientes(cuantos)

    def turno_terminado(self) -> None:
        """Ya no está haciendo nada: las acciones se retiran solas."""
        self.panel.terminar_acciones()

    # ── Posición ─────────────────────────────────────────────────────

    @staticmethod
    def _esquina_actual(widget) -> tuple[int, int]:  # noqa: ANN001
        geo = widget.frameGeometry()
        return geo.right(), geo.bottom()

    def _mover_a(self, derecha: int, abajo: int) -> None:
        actual = self.orb if self._colapsada else self.panel
        actual.move(derecha - actual.width(), abajo - actual.height())

    def _fichero(self):
        return CONFIG.data_dir / "ui.json"

    def _leer_estado_guardado(self) -> dict:
        try:
            return json.loads(self._fichero().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _guardar_estado(self) -> None:
        widget = self.orb if self._colapsada else self.panel
        datos = {
            "colapsada": self._colapsada,
            "esquina": list(self._esquina_actual(widget)),
        }
        try:
            self._fichero().parent.mkdir(parents=True, exist_ok=True)
            self._fichero().write_text(json.dumps(datos), encoding="utf-8")
        except OSError:
            log.debug("no pude guardar la posición de la interfaz", exc_info=True)
