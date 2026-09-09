"""Selección local serializada con los turnos; nunca consulta desde Qt."""
from __future__ import annotations

import time

from ..hardware import GIB
from .ollama import OllamaError


class Adaptacion:
    def __init__(self, cliente, vigilante, config):
        self.cliente = cliente
        self.vigilante = vigilante
        self.config = config
        self.preparada = False
        self.ligero = False
        self.cpu = vigilante.perfil.dedicada is not True
        self._ultimo_cambio = 0.0
        self._buenas = 0
        self._existe = {}

    def _tiene(self, nombre):
        if nombre not in self._existe:
            self._existe[nombre] = self.cliente.tiene_modelo(nombre)
        return self._existe[nombre]

    def preparar(self):
        if self.preparada:
            return
        if not self.cliente.available():
            raise OllamaError("No encuentro Ollama. Ábrelo y vuelve a hablarme.")
        if self.cpu:
            perfil = self.vigilante.perfil
            # ESTIMADO: 2K tokens y media CPU, tope 4 hilos, para la vía de 8 GiB.
            self.cliente.num_ctx = min(2048, self.config.num_ctx)
            self.cliente.opciones_hardware.update(
                num_gpu=0, num_thread=max(1, min(4, perfil.hilos // 2)))
            if self._tiene(self.config.model_cpu):
                self.cliente.usar_modelo(self.config.model_cpu)
            elif perfil.ram_total is None or perfil.ram_total <= 8 * GIB:
                # No probar a cargar 4B «a ver si cabe» en un portátil de 8 GiB.
                # El usuario instala el modelo una vez; no descargamos nada oculto.
                self._existe.clear()
                raise OllamaError(f"Para este equipo descarga antes el modelo local: ollama pull {self.config.model_cpu}")
            elif self._tiene(self.config.model_ligero):
                self.cliente.usar_modelo(self.config.model_ligero)
            self.ligero = self.cliente.model != self.config.model
        self.preparada = True

    def revisar(self):
        self.preparar()
        estado = self.vigilante.estado()
        residencia = self.cliente.residencia_detallada()
        poco = (estado.modo() == "apretado" or
                (residencia.fraccion is not None and residencia.fraccion < self.config.residencia_minima))
        if residencia.fraccion == 0:
            # CPU puro no es un incidente transitorio de VRAM: no volver al grande
            # sólo porque el modelo pequeño aparezca descargado al siguiente latido.
            self.cpu = True
            self.preparada = False
            self.preparar()
        if self.cpu:
            return estado, self.ligero
        if poco and not self.ligero and self._tiene(self.config.model_ligero):
            self.cliente.usar_modelo(self.config.model_ligero)
            self.ligero, self._ultimo_cambio, self._buenas = True, time.monotonic(), 0
        elif self.ligero:
            # ESTIMADO: tres muestras sanas y 60 s de espera reducen oscilaciones.
            # Que el pequeño quepa NO demuestra que el grande quepa; exigimos
            # además ocupación global <60% (margen estimado). Sin sensor, se queda.
            bien = estado.modo() == "holgado" and estado.vram is not None and estado.vram < 0.60
            self._buenas = self._buenas + 1 if bien else 0
            if self._buenas >= 3 and time.monotonic() - self._ultimo_cambio >= 60:
                self.cliente.usar_modelo(self.config.model)
                self.ligero, self._buenas = False, 0
        return estado, self.ligero
