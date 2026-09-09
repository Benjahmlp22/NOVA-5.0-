"""Autoarranque del usuario actual, sin administrador ni comandos de shell."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CLAVE = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOMBRE = "NOVA5"


class ErrorArranque(RuntimeError):
    pass


def comando_inicio(ejecutable: Path, proyecto: Path, *, empaquetado: bool) -> str:
    ejecutable = ejecutable.resolve()
    if not ejecutable.is_file():
        raise ErrorArranque("No encuentro el ejecutable de NOVA o de Python.")
    if empaquetado:
        argumentos = [str(ejecutable), "--inicio-windows"]
    else:
        script = (proyecto / "run.py").resolve()
        if not script.is_file():
            raise ErrorArranque("No encuentro run.py. Abre NOVA desde su carpeta actual.")
        sin_consola = ejecutable.with_name("pythonw.exe")
        if sin_consola.is_file():
            ejecutable = sin_consola
        argumentos = [str(ejecutable), str(script), "--inicio-windows"]
    # list2cmdline aplica las comillas de Windows, incluso a espacios y comillas.
    # No pasamos por cmd.exe: &, %, $, etc. no se interpretan como órdenes.
    comando = subprocess.list2cmdline(argumentos)
    if len(comando.encode("utf-16-le")) // 2 > 260:
        raise ErrorArranque("La ruta es demasiado larga para Inicio de Windows (260 caracteres). Mueve NOVA a una ruta más corta y vuelve a marcar la casilla.")
    return comando


@dataclass(frozen=True)
class EstadoInicio:
    registrado: bool
    comando: str = ""


class ArranqueWindows:
    def __init__(self, proyecto: Path, *, registro=None):
        self.proyecto = proyecto
        self._registro = registro

    @property
    def compatible(self):
        return sys.platform == "win32" or self._registro is not None

    def _reg(self):
        if self._registro is not None:
            return self._registro
        if sys.platform != "win32":
            raise ErrorArranque("El arranque automático sólo está disponible en Windows.")
        import winreg
        return winreg

    def estado(self) -> EstadoInicio:
        reg = self._reg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, CLAVE, 0, reg.KEY_READ) as clave:
                comando, _ = reg.QueryValueEx(clave, NOMBRE)
                return EstadoInicio(True, str(comando))
        except FileNotFoundError:
            return EstadoInicio(False)
        except OSError as exc:
            raise ErrorArranque(f"Windows no permite consultar el inicio automático: {exc}") from exc

    def comando_actual(self):
        return comando_inicio(Path(sys.executable), self.proyecto,
                              empaquetado=bool(getattr(sys, "frozen", False)))

    def guardar(self, activar: bool) -> EstadoInicio:
        reg = self._reg()
        try:
            if activar:
                comando = self.comando_actual()
                with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, CLAVE, 0, reg.KEY_SET_VALUE) as clave:
                    reg.SetValueEx(clave, NOMBRE, 0, reg.REG_SZ, comando)
            else:
                try:
                    with reg.OpenKey(reg.HKEY_CURRENT_USER, CLAVE, 0, reg.KEY_SET_VALUE) as clave:
                        reg.DeleteValue(clave, NOMBRE)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise ErrorArranque(f"No se ha podido cambiar el inicio automático. Windows denegó la operación: {exc}") from exc
        estado = self.estado()
        if estado.registrado != activar or (activar and estado.comando != comando):
            raise ErrorArranque("Windows no conservó el cambio. Revisa Aplicaciones de inicio.")
        return estado

    def reparar_ruta_registrada(self):
        # Mover un proyecto rompe Run. Abrir NOVA manualmente desde la nueva
        # ubicación permite reparar una elección YA guardada, sin reactivarla
        # si el usuario la desmarcó. No tocamos StartupApproved ni políticas.
        if self.compatible:
            estado = self.estado()
            if estado.registrado and estado.comando != self.comando_actual():
                self.guardar(True)
