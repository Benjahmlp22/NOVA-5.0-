"""Inventario único de hardware; una consulta fallida nunca significa «sin GPU».

DXCore distingue integrada/dedicada sin adivinarlo por «Intel» o «AMD».
Los ordinales COM y los valores de propiedad salen de dxcore_interface.h
de Microsoft, no son umbrales de rendimiento. Sólo se carga la DLL en Windows.
"""

from __future__ import annotations

import ctypes as c
import os
import sys
import threading
import uuid
from dataclasses import dataclass

GIB = 1024 ** 3


@dataclass(frozen=True)
class GPU:
    nombre: str
    marca: str
    integrada: bool | None
    memoria: int | None = None


@dataclass(frozen=True)
class PerfilHardware:
    hilos: int
    nucleos: int | None
    ram_total: int | None
    # None = inventario desconocido; () = consulta correcta, sin GPU hardware.
    gpus: tuple[GPU, ...] | None = None

    @property
    def dedicada(self) -> bool | None:
        if self.gpus is None:
            return None
        if any(g.integrada is False for g in self.gpus):
            return True
        return None if any(g.integrada is None for g in self.gpus) else False


class _GUID(c.Structure):
    _fields_ = [("datos", c.c_ubyte * 16)]

    @classmethod
    def de(cls, texto):
        return cls.from_buffer_copy(uuid.UUID(texto).bytes_le)


def _metodo(objeto, indice, retorno, *argumentos):
    tabla = c.cast(objeto, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(retorno, c.c_void_p, *argumentos)(tabla[indice])


def _comprobar(resultado):
    if resultado < 0:
        raise OSError(f"DXCore devolvió 0x{resultado & 0xffffffff:08x}")


def _soltar(objeto):
    if objeto:
        _metodo(objeto, 2, c.c_uint32)(objeto)


class _DXCore:
    def __init__(self):
        self.adaptadores = []
        self._dll = c.WinDLL("dxcore.dll")
        fabrica, lista = c.c_void_p(), c.c_void_p()
        iid_fabrica = _GUID.de("78ee5945-c36e-4b13-a669-005dd11c0f06")
        iid_lista = _GUID.de("526c7776-40e9-459b-b711-f32ad76dfc28")
        iid_gpu = _GUID.de("f0db4c7f-fe5a-42a2-bd62-f2a6cf6fc83e")
        # DXCORE_ADAPTER_ATTRIBUTE_D3D11_GRAPHICS. Es el de D3D11, no el
        # de D3D12 (0c9ece4d-…), a propósito: filtra el mismo hardware en
        # la práctica y es el que está verificado funcionando aquí
        # (RTX 3060 detectada, ocupación contrastada contra nvidia-smi:
        # 3.25 GiB frente a 3.24). Una tarjeta sólo-D3D12 o MCDM se
        # quedaría fuera; si algún día aparece una así, el respaldo de
        # nvidia-smi la cubre.
        atributo = _GUID.de("8c47866b-7583-450d-f0f0-6bada895af4b")
        crear = self._dll.DXCoreCreateAdapterFactory
        crear.argtypes = [c.POINTER(_GUID), c.POINTER(c.c_void_p)]
        crear.restype = c.c_int32
        try:
            _comprobar(crear(c.byref(iid_fabrica), c.byref(fabrica)))
            _comprobar(_metodo(fabrica, 3, c.c_int32, c.c_uint32,
                              c.c_void_p, c.c_void_p, c.c_void_p)(
                fabrica, 1, c.byref(atributo), c.byref(iid_lista), c.byref(lista)))
            cantidad = _metodo(lista, 4, c.c_uint32)(lista)
            for indice in range(cantidad):
                adaptador = c.c_void_p()
                _comprobar(_metodo(lista, 3, c.c_int32, c.c_uint32,
                                  c.c_void_p, c.c_void_p)(
                    lista, indice, c.byref(iid_gpu), c.byref(adaptador)))
                try:
                    if not self._propiedad(adaptador, 11, c.c_bool()).value:
                        continue  # WARP es software, no una tarjeta disponible.
                    largo = c.c_size_t()
                    _comprobar(_metodo(adaptador, 7, c.c_int32, c.c_uint32,
                                      c.c_void_p)(adaptador, 2, c.byref(largo)))
                    descripcion = self._propiedad(adaptador, 2, c.create_string_buffer(largo.value))
                    hardware = self._propiedad(adaptador, 3, (c.c_uint32 * 4)())
                    try:
                        integrada = self._propiedad(adaptador, 12, c.c_bool()).value
                    except OSError:
                        integrada = None
                    memoria = self._propiedad(adaptador, 7, c.c_uint64()).value
                    gpu = GPU(descripcion.value.decode("utf-8", errors="replace"),
                              {0x10de: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel"}.get(hardware[0], "Otra"),
                              integrada, memoria or None)
                    self.adaptadores.append((adaptador, gpu))
                    adaptador = None
                finally:
                    _soltar(adaptador)
        except Exception:
            for adaptador, _ in self.adaptadores:
                _soltar(adaptador)
            self.adaptadores.clear()
            raise
        finally:
            _soltar(lista)
            _soltar(fabrica)

    @staticmethod
    def _propiedad(adaptador, propiedad, salida):
        _comprobar(_metodo(adaptador, 6, c.c_int32, c.c_uint32,
                          c.c_size_t, c.c_void_p)(adaptador, propiedad,
                                                c.sizeof(salida), c.byref(salida)))
        return salida

    def ocupacion(self):
        valores = []
        for adaptador, gpu in self.adaptadores:
            if gpu.integrada is not False or not gpu.memoria:
                continue  # Memoria compartida se vigila como RAM disponible.
            if not _metodo(adaptador, 8, c.c_bool, c.c_uint32)(adaptador, 2):
                return None  # Drivers anteriores no publican el uso global.
            try:
                # Propiedad 15 = PhysicalAdapterCount: cuántas tarjetas
                # físicas hay detrás de este adaptador. Si no es 1 es un
                # grupo enlazado (SLI/CrossFire) y la lectura de una sola
                # no describe el conjunto.
                if self._propiedad(adaptador, 15, c.c_uint32()).value != 1:
                    return None
            except OSError:
                return None  # Sin topología confirmada no inventar un porcentaje.
            # AdapterMemoryUsageBytes = 2; Dedicated = 0; índice físico = 0.
            # NO usar AdapterMemoryBudget: currentUsage allí es de ESTE proceso.
            entrada = (c.c_uint32 * 2)(0, 0)
            salida = (c.c_uint64 * 2)()  # committed, resident; bytes globales.
            try:
                _comprobar(_metodo(adaptador, 9, c.c_int32, c.c_uint32,
                                  c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p)(
                    adaptador, 2, c.sizeof(entrada), c.byref(entrada),
                    c.sizeof(salida), c.byref(salida)))
            except OSError:
                return None
            valores.append(min(1.0, salida[1] / gpu.memoria))
        # Conservador en multitarjeta: la más llena manda; no se suman capacidades.
        return max(valores) if valores else None


_cerrojo = threading.RLock()
_perfil = None
_dx = None


def perfilar() -> PerfilHardware:
    global _perfil, _dx
    with _cerrojo:
        if _perfil is not None:
            return _perfil
        hilos, nucleos, ram = os.cpu_count() or 1, None, None
        try:
            import psutil
            nucleos = psutil.cpu_count(logical=False)
            ram = psutil.virtual_memory().total
        except (ImportError, AttributeError, OSError):
            pass
        gpus = None
        if sys.platform == "win32":
            try:
                _dx = _DXCore()
                gpus = tuple(g for _, g in _dx.adaptadores)
            except (OSError, AttributeError, ValueError):
                # Sin driver/API no podemos concluir que falte la tarjeta.
                pass
        _perfil = PerfilHardware(max(1, hilos), nucleos, ram, gpus)
        return _perfil


def ocupacion_dxcore() -> float | None:
    perfilar()
    with _cerrojo:
        return _dx.ocupacion() if _dx else None
