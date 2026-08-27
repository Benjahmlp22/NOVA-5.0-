"""Leer la pantalla: la lógica, sin abrir PowerShell ni WinRT.

Lo que puede romperse y no se ve venir es el recorte del texto y qué se
responde cuando no hay OCR. El motor de Windows en sí no se imita: un
test de un imitador prueba el imitador.
"""

from __future__ import annotations

import pytest

from nova.tools import pantalla


class _LectorFalso:
    def __init__(self, texto: str = "hola") -> None:
        self.texto = texto
        self.leidas: list = []

    def leer(self, imagen):  # noqa: ANN001
        self.leidas.append(imagen)
        return self.texto

    def stop(self):
        pass


@pytest.fixture
def sin_windows(monkeypatch, tmp_path):
    """Con un lector de mentira y una captura que siempre funciona."""
    def _falso(texto="hola"):
        monkeypatch.setattr(pantalla, "_obtener_lector", lambda: _LectorFalso(texto))
        monkeypatch.setattr(pantalla, "_capturar", lambda solo_ventana: tmp_path / "x.png")
    return _falso


def test_lee_lo_que_hay(sin_windows):
    sin_windows("Error 0x80070005")
    r = pantalla.leer()
    assert r.ok
    assert "Error 0x80070005" in r.message


def test_una_pantalla_sin_texto_no_es_un_fallo(sin_windows):
    sin_windows("")
    r = pantalla.leer()
    assert r.ok
    assert "no veo texto" in r.message.lower()


def test_una_pantalla_llena_de_codigo_se_recorta(sin_windows):
    """Todo lo que se lee es prefill, y el prefill se paga en latencia."""
    sin_windows("x" * (pantalla.MAX_CARACTERES + 900))
    r = pantalla.leer()
    assert r.ok
    assert r.data["caracteres"] == pantalla.MAX_CARACTERES + 900
    assert r.message.count("x") == pantalla.MAX_CARACTERES
    assert "primeros" in r.message


def test_dice_si_mira_la_ventana_o_la_pantalla(sin_windows):
    sin_windows("algo")
    assert "ventana" in pantalla.leer(todo=False).message
    assert "la pantalla" in pantalla.leer(todo=True).message


def test_sin_ocr_lo_dice_en_vez_de_callarse(monkeypatch):
    monkeypatch.setattr(pantalla, "_obtener_lector", lambda: None)
    r = pantalla.leer()
    assert not r.ok
    assert "OCR" in r.message


def test_si_falla_la_captura_lo_dice(monkeypatch):
    monkeypatch.setattr(pantalla, "_obtener_lector", lambda: _LectorFalso())
    monkeypatch.setattr(pantalla, "_capturar", lambda solo_ventana: None)
    r = pantalla.leer()
    assert not r.ok


def test_cada_captura_usa_un_archivo_nuevo(monkeypatch, tmp_path):
    """WinRT deja la imagen bloqueada después de leerla: reescribir la
    misma ruta fallaba con «Invalid argument» en la segunda lectura."""
    pantalla._usadas.clear()
    creados = []

    class _MSSFalso:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        monitors = [{"top": 0, "left": 0, "width": 8, "height": 8}]

        def grab(self, region):  # noqa: ANN001
            class C:
                rgb = b"\x00" * (8 * 8 * 3)
                size = (8, 8)
            return C()

    import sys
    import types
    falso = types.ModuleType("mss")
    falso.MSS = _MSSFalso
    herramientas = types.ModuleType("mss.tools")
    herramientas.to_png = lambda rgb, size, output: (creados.append(output),
                                                     open(output, "wb").close())
    falso.tools = herramientas
    monkeypatch.setitem(sys.modules, "mss", falso)
    monkeypatch.setitem(sys.modules, "mss.tools", herramientas)
    monkeypatch.setattr(pantalla.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(pantalla, "_region_de_la_ventana", lambda: None)
    monkeypatch.setattr(pantalla, "_barrer", lambda: None)

    pantalla._capturar(solo_ventana=False)
    pantalla._capturar(solo_ventana=False)
    assert len(set(creados)) == 2, creados
    pantalla._usadas.clear()
