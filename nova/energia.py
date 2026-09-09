"""Política de energía; el sueño conversacional sigue perteneciendo a VoiceListener."""
from __future__ import annotations

import threading
from enum import Enum


class NivelEnergia(str, Enum):
    FONDO_PROFUNDO = "Fondo profundo"
    ACTIVA = "Activa"
    REPOSO_LIGERO = "Reposo ligero"


class Energia:
    def __init__(self):
        self.nivel = NivelEnergia.FONDO_PROFUNDO
        self._version = 0
        self._cerrojo = threading.RLock()

    def activar(self):
        with self._cerrojo:
            self._version += 1
            self.nivel = NivelEnergia.ACTIVA

    def dormir(self):
        with self._cerrojo:
            if self.nivel == NivelEnergia.ACTIVA:
                self.nivel = NivelEnergia.REPOSO_LIGERO
                self._version += 1
            return self._version

    def vigente(self, version):
        with self._cerrojo:
            return self._version == version and self.nivel != NivelEnergia.ACTIVA
