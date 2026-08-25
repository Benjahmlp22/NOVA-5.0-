"""Conciencia del entorno: lo que NOVA sabe sin que se lo preguntes.

Se inyecta en el prompt de cada turno para que "¿qué hora es?" o "¿qué
estoy jugando?" se respondan directamente, sin gastar una ronda de
herramienta.

Regla dura: esto se ejecuta en el camino crítico de cada mensaje, así
que **nunca** hace red aquí.  El clima lo refresca un hilo aparte y
esto solo lee lo cacheado.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from datetime import datetime

log = logging.getLogger("nova.awareness")

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

# Códigos WMO de open-meteo → descripción corta.
_WMO = {
    0: "despejado", 1: "casi despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla", 51: "llovizna", 53: "llovizna", 55: "llovizna",
    61: "lluvia ligera", 63: "lluvia", 65: "lluvia fuerte",
    71: "nieve ligera", 73: "nieve", 75: "nieve fuerte",
    80: "chubascos", 81: "chubascos", 82: "chubascos fuertes",
    95: "tormenta", 96: "tormenta con granizo", 99: "tormenta con granizo",
}

_REFRESH_S = 15 * 60


class Awareness:
    def __init__(self) -> None:
        self._weather = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── Ciclo de vida ────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clima")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ── Uso ──────────────────────────────────────────────────────────

    def snapshot(self) -> str:
        """Bloque de contexto para el prompt. Instantáneo, sin red."""
        ahora = datetime.now()
        lineas = [f"- Ahora: {DIAS[ahora.weekday()]} {ahora.strftime('%d/%m/%Y, %H:%M')}"]

        ventana = self.foreground_app()
        if ventana:
            lineas.append(f"- El usuario tiene delante: {ventana}")

        bateria = self._battery()
        if bateria:
            lineas.append(f"- Batería: {bateria}")

        if self._weather:
            lineas.append(f"- Clima: {self._weather}")

        return "## Contexto actual\n" + "\n".join(lineas)

    @staticmethod
    def foreground_app() -> str:
        """Ventana en primer plano — qué está haciendo/jugando el usuario."""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return ""
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            titulo = buf.value.strip()

            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proceso = ""
            try:
                import psutil

                proceso = psutil.Process(pid.value).name().replace(".exe", "")
            except Exception:
                pass

            if titulo and proceso and proceso.lower() not in titulo.lower():
                return f"{titulo} ({proceso})"
            return titulo or proceso
        except Exception:
            return ""

    @staticmethod
    def _battery() -> str:
        try:
            import psutil

            b = psutil.sensors_battery()
            if b is None:
                return ""
            return f"{int(b.percent)}% ({'enchufado' if b.power_plugged else 'sin enchufar'})"
        except Exception:
            return ""

    # ── Clima en segundo plano ───────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._weather = self._fetch_weather()
            except Exception:
                log.debug("no pude leer el clima", exc_info=True)
            # Espera interrumpible: al cerrar NOVA no se queda 15 min colgado.
            self._stop.wait(_REFRESH_S)

    @staticmethod
    def _fetch_weather() -> str:
        import httpx

        with httpx.Client(timeout=5.0) as http:
            geo = http.get("http://ip-api.com/json/?fields=status,city,lat,lon").json()
            if geo.get("status") != "success":
                return ""
            datos = http.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": geo["lat"],
                    "longitude": geo["lon"],
                    "current": "temperature_2m,weather_code",
                },
            ).json()
        actual = datos.get("current") or {}
        temp = actual.get("temperature_2m")
        if temp is None:
            return ""
        desc = _WMO.get(int(actual.get("weather_code", -1)), "")
        ciudad = geo.get("city") or ""
        partes = [f"{round(float(temp))} °C"]
        if desc:
            partes.append(desc)
        if ciudad:
            partes.append(f"en {ciudad}")
        return " ".join(partes)
