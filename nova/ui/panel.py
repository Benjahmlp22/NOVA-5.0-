"""El panel: lo que NOVA enseña cuando está desplegada.

Abajo a la derecha, arrastrable, y se colapsa al orbe de 62 px que ya
existía — el orbe no se tira, es el estado minimizado.

Cuatro cosas dentro, en este orden de importancia:

**La onda va con la amplitud REAL del audio.**  Nada de `sin()`
decorativo. Cuando NOVA habla, el nivel viene del audio que está sonando
(por eso `voice/speaker.py` sintetiza y reproduce él mismo: SAPI no
expone su búfer); cuando escucha, del micrófono. Una onda que finge
estar viva es peor que no tenerla: miente sobre si te está oyendo.

**Subtítulos.**  Lo que NOVA entendió y lo que respondió. Con el
reconocimiento de voz, ver la transcripción es la forma más rápida de
saber si te entendió mal — antes de que haga algo raro.

**Barra de actividad.**  Qué está haciendo ahora mismo y las últimas
cosas que hizo, con color por tipo. Abrir una app y cerrarla no pueden
parecer lo mismo de un vistazo.

**El estado, con color propio para cada cosa.**  Y hablar tiene el suyo,
bien distinto de escuchar: es lo que se pidió ver.

No roba el foco: `WA_ShowWithoutActivating` y `Qt.Tool`. Si robara el
foco, aparecer a mitad de partida te sacaría del juego, y NOVA es
justamente para no soltar lo que estás haciendo.
"""

from __future__ import annotations

from collections import deque

from PyQt5.QtCore import QPoint, QRect, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QMenu, QWidget

ANCHO = 340
RADIO = 14

# El panel CRECE con lo que haya que enseñar en vez de recortar el texto.
# Recortar era lo cómodo, pero un subtítulo cortado a media frase no sirve
# para lo único que sirven los subtítulos: comprobar si te entendió bien.
ALTO_MINIMO = 116
ALTO_MAXIMO = 420
LINEAS_MAXIMAS = 4

# Cabecera (40) + onda (52) + aire. Todo lo que viene debajo se apila.
ALTO_SUPERIOR = 102
ALTO_LINEA = 16
ALTO_ACCION = 15
HUECO_BLOQUE = 8
RELLENO_INFERIOR = 14

# Cuántas medidas de nivel caben en la onda. 72 a ~30 fps son unos 2.4 s
# de historia visible: suficiente para ver la forma de una frase.
MUESTRAS_ONDA = 72

ACCIONES_VISIBLES = 4

# Cuánto se queda una acción ya terminada antes de irse sola.
SEGUNDOS_ACCION_VISIBLE = 3.0

# Paleta. Fondo casi negro y un color por estado; el resto es gris.
_FONDO = QColor(11, 11, 13, 238)
_BORDE = QColor(38, 38, 44, 255)
_TEXTO = QColor(228, 228, 232)
_TENUE = QColor(128, 130, 138)
# Ámbar para "tienes algo esperando". No rojo: no es un error, es un
# recado — y el rojo ya significa que algo se ha roto.
_AVISO = QColor(240, 186, 74)

COLORES = {
    "preparando": QColor(96, 118, 148),    # azul apagado: va a estar, aún no está
    "dormida": QColor(150, 152, 158),      # gris
    "escucha": QColor(77, 163, 255),       # azul
    "pensando": QColor(240, 240, 242),     # blanco
    # Verde, no menta. El menta que había primero se quedaba a 40° de
    # tono del azul de escuchar, y a tamaño de barra pequeña los dos se
    # leían igual. Verde puro está a ~85°: "está hablando" se ve sin
    # leer nada, que era el requisito.
    "hablando": QColor(76, 217, 100),
    "error": QColor(232, 97, 60),          # ámbar rojizo
    "apagada": QColor(90, 92, 98),
}

ETIQUETAS = {
    "preparando": "preparando…",
    "dormida": "dormida",
    "escucha": "te escucho",
    "pensando": "pensando",
    "hablando": "hablando",
    "error": "error",
    "apagada": "sin voz",
}

# Color por tipo de acción. Abrir y cerrar no pueden parecer lo mismo.
_COLOR_ACCION = {
    "abrir": QColor(94, 214, 134),
    "cerrar": QColor(232, 97, 60),
    "buscar": QColor(77, 163, 255),
    "archivo": QColor(178, 142, 255),
    "memoria": QColor(240, 198, 78),
    "sistema": QColor(150, 152, 158),
}


def color_de_accion(tipo: str) -> QColor:
    return _COLOR_ACCION.get(tipo, _TENUE)


class Panel(QWidget):
    """Ventana sin marco, siempre encima, que no roba el foco."""

    minimizar = pyqtSignal()

    def __init__(self, on_quit=None, on_toggle_mute=None) -> None:  # noqa: ANN001
        super().__init__()
        self._estado = "preparando"
        self._niveles: deque[float] = deque([0.0] * MUESTRAS_ONDA, maxlen=MUESTRAS_ONDA)
        self._nivel_actual = 0.0
        self._acciones: deque[tuple[str, str]] = deque(maxlen=ACCIONES_VISIBLES)
        self._dicho = ""
        self._respondido = ""
        # Cuántos recordatorios han vencido y esperan a que le hables.
        # Se enseñan parpadeando en vez de interrumpiendo: un aviso que
        # te corta a mitad de partida es peor que no tenerlo.
        self._pendientes = 0
        self._fase_aviso = 0.0
        self._arrastre: QPoint | None = None
        self._on_quit = on_quit
        self._on_toggle_mute = on_toggle_mute

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # fuera de la barra de tareas y del Alt+Tab
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Obligatorio: sin esto, aparecer a mitad de partida te saca del
        # juego, que es justo lo contrario de para lo que sirve NOVA.
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(ANCHO)
        self.setFixedHeight(ALTO_MINIMO)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps

        self._borrado = QTimer(self)
        self._borrado.setSingleShot(True)
        self._borrado.timeout.connect(self._limpiar_acciones)

    # ── Entradas ─────────────────────────────────────────────────────

    def set_estado(self, estado: str) -> None:
        if estado != self._estado:
            self._estado = estado
            self.update()

    def set_nivel(self, nivel: float) -> None:
        """Nivel real (RMS 0-1) del audio que suena o entra ahora mismo."""
        self._nivel_actual = max(0.0, min(1.0, float(nivel)))

    def set_dicho(self, texto: str) -> None:
        self._dicho = texto or ""
        self._ajustar_alto()
        self.update()

    def set_respondido(self, texto: str) -> None:
        self._respondido = texto or ""
        self._ajustar_alto()
        self.update()

    def set_pendientes(self, cuantos: int) -> None:
        if cuantos != self._pendientes:
            self._pendientes = max(0, int(cuantos))
            self.update()

    def añadir_accion(self, tipo: str, detalle: str) -> None:
        self._borrado.stop()
        self._acciones.append((tipo, detalle))
        self._ajustar_alto()
        self.update()

    def terminar_acciones(self) -> None:
        """El turno acabó: las acciones se van solas en un momento.

        No al instante: se quedan lo justo para poder leer qué acaba de
        hacer. Pero tampoco para siempre — una lista de cosas ya hechas
        deja de informar y sólo ocupa sitio.
        """
        if self._acciones:
            self._borrado.start(int(SEGUNDOS_ACCION_VISIBLE * 1000))

    def _limpiar_acciones(self) -> None:
        self._acciones.clear()
        self._ajustar_alto()
        self.update()

    # ── Colocación ───────────────────────────────────────────────────

    def colocar(self, esquina: str = "bottom-right", margen: int = 16) -> None:
        pantalla = self.screen().availableGeometry()
        x = pantalla.right() - ANCHO - margen
        y = pantalla.bottom() - self.height() - margen
        if "left" in esquina:
            x = pantalla.left() + margen
        if "top" in esquina:
            y = pantalla.top() + margen
        self.move(x, y)

    # ── Pintado ──────────────────────────────────────────────────────

    def _tick(self) -> None:
        # La onda avanza SIEMPRE, aunque el nivel sea cero: así se ve que
        # el panel está vivo y que el silencio es silencio de verdad, no
        # una imagen congelada.
        self._niveles.append(self._nivel_actual)
        if self._pendientes:
            self._fase_aviso += 0.10
        # Caída suave: sin esto, al acabar una frase la onda se corta en
        # seco y parece que la app se ha colgado.
        self._nivel_actual *= 0.82
        self.update()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = COLORES.get(self._estado, COLORES["apagada"])

        self._pintar_fondo(p)
        self._pintar_cabecera(p, color)
        self._pintar_onda(p, color)
        self._pintar_acciones(p, self._pintar_texto(p))

    def _pintar_fondo(self, p: QPainter) -> None:
        camino = QPainterPath()
        camino.addRoundedRect(0.5, 0.5, ANCHO - 1, self.height() - 1, RADIO, RADIO)
        p.fillPath(camino, _FONDO)
        p.setPen(QPen(_BORDE, 1))
        p.drawPath(camino)

    def _pintar_cabecera(self, p: QPainter, color: QColor) -> None:
        # Punto de estado
        p.setPen(Qt.NoPen)
        halo = QColor(color)
        halo.setAlpha(60)
        p.setBrush(halo)
        p.drawEllipse(14, 15, 14, 14)
        p.setBrush(color)
        p.drawEllipse(17, 18, 8, 8)

        fuente = QFont()
        fuente.setPointSize(9)
        fuente.setBold(True)
        p.setFont(fuente)
        p.setPen(color)
        p.drawText(QRect(34, 12, 200, 20), Qt.AlignVCenter | Qt.AlignLeft,
                   ETIQUETAS.get(self._estado, self._estado))

        # Aviso pendiente: un punto que respira al lado del minimizar.
        # No dice QUÉ es —eso te lo cuenta cuando le hables— sólo que hay
        # algo esperándote.
        if self._pendientes:
            import math

            pulso = 0.45 + 0.55 * abs(math.sin(self._fase_aviso))
            aviso = QColor(_AVISO)
            aviso.setAlpha(int(255 * pulso))
            p.setPen(Qt.NoPen)
            p.setBrush(aviso)
            p.drawEllipse(ANCHO - 54, 18, 9, 9)
            if self._pendientes > 1:
                fuente = QFont()
                fuente.setPointSize(7)
                fuente.setBold(True)
                p.setFont(fuente)
                p.setPen(_FONDO)
                p.drawText(QRect(ANCHO - 54, 18, 9, 9), Qt.AlignCenter,
                           str(min(9, self._pendientes)))

        # Botón de minimizar: un guion, sin adornos.
        p.setPen(QPen(_TENUE, 1.6))
        p.drawLine(ANCHO - 30, 22, ANCHO - 18, 22)

    def _pintar_onda(self, p: QPainter, color: QColor) -> None:
        arriba, alto = 40, 52
        centro = arriba + alto / 2
        n = len(self._niveles)
        ancho_barra = (ANCHO - 28) / n

        p.setPen(Qt.NoPen)
        for i, nivel in enumerate(self._niveles):
            # Raíz cuadrada: el RMS de la voz vive en la parte baja del
            # rango y en lineal casi no se movería la onda.
            altura = max(2.0, min(alto, (nivel ** 0.5) * alto * 1.6))
            x = 14 + i * ancho_barra
            c = QColor(color)
            # Las más viejas se apagan: da sensación de avance y separa
            # lo que suena ahora de lo que sonó hace dos segundos.
            c.setAlpha(60 + int(195 * (i / max(1, n - 1))))
            p.setBrush(c)
            p.drawRoundedRect(
                QRect(int(x), int(centro - altura / 2),
                      max(1, int(ancho_barra - 1.4)), int(altura)),
                1, 1,
            )

    # ── Subtítulos: bajan, no se cortan ──────────────────────────────

    def _lineas(self, metricas, texto: str, ancho: int) -> list[str]:
        """Parte el texto en líneas que quepan, cortando por palabras.

        Antes se recortaba con puntos suspensivos, y un subtítulo cortado
        a media frase no sirve para lo único que sirven los subtítulos:
        comprobar si NOVA te entendió bien.
        """
        lineas: list[str] = []
        actual = ""
        for palabra in texto.split():
            prueba = f"{actual} {palabra}".strip()
            if actual and metricas.horizontalAdvance(prueba) > ancho:
                lineas.append(actual)
                actual = palabra
                if len(lineas) == LINEAS_MAXIMAS:
                    break
            else:
                actual = prueba
        if actual and len(lineas) < LINEAS_MAXIMAS:
            lineas.append(actual)
        # Sólo se recorta si ni con todas las líneas cabe: en ese caso el
        # final es lo prescindible, no el principio.
        if len(lineas) == LINEAS_MAXIMAS and actual and lineas[-1] != actual:
            lineas[-1] = metricas.elidedText(lineas[-1] + "…", Qt.ElideRight, ancho)
        return lineas

    def _bloques_de_texto(self, metricas) -> list[tuple[str, list[str], QColor]]:
        ancho = ANCHO - 66
        bloques = []
        for etiqueta, texto, color in (
            ("tú", self._dicho, _TENUE),
            ("NOVA", self._respondido, _TEXTO),
        ):
            if texto:
                bloques.append((etiqueta, self._lineas(metricas, texto, ancho), color))
        return bloques

    def _alto_necesario(self) -> int:
        metricas = QFontMetrics(self._fuente_texto())
        alto = ALTO_SUPERIOR
        for _etiqueta, lineas, _color in self._bloques_de_texto(metricas):
            alto += len(lineas) * ALTO_LINEA + HUECO_BLOQUE
        if self._acciones:
            alto += HUECO_BLOQUE + len(self._acciones) * ALTO_ACCION
        return max(ALTO_MINIMO, min(ALTO_MAXIMO, alto + RELLENO_INFERIOR))

    @staticmethod
    def _fuente_texto() -> QFont:
        fuente = QFont()
        fuente.setPointSize(8)
        return fuente

    def _ajustar_alto(self) -> None:
        """Crece o encoge, manteniendo quieto el borde de abajo.

        Si creciera hacia abajo, el panel se saldría de la pantalla en
        cuanto el subtítulo ocupara dos líneas; y si el borde inferior se
        moviera, el bloque "saltaría" cada vez que NOVA responde.
        """
        alto = self._alto_necesario()
        if alto == self.height():
            return
        abajo = self.geometry().bottom()
        self.setFixedHeight(alto)
        self.move(self.x(), abajo - alto)

    def _pintar_texto(self, p: QPainter) -> int:
        fuente = self._fuente_texto()
        p.setFont(fuente)
        metricas = p.fontMetrics()

        y = ALTO_SUPERIOR
        for etiqueta, lineas, color in self._bloques_de_texto(metricas):
            p.setPen(_TENUE)
            p.drawText(QRect(14, y, 36, ALTO_LINEA),
                       Qt.AlignLeft | Qt.AlignVCenter, etiqueta)
            p.setPen(color)
            for linea in lineas:
                p.drawText(QRect(52, y, ANCHO - 66, ALTO_LINEA),
                           Qt.AlignLeft | Qt.AlignVCenter, linea)
                y += ALTO_LINEA
            y += HUECO_BLOQUE
        return y

    def _pintar_acciones(self, p: QPainter, y: int) -> None:
        if not self._acciones:
            return
        metricas = p.fontMetrics()
        y += HUECO_BLOQUE
        for i, (tipo, detalle) in enumerate(self._acciones):
            reciente = i == len(self._acciones) - 1
            c = QColor(color_de_accion(tipo))
            if not reciente:
                c.setAlpha(120)
            p.setPen(c)
            p.drawText(QRect(14, y, 12, ALTO_ACCION), Qt.AlignLeft | Qt.AlignVCenter, "▸")
            p.setPen(c if reciente else QColor(_TENUE.red(), _TENUE.green(),
                                               _TENUE.blue(), 150))
            p.drawText(
                QRect(28, y, ANCHO - 42, ALTO_ACCION),
                Qt.AlignLeft | Qt.AlignVCenter,
                metricas.elidedText(detalle, Qt.ElideRight, ANCHO - 46),
            )
            y += ALTO_ACCION

    # ── Interacción ──────────────────────────────────────────────────

    def mousePressEvent(self, e) -> None:  # noqa: ANN001, N802
        if e.button() == Qt.LeftButton:
            if e.pos().x() > ANCHO - 40 and e.pos().y() < 36:
                self.minimizar.emit()
                e.accept()
                return
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
        menu.addAction("Minimizar al orbe", self.minimizar.emit)
        if self._on_toggle_mute:
            menu.addAction("Silenciar / reactivar voz", self._on_toggle_mute)
        menu.addSeparator()
        menu.addAction("Salir de NOVA", self._on_quit or (lambda: None))
        menu.exec_(e.globalPos())
