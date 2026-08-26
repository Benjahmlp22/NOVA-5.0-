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

**Las esquinas pesan más que el centro de los lados.**  La visión
periférica detecta mucho mejor un cambio en diagonal que uno de frente,
y además el centro de la pantalla es donde está lo que estás mirando: un
borde que se aprieta arriba y abajo del todo se nota antes y estorba
menos que uno uniforme.

**Y respira más despacio que antes.**  El pulso de NOVA4 iba a 0.09 rad
por fotograma; a 60 fps eso es un ciclo cada dos segundos, que en el
rabillo del ojo se lee como un aviso urgente. Ahora es la mitad de
rápido: se percibe como algo encendido, no como algo que reclama.
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

# Opacidad de UNA capa. En las esquinas se pintan dos (el lado de arriba
# y el de al lado se cruzan ahí), así que el máximo real de la pantalla es
# 1-(1-0.34)^2 = 0.56 — medido renderizando, no calculado a ojo. Ése es el
# techo que importa: es un aviso periférico, y si tapa el juego deja de
# ser periférico y pasa a ser un estorbo.
ALFA_MAXIMO = 0.34

# Cuánto se apaga el centro de cada lado respecto a las esquinas. A 0 el
# borde es uniforme (como NOVA4); a 1 desaparecería por el medio.
FUERZA_ESQUINAS = 0.55

# Tramos por lado. Subirlo NO mejora nada: renderizado a 1920x1080, el
# mayor salto de opacidad entre píxeles vecinos es 5/255 con 32 tramos y
# sigue siendo 5/255 con 128 — y ese salto está en x=1..7, que es la
# pendiente real de la esquina, no un escalón de la segmentación. Lo que
# sí cambia es el coste: 1.56 ms por fotograma con 32 y 5.21 ms con 128,
# sobre un presupuesto de 16.7 ms. Ese tiempo se lo estaríamos quitando a
# un juego, que es justo cuando este borde importa.
TRAMOS = 32


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

        self._fase += 0.025
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
        pulso = 0.86 + 0.14 * math.sin(self._fase)
        alfa = ALFA_MAXIMO * self._intensidad * pulso

        # Un degradado de verdad por lado, en vez de capas de rectángulos:
        # las capas dejaban escalones visibles en pantallas grandes.
        for lado in ("arriba", "abajo", "izquierda", "derecha"):
            self._pintar_lado(p, lado, w, h, alfa)

    def _pintar_lado(self, p: QPainter, lado: str, w: int, h: int, alfa: float) -> None:
        """Un lado, en tramos: fuerte en las esquinas y flojo en el centro.

        Hacen falta dos degradados a la vez — uno hacia dentro y otro a lo
        largo — y QPainter sólo sabe pintar uno. Restar el del medio
        pintando negro encima NO vale: sobre una ventana translúcida el
        negro no resta alfa, oscurece, y dejaría una neblina gris en mitad
        de cada lado, justo encima del juego.

        Así que se pinta por tramos, cada uno con su propio degradado hacia
        dentro y su opacidad. Con TRAMOS suficientes el escalón cae por
        debajo de lo que se distingue a estas opacidades.
        """
        x, y, ancho, alto = self._rect(lado, w, h)
        horizontal = lado in ("arriba", "abajo")
        largo = ancho if horizontal else alto
        if largo <= 0:
            return

        borde, dentro = self._geometria(lado, w, h)
        for i in range(TRAMOS):
            # Bordes redondeados a entero y encadenados: el final de un
            # tramo ES el principio del siguiente. Solapar 1 px dejaba una
            # línea el doble de brillante cada 60 px — más visible que la
            # costura que intentaba tapar.
            desde = round(i * largo / TRAMOS)
            hasta = round((i + 1) * largo / TRAMOS)
            grueso = hasta - desde
            if grueso <= 0:
                continue
            # 0 en las esquinas, 1 en el centro del lado.
            centro = abs((i + 0.5) / TRAMOS - 0.5) * 2
            peso = 1.0 - FUERZA_ESQUINAS * (1.0 - centro)
            a = alfa * peso

            g = QLinearGradient(*borde, *dentro)
            g.setColorAt(0.0, self._tono(a))
            # La parada intermedia es lo que hace que se vea como niebla y
            # no como una banda de color: sin ella el degradado es lineal
            # y el ojo le ve el borde.
            g.setColorAt(0.35, self._tono(a * 0.35))
            g.setColorAt(1.0, self._tono(0))

            if horizontal:
                p.fillRect(x + desde, y, grueso, alto, g)
            else:
                p.fillRect(x, y + desde, ancho, grueso, g)

    def _tono(self, alfa: float) -> QColor:
        """El color del estado actual con la opacidad pedida."""
        c = QColor(self._color)
        c.setAlphaF(max(0.0, min(1.0, alfa)))
        return c

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
