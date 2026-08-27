"""Las herramientas de la ronda de agosto: notas, portapapeles, archivos.

Lo de ventanas casi no se prueba aquí a propósito: lo que hace es hablar
con la API de Windows, y un test que la imite sólo probaría el imitador.
Lo que sí se prueba es lo que decide NOVA antes de llamarla.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nova.tools import buscar_archivos, notas, portapapeles, ventanas

# ── Dictado ──────────────────────────────────────────────────────────

@pytest.fixture
def cuaderno(tmp_path, monkeypatch):
    """Manda las notas a una carpeta temporal, no al workspace real."""
    monkeypatch.setattr(notas, "_raiz", lambda: tmp_path)
    return tmp_path


def test_apuntar_guarda_lo_dictado(cuaderno):
    assert notas.apuntar("comprar pan").ok
    assert "comprar pan" in notas.leer_notas().message


def test_apuntar_dos_veces_no_pisa_la_primera(cuaderno):
    """Append y no write: la segunda nota del día no borra la primera."""
    notas.apuntar("uno")
    notas.apuntar("dos")
    dicho = notas.leer_notas().message
    assert "uno" in dicho
    assert "dos" in dicho


def test_apuntar_vacio_pregunta_en_vez_de_guardar_nada(cuaderno):
    r = notas.apuntar("   ")
    assert not r.ok
    assert "qué" in r.message.lower()


def test_leer_un_dia_sin_notas_no_es_un_error(cuaderno):
    r = notas.leer_notas("2020-01-01")
    assert r.ok
    assert "no apuntaste" in r.message.lower()


def test_una_fecha_que_no_es_fecha_se_dice(cuaderno):
    r = notas.leer_notas("el martes pasado")
    assert not r.ok


# ── Portapapeles ─────────────────────────────────────────────────────

def test_sin_texto_copiado_lo_explica(monkeypatch):
    """Distinguir «no has copiado nada» de «has copiado una imagen»."""
    monkeypatch.setattr(portapapeles, "_leer_crudo", lambda: None)
    r = portapapeles.leer()
    assert not r.ok
    assert "imagen" in r.message


def test_lo_copiado_llega_entero_si_cabe(monkeypatch):
    monkeypatch.setattr(portapapeles, "_leer_crudo", lambda: "  hola qué tal  ")
    r = portapapeles.leer()
    assert r.ok
    assert r.message.endswith("hola qué tal")


def test_un_texto_enorme_se_corta_y_se_avisa(monkeypatch):
    """Resumir media cosa creyendo que era entera es peor que no resumir."""
    largo = "x" * (portapapeles.MAX_CARACTERES + 500)
    monkeypatch.setattr(portapapeles, "_leer_crudo", lambda: largo)
    r = portapapeles.leer()
    assert r.ok
    assert r.data["caracteres"] == len(largo)
    assert "primeros" in r.message
    assert r.message.count("x") == portapapeles.MAX_CARACTERES


# ── Buscar archivos ──────────────────────────────────────────────────

@pytest.fixture
def carpetas(tmp_path, monkeypatch):
    escritorio = tmp_path / "Desktop"
    (escritorio / "sub" / "hondo").mkdir(parents=True)
    (escritorio / "factura-luz-agosto.pdf").write_text("x")
    (escritorio / "factura-agua.pdf").write_text("x")
    (escritorio / "instalador.exe").write_text("x")
    (escritorio / "sub" / "notas.txt").write_text("x")
    (escritorio / "node_modules").mkdir()
    (escritorio / "node_modules" / "factura-falsa.pdf").write_text("x")
    monkeypatch.setattr(buscar_archivos, "_carpetas", lambda: [escritorio])
    return escritorio


def test_todas_las_palabras_tienen_que_estar(carpetas):
    """«factura luz» no puede traer la del agua."""
    nombres = [Path(p).name for p in buscar_archivos.buscar("factura luz").data["rutas"]]
    assert nombres == ["factura-luz-agosto.pdf"]


def test_encuentra_varias_cuando_las_hay(carpetas):
    r = buscar_archivos.buscar("factura")
    assert r.ok
    assert len(r.data["rutas"]) == 2


def test_no_entra_en_node_modules(carpetas):
    """Ojo al escribirlo: pytest bautiza la carpeta temporal con el
    nombre del test, así que buscar el texto "node_modules" en la ruta
    entera da verdadero siempre. Hay que mirar la carpeta padre."""
    padres = [Path(p).parent.name for p in buscar_archivos.buscar("factura").data["rutas"]]
    assert "node_modules" not in padres


def test_lo_que_no_existe_se_dice(carpetas):
    r = buscar_archivos.buscar("hipoteca")
    assert not r.ok


def test_no_abre_ejecutables(carpetas):
    """Un .exe suelto en Descargas es justo lo que no debe lanzarse
    porque una frase sonó parecida."""
    r = buscar_archivos.abrir("instalador")
    assert not r.ok
    assert "programa" in r.message


def test_buscar_sin_palabras_utiles_pregunta(carpetas):
    assert not buscar_archivos.buscar("a").ok


# ── Ventanas ─────────────────────────────────────────────────────────

def test_no_lista_los_procesos_de_fondo_de_windows():
    """ApplicationFrameHost se titula con el nombre de la app que aloja,
    así que por título parecería una app de verdad."""
    assert "ApplicationFrameHost" in ventanas._PROCESOS_INVISIBLES
    assert "TextInputHost" in ventanas._PROCESOS_INVISIBLES


def test_buscar_una_ventana_sin_nombre_no_devuelve_cualquiera():
    assert ventanas._buscar("") is None
    assert ventanas._buscar("   ") is None
