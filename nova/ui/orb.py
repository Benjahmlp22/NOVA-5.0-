"""El orbe: la única UI permanente de NOVA.

Un círculo pequeño, siempre encima, arrastrable.  Sin panel, sin
dashboard, sin ventana que gestionar — se abre y ya está.

Estados (lo que ves de un vistazo):
    preparando  azul apagado, medio      → cargando el modelo de voz
    dormida   gris, respirando lento     → escuchando el wake word
    escucha   azul, ondas rápidas        → te está oyendo
    pensando  blanco, ondas medias       → procesando
    hablando  blanco, ondas al ritmo     → respondiendo
    apagada   gris tenue, quieto         → sin voz disponible
"""

from __future__ import annotations

import math

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QMenu, QWidget

from .panel import _AVISO
from .panel import COLORES as _PALETA

TAMANO = 62
_BARRAS = 3

# La paleta la manda el panel: dos tablas de color para los mismos
# estados se desincronizan en cuanto alguien toca una.
_COLORES = _PALETA

_VELOCIDAD = {
    "preparando": 0.12,
    "dormida": 0.045,
    "escucha": 0.30,
    "pensando": 0.18,
    "hablando": 0.26,
    "apagada": 0.0,
}


class Orb(QWidget):
    expandir = pyqtSignal()

    def __init__(self, on_quit=None, on_toggle_mute=None,  # noqa: ANN001
                 on_toggle_sordo=None) -> None:  # noqa: ANN001
        super().__init__()
        self._estado = "apagada"
        self._fase = 0.0
        self._nivel = 0.0
        self._pendientes = 0
        self._arrastre: QPoint | None = None
        self._on_quit = on_quit
        self._on_toggle_mute = on_toggle_mute
        self._on_toggle_sordo = on_toggle_sordo
        self._voz_silenciada = False
        self._sordo = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # fuera de la barra de tareas y del Alt+Tab
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(TAMANO, TAMANO)
        self.setToolTip('NOVA — di "NOVA". Clic para desplegar, arrastra para mover.')

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps: fluido sin gastar CPU

    # ── Estado ───────────────────────────────────────────────────────

    def set_estado(self, estado: str) -> None:
        if estado != self._estado:
            self._estado = estado
            self.update()

    def set_conmutadores(self, *, mudo: bool, sordo: bool) -> None:
        """Minimizada al orbe también hay que poder ver que está sorda:
        si no, parece que se ha roto."""
        if (mudo, sordo) != (self._voz_silenciada, self._sordo):
            self._voz_silenciada, self._sordo = mudo, sordo
            self.update()

    def set_pendientes(self, cuantos: int) -> None:
        """Colapsada también tiene que avisar: si no, el recado se pierde."""
        if cuantos != self._pendientes:
            self._pendientes = max(0, int(cuantos))
            self.update()

    def set_nivel(self, nivel: float) -> None:
        """Nivel real del audio, igual que en el panel.

        Colapsado se ve menos, pero tiene que seguir diciendo la verdad:
        una barra que se mueve sola cuando no hay sonido es exactamente
        lo que había que quitar.
        """
        self._nivel = max(0.0, min(1.0, float(nivel)))

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
        elif self._pendientes:
            # Dormida no anima nada, pero el aviso tiene que respirar.
            self._fase += 0.06
        self._nivel *= 0.82
        if vel or self._nivel > 0.001 or self._pendientes:
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
            elif self._estado in ("escucha", "hablando"):
                # Amplitud REAL. La raíz cuadrada porque el RMS de la voz
                # vive en la parte baja del rango y en lineal la barra
                # casi no se movería.
                alto = 6 + 13 * min(1.0, (self._nivel ** 0.5) * 1.6)
            else:
                # "pensando" no tiene audio que enseñar, así que aquí sí
                # toca una animación: es un latido, no una mentira.
                alto = 8 + 11 * abs(math.sin(self._fase + i * 0.55))
            x = x0 + i * (ancho + hueco)
            p.drawRoundedRect(int(x), int(centro - alto / 2), ancho, int(alto), 2, 2)

        self._pintar_aviso(p)
        self._pintar_apagados(p)

    # ── Interacción ──────────────────────────────────────────────────

    def _pintar_apagados(self, p: QPainter) -> None:
        """Una raya sobre el orbe si está muda o sorda.

        Minimizada al orbe no hay etiquetas ni botones, así que sin esto
        una NOVA sorda parece exactamente una NOVA rota: enseña que está
        despierta y no reacciona a nada.
        """
        if not (self._voz_silenciada or self._sordo):
            return
        centro = TAMANO / 2
        radio = centro - 5
        d = radio * 0.62
        p.setBrush(Qt.NoBrush)
        # Dos rayas si está las dos cosas; una sola no distinguiría
        # "no te oigo" de "no te hablo".
        p.setPen(QPen(_AVISO, 2.2))
        if self._sordo:
            p.drawLine(int(centro - d), int(centro + d), int(centro + d), int(centro - d))
        if self._voz_silenciada:
            p.drawLine(int(centro - d), int(centro - d), int(centro + d), int(centro + d))

    def _pintar_aviso(self, p: QPainter) -> None:
        if not self._pendientes:
            return
        pulso = 0.45 + 0.55 * abs(math.sin(self._fase * 1.6))
        aviso = QColor(_AVISO)
        aviso.setAlpha(int(255 * pulso))
        p.setPen(Qt.NoPen)
        p.setBrush(aviso)
        p.drawEllipse(TAMANO - 20, 6, 11, 11)

    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.button() == Qt.LeftButton:
            self._arrastre = e.globalPos() - self.frameGeometry().topLeft()
            self._movido = False
            e.accept()

    def mouseMoveEvent(self, e) -> None:  # noqa: ANN001, N802
        if self._arrastre is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._arrastre)
            self._movido = True
            e.accept()

    def mouseReleaseEvent(self, e) -> None:  # noqa: ANN001, N802
        # Un clic que no arrastró es un clic: despliega el panel. Si se
        # expandiera también al soltar tras mover, arrastrar el orbe
        # abriría el panel cada vez.
        if self._arrastre is not None and not getattr(self, "_movido", False):
            self.expandir.emit()
        self._arrastre = None
        self._movido = False

    def contextMenuEvent(self, e) -> None:  # noqa: ANN001, N802
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:#141416;color:#f0f0f2;border:1px solid #2a2a2e;padding:4px}"
            "QMenu::item{padding:6px 22px;border-radius:4px}"
            "QMenu::item:selected{background:#26262b}"
        )
        if self._on_toggle_mute:
            menu.addAction(
                "Dejar de hablar" if not self._voz_silenciada else "Volver a hablar",
                self._on_toggle_mute)
        if self._on_toggle_sordo:
            menu.addAction(
                "Dejar de escuchar" if not self._sordo else "Volver a escuchar",
                self._on_toggle_sordo)
        menu.addSeparator()
        menu.addAction("Salir de NOVA", self._on_quit or (lambda: None))
        menu.exec_(e.globalPos())
