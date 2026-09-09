from types import SimpleNamespace

import httpx
import pytest

from nova.hardware import GIB, GPU, PerfilHardware
from nova.llm.adaptacion import Adaptacion
from nova.llm.ollama import OllamaClient, Residencia
from nova.recursos import Estado, Vigilante, calibrar


@pytest.mark.parametrize("hilos,ram,gpus,cpu_j,cpu_a,libre_j,hilos_fondo", [
    # `hilos_fondo` son tres cuartos de los hilos lógicos con tope 8: el
    # tope está MEDIDO en el 5600G (8 hilos, 31 ms/imagen con CLIP, mejor
    # que los 12 con 51 ms) y la proporción es estimada.
    (4, 8, (), 55, 73, 2, 3),
    (8, 16, (GPU("Integrada", "Intel", True),), 65, 81, 2, 6),
    (16, 32, (GPU("Arc", "Intel", False, 8 * GIB),), 70, 88, 4, 8),
])
def test_presupuesto_en_tres_maquinas(monkeypatch, hilos, ram, gpus, cpu_j, cpu_a, libre_j, hilos_fondo):
    perfil = PerfilHardware(hilos, hilos // 2, ram * GIB, gpus)
    v = Vigilante(perfil)
    assert (v.umbrales.cpu_justo, v.umbrales.cpu_apretado) == (cpu_j, cpu_a)
    assert v.umbrales.libre_justo == libre_j * GIB
    monkeypatch.setattr(v, "estado", lambda: Estado(umbrales=v.umbrales))
    assert v.hilos_para_lo_pesado() == hilos_fondo
    assert Estado(cpu=70, umbrales=v.umbrales).modo() == "justo"


def test_ram_disponible_manda_sobre_el_porcentaje():
    p = PerfilHardware(4, 2, 8 * GIB, ())
    assert Estado(ram=85, ram_disponible=int(1.2 * GIB), umbrales=calibrar(p)).modo() == "justo"
    assert Estado(ram=50, ram_disponible=int(.8 * GIB), umbrales=calibrar(p)).modo() == "apretado"


def test_sin_gpu_desconocida_e_integrada_son_distintas():
    sin = PerfilHardware(4, 2, 8 * GIB, ())
    desconocida = PerfilHardware(4, 2, 8 * GIB, None)
    integrada = PerfilHardware(8, 4, 16 * GIB, (GPU("Radeon integrada", "AMD", True),))
    assert sin.dedicada is False and desconocida.dedicada is None and integrada.dedicada is False
    assert "No he detectado" in Estado(perfil=sin).en_una_frase()
    assert "No sé" in Estado(perfil=desconocida).en_una_frase()
    assert "gráfica al 0" not in Estado(perfil=integrada).en_una_frase()


def test_cero_cpu_del_modelo_se_dice():
    e = Estado(residencia=0, modelo_cargado=True)
    assert "memoria del procesador" in e.en_una_frase()


@pytest.mark.parametrize("contenido,cargado,fraccion", [
    ({"models": []}, False, None),
    ({"models": [{"name": "modelo:latest", "size": 100, "size_vram": 0}]}, True, 0),
    ({"models": [{"name": "modelo:latest", "size": 100, "size_vram": 25}]}, True, .25),
    ({"models": [{"name": "modelo:latest", "size": 100}]}, True, None),
    ({"models": [{"name": "modelo:latest", "size": 0, "size_vram": 0}]}, True, None),
    ({"models": [{"name": "modelo:latest", "size": 100, "size_vram": 200}]}, True, None),
    ({"models": "no es una lista"}, None, None),
])
def test_residencia_sin_asumir_marca(contenido, cargado, fraccion):
    cliente = OllamaClient("http://127.0.0.1:11434", "modelo")
    cliente._http.close()
    cliente._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=contenido)))
    try:
        dato = cliente.residencia_detallada()
        assert dato.cargado is cargado and dato.fraccion == fraccion
    finally:
        cliente.close()


def test_error_http_no_se_convierta_en_modelo_descargado():
    c = OllamaClient("http://127.0.0.1:11434", "modelo")
    c._http.close()
    c._http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"models": []})))
    try:
        assert c.residencia_detallada().cargado is None
    finally:
        c.close()


class ClienteFalso:
    def __init__(self):
        self.model, self.num_ctx = "grande", 8192
        self.opciones_hardware = {}
        self.cambios = []
        self.dato = Residencia(True, 1)

    def available(self):
        return True

    def tiene_modelo(self, nombre):
        return True

    def usar_modelo(self, nombre):
        self.cambios.append(nombre)
        self.model = nombre

    def residencia_detallada(self):
        return self.dato


def adaptacion(perfil):
    c = ClienteFalso()
    v = SimpleNamespace(perfil=perfil, estado=lambda: Estado(vram=None))
    config = SimpleNamespace(model="grande", model_ligero="ligero", model_cpu="cpu",
                             num_ctx=8192, residencia_minima=.85)
    return Adaptacion(c, v, config), c


def test_portatil_cpu_no_carga_el_modelo_grande():
    a, c = adaptacion(PerfilHardware(4, 2, 8 * GIB, ()))
    a.preparar()
    assert c.model == "cpu" and c.num_ctx == 2048
    assert c.opciones_hardware == {"num_gpu": 0, "num_thread": 2}
    c.dato = Residencia(False, None)
    a.revisar()
    assert c.model == "cpu"


def test_una_dedicada_que_ollama_no_usa_cae_a_cpu():
    a, c = adaptacion(PerfilHardware(16, 8, 32 * GIB, (GPU("Radeon", "AMD", False),)))
    c.dato = Residencia(True, 0)
    a.revisar()
    assert a.cpu and c.model == "cpu"


def test_que_el_pequeno_quepa_no_demuestra_que_quepa_el_grande():
    a, c = adaptacion(PerfilHardware(16, 8, 32 * GIB, (GPU("Arc", "Intel", False),)))
    c.dato = Residencia(True, .1)
    a.revisar()
    assert c.model == "ligero"
    c.dato = Residencia(True, 1)
    a._ultimo_cambio -= 600
    for _ in range(4):
        a.revisar()
    assert c.model == "ligero" and c.cambios == ["ligero"]


def test_perfilar_solo_una_vez_y_dll_inaccesible_es_desconocido(monkeypatch):
    import nova.hardware as h
    llamadas = []
    monkeypatch.setattr(h, "_perfil", None)
    monkeypatch.setattr(h, "_dx", None)
    monkeypatch.setattr(h.sys, "platform", "win32")
    def falla():
        llamadas.append(1)
        raise OSError("sin DXCore")
    monkeypatch.setattr(h, "_DXCore", falla)
    assert h.perfilar() is h.perfilar()
    assert llamadas == [1] and h.perfilar().gpus is None
