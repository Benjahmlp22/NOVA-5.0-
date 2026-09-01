"""Cuándo NOVA decide apartarse.

Sin tocar la máquina de verdad: se le dan los números y se comprueba qué
decide. Medir el PC real haría que el test dijera cosas distintas según
lo que estuviera abierto, que es justo lo contrario de un test.
"""

from __future__ import annotations

import pytest

from nova.recursos import Estado, Vigilante


def _estado(cpu=10.0, ram=30.0, vram=0.2, juego=""):  # noqa: ANN001
    return Estado(cpu=cpu, ram=ram, vram=vram, juego=juego, medido=1.0)


# ── En qué modo está ─────────────────────────────────────────────────

def test_un_pc_tranquilo_va_holgado():
    assert _estado().modo() == "holgado"


@pytest.mark.parametrize("kwargs", [
    {"cpu": 75.0},      # medido: a partir de aquí hablar ya se nota lento
    {"ram": 88.0},
    {"vram": 0.90},     # Ollama empieza a dejar el modelo fuera
])
def test_una_sola_cosa_cargada_ya_es_justo(kwargs):
    assert _estado(**kwargs).modo() == "justo"


@pytest.mark.parametrize("kwargs", [
    {"cpu": 95.0},
    {"ram": 96.0},
    {"vram": 0.97},
])
def test_al_limite_es_apretado(kwargs):
    assert _estado(**kwargs).modo() == "apretado"


def test_un_juego_delante_es_apretado_aunque_los_numeros_den_bien():
    """Los fotogramas son del juego, no de NOVA. Aunque la CPU esté al
    10%, ponerse a indexar 16.000 imágenes ahí es quitarle recursos a
    algo que el usuario SÍ está mirando."""
    assert _estado(cpu=10.0, ram=20.0, vram=0.1, juego="StarCitizen").modo() == "apretado"


# ── Qué se hace con eso ──────────────────────────────────────────────

def test_con_el_pc_al_limite_no_se_indexa(monkeypatch):
    v = Vigilante()
    monkeypatch.setattr(v, "estado", lambda forzar=False: _estado(cpu=95.0))
    assert not v.hay_sitio_para_lo_pesado()


def test_con_el_pc_tranquilo_si_se_indexa(monkeypatch):
    v = Vigilante()
    monkeypatch.setattr(v, "estado", lambda forzar=False: _estado())
    assert v.hay_sitio_para_lo_pesado()


def test_los_hilos_bajan_segun_se_carga_el_pc(monkeypatch):
    v = Vigilante()

    def con(estado):
        monkeypatch.setattr(v, "estado", lambda forzar=False, e=estado: e)
        return v.hilos_para_lo_pesado()

    holgado = con(_estado())
    justo = con(_estado(cpu=75.0))
    apretado = con(_estado(juego="StarCitizen"))

    assert holgado > justo > apretado
    assert apretado == 1
    # Nunca todos los núcleos: dejar cuatro libres salió MÁS rápido
    # medido (31 ms por imagen con 8 hilos contra 51 con 12).
    import os
    assert holgado <= max(2, (os.cpu_count() or 4) - 4)


# ── Lo que se dice en alto ───────────────────────────────────────────

def test_con_un_juego_lo_nombra():
    frase = _estado(juego="StarCitizen", cpu=80.0, vram=0.92).en_una_frase()
    assert "StarCitizen" in frase
    assert "80" in frase


def test_sin_juego_dice_los_tres_numeros():
    frase = _estado(cpu=12.0, ram=34.0, vram=0.56).en_una_frase()
    assert "12" in frase
    assert "34" in frase
    assert "56" in frase


def test_la_frase_no_lleva_tecnicismos():
    """Se dice en alto: «fracción de VRAM» no lo entiende nadie."""
    frase = _estado(cpu=50.0).en_una_frase().lower()
    for palabra in ("vram", "fracción", "threshold", "%"):
        assert palabra not in frase


# ── Sin gráfica NVIDIA ───────────────────────────────────────────────

def test_sin_nvidia_no_es_un_error(monkeypatch):
    """Hay PCs sin gráfica dedicada, y NOVA funciona igual."""
    import nova.recursos as mod
    monkeypatch.setattr(mod, "_vram", lambda: 0.0)
    monkeypatch.setattr(mod, "_juego_delante", lambda: "")
    v = Vigilante()
    assert v.estado(forzar=True).vram == 0.0


def test_la_primera_medida_de_cpu_no_es_cero(monkeypatch):
    """`cpu_percent(interval=None)` devuelve 0.0 la primera vez de un
    proceso, y NOVA llegó a decir «CPU al 0 por ciento» con un juego
    abierto y la máquina al 90."""
    import nova.recursos as mod
    monkeypatch.setattr(mod, "_vram", lambda: 0.5)
    monkeypatch.setattr(mod, "_juego_delante", lambda: "")

    llamadas = []

    class _PsutilFalso:
        @staticmethod
        def cpu_percent(interval=None):  # noqa: ANN001
            llamadas.append(interval)
            return 42.0 if interval else 0.0

        @staticmethod
        def virtual_memory():
            class M:
                percent = 50.0
            return M()

    monkeypatch.setitem(__import__("sys").modules, "psutil", _PsutilFalso)
    v = Vigilante()
    assert v.estado(forzar=True).cpu == 42.0
    # La primera con ventana de verdad, la segunda ya sin bloquear.
    v.estado(forzar=True)
    assert llamadas[-1] is None
