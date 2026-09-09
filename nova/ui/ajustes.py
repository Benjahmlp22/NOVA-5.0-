"""Una sola ventana de ajustes; los fallos de permisos se explican aquí."""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from ..arranque_windows import ErrorArranque


class Ajustes(QDialog):
    def __init__(self, arranque, parent=None):
        super().__init__(parent)
        self.arranque = arranque
        self.setWindowTitle("Ajustes de NOVA")
        self.setMinimumWidth(430)
        self.setStyleSheet("QDialog{background:#141416;color:#f0f0f2} QLabel,QCheckBox{color:#f0f0f2} QCheckBox{padding:8px 0}")
        caja = QVBoxLayout(self)
        self.inicio = QCheckBox("Arrancar con Windows")
        self.inicio.setToolTip("Iniciar NOVA en fondo profundo al entrar en tu sesión de Windows.")
        self.inicio.setEnabled(arranque.compatible)
        caja.addWidget(self.inicio)
        self.energia = QLabel("Fondo profundo")
        caja.addWidget(self.energia)
        self.aviso = QLabel()
        self.aviso.setWordWrap(True)
        self.aviso.setTextFormat(Qt.PlainText)
        caja.addWidget(self.aviso)
        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.button(QDialogButtonBox.Close).setText("Cerrar")
        botones.rejected.connect(self.close)
        caja.addWidget(botones)
        self.inicio.clicked.connect(self._guardar)
        self._guardado = False

    def recargar(self):
        try:
            self._guardado = self.arranque.estado().registrado
            self.inicio.setChecked(self._guardado)
            self.aviso.setText("Se aplicará al iniciar sesión. Windows puede desactivarlo desde Aplicaciones de inicio. Si mueves NOVA, ábrela una vez desde su nueva carpeta para actualizar la ruta.")
        except ErrorArranque as exc:
            self.aviso.setText(str(exc))

    def _guardar(self, activar):
        try:
            self._guardado = self.arranque.guardar(activar).registrado
            self.aviso.setText("Preferencia guardada." if activar else "Inicio automático desactivado.")
        except ErrorArranque as exc:
            self.aviso.setText(str(exc))
        self.inicio.setChecked(self._guardado)
