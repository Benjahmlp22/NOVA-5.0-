"""Filtro experimental de energía; no confundirlo con un VAD de habla."""
from collections import deque


class PuertaEnergia:
    def __init__(self, detector, bloque_ms):
        self.detector = detector
        # ESTIMADO: 300 ms previos protegen consonantes; 600 ms finales dejan
        # terminar a Vosk. Probar susurros, distancia, música y «no va» antes
        # de activar por defecto: bajar CPU perdiendo llamadas no es una mejora.
        self.previo = deque(maxlen=max(1, round(300 / bloque_ms)))
        self.cola_maxima = max(1, round(600 / bloque_ms))
        self.restantes = 0

    def escucha(self, bloque, nivel, umbral):
        self.previo.append(bloque)
        if nivel >= umbral:
            entrada = bloque if self.restantes else b"".join(self.previo)
            self.restantes = self.cola_maxima
        elif self.restantes:
            entrada = bloque
            self.restantes -= 1
        else:
            return False
        resultado = self.detector.escucha(entrada)
        if resultado or not self.restantes:
            self.detector.reiniciar()
            self.restantes = 0
            self.previo.clear()
        return resultado
