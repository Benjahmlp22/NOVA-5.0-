"""El orbe: la única UI permanente de NOVA.

Un círculo pequeño, siempre encima, arrastrable.  Sin panel, sin
dashboard, sin ventana que gestionar — se abre y ya está.

Estados (lo que ves de un vistazo):
    dormida   gris, respirando lento     → escuchando el wake word
    escucha   azul, ondas rápidas        → te está oyendo
    pensando  blanco, ondas medias       → procesando
    hablando  blanco, ondas al ritmo     → respondiendo
    apagada   gris tenue, quieto         → sin voz disponible
"""

from __future__ import annotations

import math

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QMenu, QWidget

TAMANO = 62
_BARRAS = 3

_COLORES = {
    "dormida": QColor(150, 152, 158),
    "escucha": QColor(77, 163, 255),
    "pensando": QColor(240, 240, 242),
    "hablando": QColor(255, 255, 255),
    "apagada": QColor(90, 92, 98),
}
_VELOCIDAD = {
    "dormida": 0.045,
    "escucha": 0.30,
    "pensando": 0.18,
    "hablando": 0.26,
    "apagada": 0.0,
}


class Orb(QWidget):
    def __init__(self, on_quit=None, on_toggle_mute=None) -> None:  # noqa: ANN001
        super().__init__()
        self._estado = "apagada"
        self._fase = 0.0
        self._arrastre: QPoint | None = None
        self._on_quit = on_quit
        self._on_toggle_mute = on_toggle_mute

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # fuera de la barra de tareas y del Alt+Tab
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(TAMANO, TAMANO)
        self.setToolTip('NOVA — di "NOVA". Arrastra para mover, clic derecho para opciones.')

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps: fluido sin gastar CPU

    # ── Estado ───────────────────────────────────────────────────────

    def set_estado(self, estado: str) -> None:
        if estado != self._estado:
            self._estado = estado
            self.update()

    def colocar(self, esquina: str = "bottom-left", margen: int = 16) -> None:
        pantalla = self.screen().availableGeometry()
        x = pantalla.left() + margen
        y = pantalla.bottom() - TAMANO - margen
        if "right" in esquina:
            x = pantalla.right() - TAMANO - margen
        if "top" in esquina:
            y = pantalla.top() + margen
        self.move(x, y)

    # ── Pintado ──────────────────────────────────────────────────────

    def _tick(self) -> None:
        vel = _VELOCIDAD.get(self._estado, 0.0)
        if vel:
            self._fase += vel
            self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = _COLORES.get(self._estado, _COLORES["apagada"])

        centro = TAMANO / 2
        radio = centro - 5

        # Halo suave cuando está activa: se ve desde el rabillo del ojo.
        if self._estado in ("escucha", "hablando"):
            halo = QColor(color)
            halo.setAlpha(45)
            p.setPen(Qt.NoPen)
            p.setBrush(halo)
            p.drawEllipse(int(centro - radio - 3), int(centro - radio - 3),
                          int((radio + 3) * 2), int((radio + 3) * 2))

        # Cuerpo oscuro (el tema es negro; el orbe no puede ser un foco).
        p.setBrush(QColor(12, 12, 14, 245))
        p.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 110), 1.4))
        p.drawEllipse(int(centro - radio), int(centro - radio), int(radio * 2), int(radio * 2))

        # Barras tipo ecualizador: la forma más legible de "te escucho".
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        ancho, hueco = 4, 4
        total = _BARRAS * ancho + (_BARRAS - 1) * hueco
        x0 = centro - total / 2
        for i in range(_BARRAS):
            if self._estado in ("dormida", "apagada"):
                alto = 6 + (2 * math.sin(self._fase + i * 0.9) if self._estado == "dormida" else 0)
            else:
                alto = 8 + 11 * abs(math.sin(self._fase + i * 0.55))
            x = x0 + i * (ancho + hueco)
            p.drawRoundedRect(int(x), int(centro - alto / 2), ancho, int(alto), 2, 2)

    # ── Interacción ──────────────────────────────────────────────────

    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.button() == Qt.LeftButton:
            self._arrastre = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        if self._arrastre is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._arrastre)
            e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001, N802
        self._arrastre = None

    def contextMenuEvent(self, e) -> None:  # noqa: ANN001, N802
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:#141416;color:#f0f0f2;border:1px solid #2a2a2e;padding:4px}"
            "QMenu::item{padding:6px 22px;border-radius:4px}"
            "QMenu::item:selected{background:#26262b}"
        )
        if self._on_toggle_mute:
            menu.addAction("Silenciar / reactivar voz", self._on_toggle_mute)
        menu.addSeparator()
        menu.addAction("Salir de NOVA", self._on_quit or (lambda: None))
        menu.exec_(e.globalPos())
