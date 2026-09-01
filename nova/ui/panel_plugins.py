"""El panel de plugins: la ventana que se abre diciendo «abre los plugins».

Lo único que hace esta ventana que no es cosmético: **enseñar lo que un
plugin puede hacerte ANTES de que lo actives**.

Por eso la disposición es la que es. Los permisos delicados van en
ámbar, el código se puede leer entero sin salir de aquí, y los avisos de
la revisión salen al lado del botón de activar — no escondidos en una
pestaña que nadie abre.

Un plugin de sólo datos (personalidad, voz, frases) se activa con un
clic y ya: no puede hacer daño. Uno con código pide una vuelta más.
"""

from __future__ import annotations

import logging

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..plugins import DELICADOS, Gestor, Plugin
from .panel import _AVISO, _BORDE, _FONDO, _TENUE, _TEXTO, COLORES

log = logging.getLogger("nova.ui.plugins")

_HOJA = f"""
QDialog {{ background: {_FONDO.name()}; }}
QLabel  {{ color: {_TEXTO.name()}; }}
QListWidget {{
    background: #101014;
    border: 1px solid {_BORDE.name()};
    color: {_TEXTO.name()};
    outline: none;
    padding: 4px;
}}
QListWidget::item {{ padding: 9px 8px; }}
QListWidget::item:selected {{ background: #1e1e24; }}
QPlainTextEdit {{
    background: #0d0d10;
    border: 1px solid {_BORDE.name()};
    color: {_TENUE.name()};
    font-family: Consolas, monospace;
    font-size: 11px;
}}
QPushButton {{
    background: transparent;
    border: 1px solid {_TEXTO.name()};
    color: {_TEXTO.name()};
    padding: 8px 18px;
    font-weight: bold;
}}
QPushButton:hover {{ background: {_TEXTO.name()}; color: {_FONDO.name()}; }}
QPushButton:disabled {{ border-color: {_BORDE.name()}; color: #5a5c62; }}
QScrollArea {{ border: none; background: transparent; }}
"""


class PanelPlugins(QDialog):
    """La ventana. No sabe de voz ni de Ollama: sólo del gestor."""

    cambiado = pyqtSignal()          # alguien activó o desactivó algo

    def __init__(self, gestor: Gestor, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.gestor = gestor
        self._actual: Plugin | None = None

        self.setWindowTitle("NOVA · Plugins")
        self.setMinimumSize(880, 560)
        self.setStyleSheet(_HOJA)
        # Sin `WindowStaysOnTop`: es una ventana normal, y ponerla
        # siempre delante estorbaría justo cuando estás mirando otra cosa
        # para decidir si activas algo.
        self.setWindowFlags(Qt.Window)

        raiz = QHBoxLayout(self)
        raiz.setContentsMargins(18, 18, 18, 18)
        raiz.setSpacing(18)
        raiz.addLayout(self._columna_lista(), 4)
        raiz.addWidget(self._columna_detalle(), 6)

        self.recargar()

    # ── Izquierda: la lista ──────────────────────────────────────────

    def _columna_lista(self) -> QVBoxLayout:
        columna = QVBoxLayout()
        columna.setSpacing(10)

        titulo = QLabel("PLUGINS")
        fuente = QFont()
        fuente.setPointSize(10)
        fuente.setBold(True)
        titulo.setFont(fuente)
        titulo.setStyleSheet(f"color: {_TENUE.name()}; letter-spacing: 3px;")
        columna.addWidget(titulo)

        self.lista = QListWidget()
        self.lista.currentItemChanged.connect(self._al_elegir)
        columna.addWidget(self.lista, 1)

        self.resumen = QLabel()
        self.resumen.setStyleSheet(f"color: {_TENUE.name()}; font-size: 11px;")
        self.resumen.setWordWrap(True)
        columna.addWidget(self.resumen)

        boton = QPushButton("Volver a buscar")
        boton.clicked.connect(self.recargar)
        columna.addWidget(boton)
        return columna

    # ── Derecha: el detalle ──────────────────────────────────────────

    def _columna_detalle(self) -> QWidget:
        caja = QWidget()
        self.detalle = QVBoxLayout(caja)
        self.detalle.setSpacing(12)
        self.detalle.setAlignment(Qt.AlignTop)

        self.nombre = QLabel()
        fuente = QFont()
        fuente.setPointSize(15)
        fuente.setBold(True)
        self.nombre.setFont(fuente)
        self.nombre.setWordWrap(True)
        self.detalle.addWidget(self.nombre)

        self.autoria = QLabel()
        self.autoria.setStyleSheet(f"color: {_TENUE.name()}; font-size: 11px;")
        self.detalle.addWidget(self.autoria)

        self.descripcion = QLabel()
        self.descripcion.setWordWrap(True)
        self.descripcion.setStyleSheet(f"color: {_TENUE.name()};")
        self.detalle.addWidget(self.descripcion)

        self.titulo_permisos = QLabel("LO QUE PUEDE HACER")
        self.titulo_permisos.setStyleSheet(
            f"color: {_TENUE.name()}; font-size: 10px; letter-spacing: 2px;")
        self.detalle.addWidget(self.titulo_permisos)

        self.permisos = QLabel()
        self.permisos.setWordWrap(True)
        self.detalle.addWidget(self.permisos)

        self.aviso = QLabel()
        self.aviso.setWordWrap(True)
        self.aviso.setStyleSheet(
            f"color: {_AVISO.name()}; font-size: 11px; "
            f"border: 1px solid {_AVISO.name()}; padding: 8px;")
        self.aviso.hide()
        self.detalle.addWidget(self.aviso)

        self.titulo_codigo = QLabel("SU CÓDIGO, ENTERO")
        self.titulo_codigo.setStyleSheet(
            f"color: {_TENUE.name()}; font-size: 10px; letter-spacing: 2px;")
        self.titulo_codigo.hide()
        self.detalle.addWidget(self.titulo_codigo)

        self.codigo = QPlainTextEdit()
        self.codigo.setReadOnly(True)
        self.codigo.setMinimumHeight(190)
        self.codigo.hide()
        self.detalle.addWidget(self.codigo, 1)

        fila = QHBoxLayout()
        self.boton_activar = QPushButton("Activar")
        self.boton_activar.clicked.connect(self._alternar)
        fila.addWidget(self.boton_activar)
        fila.addStretch(1)
        self.detalle.addLayout(fila)

        envoltorio = QScrollArea()
        envoltorio.setWidgetResizable(True)
        envoltorio.setWidget(caja)
        return envoltorio

    # ── Datos ────────────────────────────────────────────────────────

    def recargar(self) -> None:
        recordado = self._actual.id if self._actual else None
        self.gestor.buscar()
        self.lista.clear()

        plugins = self.gestor.todos()
        for plugin in plugins:
            activo = self.gestor.esta_activo(plugin.id)
            marca = "●" if activo else "○"
            aviso = "  ⚠" if plugin.es_delicado else ""
            item = QListWidgetItem(f"{marca}  {plugin.nombre}{aviso}")
            item.setData(Qt.UserRole, plugin.id)
            if activo:
                item.setForeground(COLORES["hablando"])
            elif not plugin.valido:
                item.setForeground(COLORES["error"])
            self.lista.addItem(item)
            if plugin.id == recordado:
                self.lista.setCurrentItem(item)

        cuantos = len(plugins)
        activos = len(self.gestor.activos())
        self.resumen.setText(
            f"{cuantos} instalado(s), {activos} activo(s).\n"
            "El ⚠ significa que trae código o pide permisos delicados."
            if cuantos else
            "No hay ninguno. Se instalan copiando su carpeta en data/plugins."
        )

        if self.lista.currentItem() is None and self.lista.count():
            self.lista.setCurrentRow(0)
        elif not self.lista.count():
            self._mostrar(None)

    def _al_elegir(self, item: QListWidgetItem | None, _anterior=None) -> None:  # noqa: ANN001
        if item is None:
            self._mostrar(None)
            return
        self._mostrar(self.gestor.obtener(item.data(Qt.UserRole)))

    def _mostrar(self, plugin: Plugin | None) -> None:
        self._actual = plugin
        if plugin is None:
            self.nombre.setText("—")
            self.autoria.setText("")
            self.descripcion.setText("Elige un plugin de la lista.")
            self.permisos.setText("")
            self.aviso.hide()
            self.titulo_codigo.hide()
            self.codigo.hide()
            self.boton_activar.setEnabled(False)
            return

        activo = self.gestor.esta_activo(plugin.id)
        self.nombre.setText(plugin.nombre)
        self.autoria.setText(f"v{plugin.version} · por {plugin.autor}")
        self.descripcion.setText(plugin.descripcion)

        # Los permisos, en castellano y con los delicados en ámbar.
        lineas = []
        for clave, texto in zip(plugin.permisos, plugin.permisos_en_castellano(), strict=True):
            color = _AVISO.name() if clave in DELICADOS else _TEXTO.name()
            lineas.append(f'<span style="color:{color}">· {texto}</span>')
        self.permisos.setText("<br>".join(lineas) or
                              f'<span style="color:{_TENUE.name()}">Nada. Sólo cambia cómo habla.</span>')

        self._mostrar_codigo(plugin)
        self._mostrar_aviso(plugin)

        self.boton_activar.setEnabled(plugin.valido)
        self.boton_activar.setText("Desactivar" if activo else "Activar")

    def _mostrar_codigo(self, plugin: Plugin) -> None:
        if not plugin.tiene_codigo or plugin.carpeta is None:
            self.titulo_codigo.hide()
            self.codigo.hide()
            return
        try:
            fuente = (plugin.carpeta / "plugin.py").read_text(encoding="utf-8")
        except OSError as exc:
            fuente = f"(no pude leerlo: {exc})"
        self.codigo.setPlainText(fuente)
        self.titulo_codigo.show()
        self.codigo.show()

    def _mostrar_aviso(self, plugin: Plugin) -> None:
        partes: list[str] = []

        if not plugin.valido:
            partes.append("Este plugin está mal hecho y no se puede activar:\n· "
                          + "\n· ".join(plugin.errores))
        elif plugin.tiene_codigo:
            revision = self.gestor.revisar_codigo(plugin)
            partes.append(
                "Trae código Python, que corre con TUS permisos: puede leer tus "
                "archivos, borrarlos o mandarlos fuera. Léelo antes de activarlo."
            )
            partes.append(revision.resumen())
            for hallazgo in revision.hallazgos[:5]:
                marca = "!!" if hallazgo.grave else "· "
                partes.append(f"{marca} línea {hallazgo.linea}: {hallazgo.que} — {hallazgo.porque}")
            if revision.hallazgos:
                partes.append(
                    "Esto es un detector de descuidos, no un antivirus: quien "
                    "quiera esconder algo, puede."
                )
        elif set(plugin.permisos) & DELICADOS:
            partes.append("Pide permisos delicados. Mira arriba qué son.")

        if partes:
            self.aviso.setText("\n".join(partes))
            self.aviso.show()
        else:
            self.aviso.hide()

    # ── Acción ───────────────────────────────────────────────────────

    def _alternar(self) -> None:
        if self._actual is None:
            return
        id_ = self._actual.id
        if self.gestor.esta_activo(id_):
            self.gestor.desactivar(id_)
        else:
            self.gestor.activar(id_)
        self.recargar()
        self.cambiado.emit()
