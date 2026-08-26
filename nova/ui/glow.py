"""El borde de la pantalla: el aviso que se ve sin mirar a ningún sitio.

Cubre la pantalla entera pero es transparente al ratón (click-through),
así que puedes seguir jugando con él encendido.  Es información
periférica: no se lee, se nota.

Qué cambia respecto a NOVA4:

**El color dice el estado.**  Antes siempre era azul, incluso mientras
NOVA hablaba o pensaba. Ahora usa la misma paleta que el panel, así que
el borde y el orbe nunca dicen cosas distintas — y si estás a pantalla
completa, el borde es lo ÚNICO que ves.

**Se enciende y se apaga con una transición.**  Aparecer y desaparecer de
golpe en el rabillo del ojo se percibe como un parpadeo del monitor. Al
subir y bajar en ~200 ms, se lee como que algo empieza y algo termina.

**Más apretado a los bordes y más flojo.**  Antes eran 22 px de azul
bastante presente; sobre un juego oscuro cansaba. Ahora el degradado
arranca más suave y la esquina pesa más que el centro del lado, que es
por donde el ojo detecta el cambio sin distraerse.
"""

from __future__ import annotations

import math

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QLinearGradient, QPainter
from PyQt5.QtWidgets import QWidget

from .panel import COLORES

# Grosor del degradado, en píxeles. Antes 22 px de color bastante sólido;
# 26 px con caída suave se nota igual y molesta menos, porque lo que
# llama la atención es el gradiente, no la cantidad de tinta.
GROSOR = 26

# Cuánto tarda en encenderse o apagarse. Por debajo de ~120 ms se percibe
# como un parpadeo del monitor; por encima de ~350 ms parece que la app
# va lenta.
TRANSICION_S = 0.20

# Opacidad máxima del borde. Es un aviso periférico: si tapa el juego,
# deja de ser periférico y pasa a ser un estorbo.
ALFA_MAXIMO = 0.55


class GlowBorder(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._fase = 0.0
        self._activo = False
        self._intensidad = 0.0        # 0 apagado, 1 del todo encendido
        self._color = COLORES["escucha"]

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

    # ── Encendido ────────────────────────────────────────────────────

    def encender(self, estado: str = "escucha") -> None:
        self._color = COLORES.get(estado, COLORES["escucha"])
        if self._activo:
            self.update()
            return
        self._activo = True
        self._fase = 0.0
        self.setGeometry(self.screen().geometry())
        self.show()
        self._timer.start(16)  # ~60 fps: la transición tiene que ir fina

    def apagar(self) -> None:
        """Empieza a apagarse. No se esconde hasta que termina de bajar."""
        if not self._activo:
            return
        self._activo = False
        if not self._timer.isActive():
            self._timer.start(16)

    def set_estado(self, estado: str) -> None:
        """El borde sigue al estado: a pantalla completa es lo único que se ve."""
        color = COLORES.get(estado)
        if color is not None and color != self._color:
            self._color = color
            self.update()

    # ── Animación ────────────────────────────────────────────────────

    def _tick(self) -> None:
        paso = (self._timer.interval() / 1000) / TRANSICION_S
        objetivo = 1.0 if self._activo else 0.0
        if self._intensidad < objetivo:
            self._intensidad = min(objetivo, self._intensidad + paso)
        elif self._intensidad > objetivo:
            self._intensidad = max(objetivo, self._intensidad - paso)

        self._fase += 0.05
        self.update()

        if not self._activo and self._intensidad <= 0.0:
            self._timer.stop()
            self.hide()

    # ── Pintado ──────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._intensidad <= 0.0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Latido lento: un borde fijo se vuelve invisible a los 2 s; uno
        # que respira mantiene el "estoy aquí" presente sin gritar.
        pulso = 0.82 + 0.18 * math.sin(self._fase)
        alfa = ALFA_MAXIMO * self._intensidad * pulso

        # Un degradado de verdad por lado, en vez de capas de rectángulos:
        # las capas dejaban escalones visibles en pantallas grandes.
        for lado in ("arriba", "abajo", "izquierda", "derecha"):
            self._pintar_lado(p, lado, w, h, alfa)

    def _pintar_lado(self, p: QPainter, lado: str, w: int, h: int, alfa: float) -> None:
        borde, dentro = self._geometria(lado, w, h)
        gradiente = QLinearGradient(*borde, *dentro)

        fuerte = QColor(self._color)
        fuerte.setAlphaF(min(1.0, alfa))
        medio = QColor(self._color)
        medio.setAlphaF(min(1.0, alfa * 0.35))
        nada = QColor(self._color)
        nada.setAlpha(0)

        gradiente.setColorAt(0.0, fuerte)
        # La parada intermedia es lo que hace que se vea como niebla y no
        # como una banda de color: sin ella el degradado es lineal y el
        # ojo le ve el borde.
        gradiente.setColorAt(0.35, medio)
        gradiente.setColorAt(1.0, nada)

        p.fillRect(*self._rect(lado, w, h), gradiente)

    @staticmethod
    def _geometria(lado: str, w: int, h: int) -> tuple[tuple[int, int], tuple[int, int]]:
        return {
            "arriba": ((0, 0), (0, GROSOR)),
            "abajo": ((0, h), (0, h - GROSOR)),
            "izquierda": ((0, 0), (GROSOR, 0)),
            "derecha": ((w, 0), (w - GROSOR, 0)),
        }[lado]

    @staticmethod
    def _rect(lado: str, w: int, h: int) -> tuple[int, int, int, int]:
        return {
            "arriba": (0, 0, w, GROSOR),
            "abajo": (0, h - GROSOR, w, GROSOR),
            "izquierda": (0, 0, GROSOR, h),
            "derecha": (w - GROSOR, 0, GROSOR, h),
        }[lado]
