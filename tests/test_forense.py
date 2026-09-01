"""Que NOVA no se pueda volver a morir en silencio.

Estas pruebas existen por seis cierres seguidos el 01/09 sin una sola
línea que dijera por qué: el log acababa en «oído en 1.38s» y ahí se
terminaba todo. Lo que se comprueba aquí es exactamente eso — que una
excepción sin capturar deje rastro en el fichero de volcados aunque
nadie esté mirando la consola.
"""

from __future__ import annotations

import faulthandler
import sys
import threading

import pytest

from nova import forense


@pytest.fixture
def forense_activo(tmp_path, monkeypatch):
    """Activa el forense y lo deja como estaba al salir.

    Sin restaurar, pytest se queda con el `excepthook` de NOVA puesto y
    los fallos de las demás pruebas dejan de verse.
    """
    excepthook = sys.excepthook
    hilo_excepthook = threading.excepthook
    estaba = faulthandler.is_enabled()
    fichero_previo = forense._fichero  # noqa: SLF001

    destino = tmp_path / "crash.log"
    yield forense.activar(destino), destino

    sys.excepthook = excepthook
    threading.excepthook = hilo_excepthook
    if forense._fichero is not None:  # noqa: SLF001
        forense._fichero.close()  # noqa: SLF001
    forense._fichero = fichero_previo  # noqa: SLF001
    if not estaba:
        faulthandler.disable()


def test_activar_deja_el_fichero_listo(forense_activo):
    ruta, destino = forense_activo
    assert ruta == destino
    assert destino.exists()
    assert "arranque" in destino.read_text(encoding="utf-8")
    assert faulthandler.is_enabled()
    assert sys.excepthook is forense._al_fallar  # noqa: SLF001


def test_una_excepcion_sin_capturar_queda_escrita(forense_activo, caplog):
    """El caso real: PyQt5 se la pasa al excepthook y aborta el proceso."""
    _, destino = forense_activo
    try:
        raise ValueError("se rompió al procesar el comando")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    escrito = destino.read_text(encoding="utf-8")
    assert "se rompió al procesar el comando" in escrito
    assert "ValueError" in escrito
    # Y también en el log, que es donde se mira primero.
    assert "se rompió al procesar el comando" in caplog.text


def test_ctrl_c_no_ensucia_el_volcado(forense_activo):
    """Ctrl+C no es un fallo: cerrar NOVA a mano no puede parecer uno."""
    _, destino = forense_activo
    antes = destino.read_text(encoding="utf-8")
    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt:
        sys.excepthook(*sys.exc_info())
    assert destino.read_text(encoding="utf-8") == antes


def test_un_hilo_que_revienta_tambien_deja_rastro(forense_activo):
    """El de escucha y el de voz son hilos: sin esto se callan al morir."""
    _, destino = forense_activo

    def explota() -> None:
        raise RuntimeError("el hilo de escucha se fue")

    hilo = threading.Thread(target=explota, name="voz")
    hilo.start()
    hilo.join()

    escrito = destino.read_text(encoding="utf-8")
    assert "el hilo de escucha se fue" in escrito
    assert "hilo voz" in escrito
