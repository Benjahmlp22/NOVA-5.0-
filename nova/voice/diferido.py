"""Whisper aislado de Qt. Cerrar el proceso devuelve sus reservas al SO.

El audio se captura ANTES de cargar: la primera orden no se pierde mientras
se prepara el motor. No se escriben grabaciones en disco.
"""
from __future__ import annotations

import logging
import multiprocessing
import threading
import time

log = logging.getLogger("nova.voice.diferido")


def _motor(conexion, opciones):
    # En spawn esta función se importa sin cargar nova.app ni PyQt5.
    from .transcriptor import Transcriptor
    motor = Transcriptor(**opciones)
    try:
        if not motor.cargar():
            conexion.send((False, motor.error))
            return
        conexion.send((True, motor.motor))
        while True:
            mensaje = conexion.recv()
            if mensaje is None:
                return
            try:
                conexion.send((True, motor.transcribir_detallado(mensaje)))
            except Exception as exc:
                conexion.send((False, str(exc)))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        conexion.close()


class TranscriptorDiferido:
    diferido = True

    def __init__(self, *, timeout=120.0, **opciones):
        self.opciones = opciones
        # ESTIMADO: plazo de fallo, no promesa de latencia. Sin descargas durante
        # el arranque: los modelos deben estar instalados antes del uso offline.
        self.timeout = timeout
        self.error = ""
        self.motor = "Oído preparado; transcripción pendiente de la primera llamada"
        self._proceso = self._conexion = None
        self._cerrojo = threading.RLock()
        self._uso = 0
        self._confirmado = False
        self._cerrado = False

    @property
    def cargado(self):
        return self._proceso is not None and self._proceso.is_alive()

    @property
    def version(self):
        return self._uso

    def _recibir(self):
        if not self._conexion.poll(self.timeout):
            raise TimeoutError("La transcripción agotó el tiempo de espera")
        bien, resultado = self._conexion.recv()
        if not bien:
            raise RuntimeError(resultado)
        return resultado

    def _cargar(self):
        if self._cerrado:
            raise RuntimeError("El transcriptor se está cerrando")
        if self.cargado:
            return
        inicio = time.perf_counter()
        self._descargar()
        contexto = multiprocessing.get_context("spawn")
        self._conexion, hijo = contexto.Pipe()
        self._proceso = contexto.Process(target=_motor, args=(hijo, self.opciones),
                                         name="NOVA-STT", daemon=True)
        self._proceso.start()
        hijo.close()
        self.motor = self._recibir()
        log.info("STT carga del proceso y motor: %.3f s", time.perf_counter() - inicio)

    def cargar(self):
        with self._cerrojo:
            try:
                self._cargar()
                return True
            except Exception as exc:
                self.error = str(exc)
                self._descargar()
                return False

    def precalentar(self):
        # No inferir sobre ruido artificial: el primer audio útil ya está guardado.
        return None

    def transcribir(self, senal):
        return self.transcribir_detallado(senal).texto

    def transcribir_detallado(self, senal):
        with self._cerrojo:
            self._uso += 1
            t0 = time.perf_counter()
            try:
                self._cargar()
                self._conexion.send(senal)
                resultado = self._recibir()
                log.info("STT hasta texto: %.3f s; motor=%s", time.perf_counter() - t0, self.motor)
                return resultado
            except Exception as exc:
                self.error = f"No pude transcribir: {exc}"
                self._descargar()
                if self.opciones.get("device") != "cpu":
                    # También cubre un crash nativo: se reintenta EL MISMO audio
                    # en CPU una sola vez, conservando vivo el proceso de Qt.
                    self.opciones.update(device="cpu", compute_type="int8")
                    return self.transcribir_detallado(senal)
                raise RuntimeError(self.error) from exc

    def confirmar_candidato(self, aceptado):
        with self._cerrojo:
            self._confirmado |= aceptado
            if not self._confirmado:
                # «No va» puede abrir Vosk: no debe dejar Whisper residente para
                # siempre si nadie le ha hablado realmente en esta sesión.
                self._descargar()

    def descargar(self, version=None):
        if not self._cerrojo.acquire(blocking=False):
            return False
        try:
            if version is None or version == self._uso:
                self._descargar()
            return True
        finally:
            self._cerrojo.release()

    def _descargar(self):
        proceso, conexion = self._proceso, self._conexion
        self._proceso = self._conexion = None
        if proceso is not None:
            try:
                if proceso.is_alive():
                    conexion.send(None)
                    proceso.join(1.0)
                if proceso.is_alive():
                    # Sólo se termina NUESTRO trabajador STT, nunca Ollama ni otra app.
                    proceso.terminate()
                    proceso.join(1.0)
                if proceso.is_alive():
                    proceso.kill()
                    proceso.join(1.0)
                proceso.close()
            except (OSError, ValueError, EOFError):
                log.debug("El trabajador ya terminó", exc_info=True)
        if conexion is not None:
            conexion.close()

    def cerrar(self):
        with self._cerrojo:
            self._cerrado = True
            self._descargar()
