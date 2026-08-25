"""Borde azul brillante: NOVA te está escuchando.

Cubre la pantalla entera pero es transparente al ratón (click-through),
así que puedes seguir trabajando/jugando con él encendido.  Es el aviso
periférico que se ve sin mirar a ninguna ventana.
"""

from __future__ import annotations

import math

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QWidget

# De fuera hacia dentro: grosor y opacidad base. Varias capas finas dan
# un degradado real; una sola gruesa se ve como un marco plano y feo.
_CAPAS = [(22, 0.05), (17, 0.10), (13, 0.17), (9, 0.28), (6, 0.42), (3, 0.62), (2, 0.85)]
_AZUL = (77, 163, 255)


class GlowBorder(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._fase = 0.0
        self._activo = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput  # no roba clics: clave
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def encender(self) -> None:
        if self._activo:
            return
        self._activo = True
        self._fase = 0.0
        self.setGeometry(self.screen().geometry())
        self.show()
        self._timer.start(40)

    def apagar(self) -> None:
        if not self._activo:
            return
        self._activo = False
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._fase += 0.09
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._activo:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r, g, b = _AZUL

        # Latido lento: un borde fijo se vuelve invisible a los 2 s;
        # uno que respira mantiene el "estoy escuchando" presente.
        pulso = 0.78 + 0.22 * math.sin(self._fase)

        for grosor, alpha in _CAPAS:
            p.setPen(QPen(QColor(r, g, b, int(255 * alpha * pulso)), grosor))
            d = grosor / 2
            p.drawRect(int(d), int(d), int(w - grosor), int(h - grosor))
