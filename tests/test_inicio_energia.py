import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nova.arranque_windows import ArranqueWindows, ErrorArranque, comando_inicio
from nova.energia import Energia, NivelEnergia


class RegistroFalso:
    HKEY_CURRENT_USER, KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 3, 4

    def __init__(self):
        self.datos = {}
        self.denegar = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def OpenKey(self, *args):
        return self

    def CreateKeyEx(self, *args):
        if self.denegar:
            raise PermissionError("Acceso denegado")
        return self

    def QueryValueEx(self, clave, nombre):
        if nombre not in self.datos:
            raise FileNotFoundError(nombre)
        return self.datos[nombre], self.REG_SZ

    def SetValueEx(self, clave, nombre, reservado, tipo, valor):
        self.datos[nombre] = valor

    def DeleteValue(self, clave, nombre):
        self.datos.pop(nombre, None)


def test_registro_persistente_idempotente_y_desactivable(tmp_path, monkeypatch):
    registro = RegistroFalso()
    servicio = ArranqueWindows(tmp_path, registro=registro)
    monkeypatch.setattr(servicio, "comando_actual", lambda: '"NOVA 5.exe" --inicio-windows')
    assert servicio.estado().registrado is False
    servicio.guardar(True)
    assert ArranqueWindows(tmp_path, registro=registro).estado().registrado is True
    servicio.guardar(True)
    assert len(registro.datos) == 1
    servicio.guardar(False)
    servicio.guardar(False)
    assert servicio.estado().registrado is False


def test_permiso_denegado_no_se_guarda_como_exito(tmp_path, monkeypatch):
    registro = RegistroFalso()
    registro.denegar = True
    servicio = ArranqueWindows(tmp_path, registro=registro)
    monkeypatch.setattr(servicio, "comando_actual", lambda: "NOVA.exe")
    with pytest.raises(ErrorArranque, match="denegó"):
        servicio.guardar(True)
    assert not servicio.estado().registrado


@pytest.mark.parametrize("empaquetado", [False, True])
def test_comando_con_espacios_python_y_exe(tmp_path, empaquetado):
    # Carpeta propia y CORTA, no `tmp_path` a secas: la ruta que pytest
    # reparte depende de dónde se corra, y desde una carpeta ya larga
    # (la de Benjahmlp22 lo es) el comando pasaba de 260 caracteres y saltaba
    # el guardián de `comando_inicio`. Lo que se prueba aquí son las
    # comillas, no el límite de longitud — ese tiene su propio test.
    proyecto = Path(tempfile.mkdtemp(prefix="nv ")) / "NOVA con espacios"
    proyecto.mkdir()
    (proyecto / "run.py").touch()
    ejecutable = proyecto / ("NOVA.exe" if empaquetado else "python.exe")
    ejecutable.touch()
    (proyecto / "pythonw.exe").touch()
    comando = comando_inicio(ejecutable, proyecto, empaquetado=empaquetado)
    assert comando.startswith('"') and comando.endswith("--inicio-windows")
    assert ("run.py" in comando) is not empaquetado
    assert ("pythonw.exe" in comando) is not empaquetado


def test_reparar_tras_mover_no_reactiva_una_eleccion_apagada(tmp_path, monkeypatch):
    registro = RegistroFalso()
    s = ArranqueWindows(tmp_path, registro=registro)
    monkeypatch.setattr(s, "comando_actual", lambda: "nuevo.exe --inicio-windows")
    s.reparar_ruta_registrada()
    assert not registro.datos
    registro.datos["NOVA5"] = "viejo.exe --inicio-windows"
    s.reparar_ruta_registrada()
    assert registro.datos["NOVA5"] == "nuevo.exe --inicio-windows"


def test_ruta_larga_se_rechaza_con_aviso(tmp_path):
    proyecto = tmp_path / ("x" * 120) / ("y" * 120)
    proyecto.mkdir(parents=True)
    ejecutable = proyecto / "nova.exe"
    ejecutable.touch()
    with pytest.raises(ErrorArranque, match="larga"):
        comando_inicio(ejecutable, proyecto, empaquetado=True)


def test_en_otro_so_el_import_y_la_ui_pueden_existir(monkeypatch):
    import nova.arranque_windows as mod
    monkeypatch.setattr(mod.sys, "platform", "linux")
    servicio = ArranqueWindows(Path("."))
    assert not servicio.compatible
    with pytest.raises(ErrorArranque, match="Windows"):
        servicio.guardar(True)


def test_despertar_invalida_descarga_pendiente():
    energia = Energia()
    assert energia.nivel == NivelEnergia.FONDO_PROFUNDO
    energia.activar()
    ticket = energia.dormir()
    assert energia.nivel == NivelEnergia.REPOSO_LIGERO and energia.vigente(ticket)
    energia.activar()
    assert not energia.vigente(ticket)
    energia.dormir()
    assert energia.nivel == NivelEnergia.REPOSO_LIGERO
    assert not energia.vigente(ticket)


def test_dormir_antes_de_hablar_conserva_fondo_profundo():
    energia = Energia()
    energia.dormir()
    assert energia.nivel == NivelEnergia.FONDO_PROFUNDO


def _eco_spawn(conexion, opciones):
    import sys

    from nova.voice.transcriptor import Transcripcion
    # El propio hijo prueba la propiedad que evita el conflicto nativo.
    conexion.send((True, "sin Qt" if "PyQt5.QtCore" not in sys.modules else "Qt cargado"))
    while True:
        senal = conexion.recv()
        if senal is None:
            break
        conexion.send((True, Transcripcion("nova abre notas")))
    conexion.close()


def test_stt_diferido_spawn_y_liberacion_real_del_proceso(monkeypatch):
    import numpy as np

    import nova.voice.diferido as mod
    monkeypatch.setattr(mod, "_motor", _eco_spawn)
    t = mod.TranscriptorDiferido(device="cpu")
    assert not t.cargado
    try:
        texto = t.transcribir(np.zeros(1600, dtype=np.float32))
        assert texto == "nova abre notas" and t.motor == "sin Qt"
        assert t.cargado
        assert t.descargar()
        assert not t.cargado
    finally:
        t.cerrar()


def test_candidato_falso_no_deja_stt_cargado(monkeypatch):
    from nova.voice.diferido import TranscriptorDiferido
    t = TranscriptorDiferido(device="cpu")
    descargas = []
    monkeypatch.setattr(t, "_descargar", lambda: descargas.append(1))
    t.confirmar_candidato(False)
    assert descargas == [1]
    t.confirmar_candidato(True)
    t.confirmar_candidato(False)
    assert descargas == [1]


def test_listener_no_carga_la_etapa_dos_al_iniciar(monkeypatch):
    import nova.voice.listener as mod
    from nova.voice.diferido import TranscriptorDiferido
    t = TranscriptorDiferido(device="cpu")
    def prohibido():
        pytest.fail("Se cargó Whisper al arrancar")
    monkeypatch.setattr(t, "cargar", prohibido)
    oyente = mod.VoiceListener(Path("no-existe"), transcriptor=t)
    monkeypatch.setattr(oyente, "_elegir_camino", lambda: object())
    monkeypatch.setattr(oyente.detector, "cargar", lambda: True)
    monkeypatch.setattr(mod, "medir_ruido", lambda *a, **kw: .001)
    escuchando = []
    monkeypatch.setattr(oyente, "_escuchar", lambda c: escuchando.append(1))
    oyente._run()
    assert escuchando == [1] and not t.cargado


def test_fondo_profundo_no_hace_consultas_desde_qt():
    from nova.app import Nova
    pendientes = []
    n = SimpleNamespace(_ocupada=False, _consulta_en_curso=False, energia=Energia(),
                         _consultar_recursos=SimpleNamespace(emit=lambda: pendientes.append(1)))
    Nova._revisar_recursos(n)
    assert pendientes == []
    n.energia.activar()
    Nova._revisar_recursos(n)
    Nova._revisar_recursos(n)
    assert pendientes == [1]


def test_mantenimiento_caducado_no_descarga_nada():
    from nova.app import _Worker
    energia = Energia()
    energia.activar()
    ticket = energia.dormir()
    energia.activar()
    trabajador = SimpleNamespace(energia=energia)
    # Sólo tiene energia: cualquier intento de liberar modelos produciría error.
    _Worker.mantenimiento(trabajador, ticket, True)


def test_puerta_de_energia_conserva_preroll_y_no_procesa_silencio():
    from nova.voice.puerta_energia import PuertaEnergia
    enviados = []
    detector = SimpleNamespace(escucha=lambda b: enviados.append(b) or False, reiniciar=lambda: None)
    puerta = PuertaEnergia(detector, 30)
    for _ in range(12):
        puerta.escucha(b"a", 0, 1)
    assert not enviados
    puerta.escucha(b"b", 2, 1)
    assert enviados == [b"a" * 9 + b"b"]


def test_tuerca_no_arrastra_y_permiso_denegado_se_muestra(tmp_path, monkeypatch):
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    from nova.ui.ajustes import Ajustes
    from nova.ui.panel import Panel
    app = QApplication.instance() or QApplication([])
    panel = Panel()
    avisos = []
    panel.abrir_ajustes.connect(lambda: avisos.append(1))
    posicion = panel.pos()
    QTest.mouseClick(panel.tuerca, Qt.LeftButton)
    assert avisos == [1] and panel.pos() == posicion and panel._arrastre is None
    registro = RegistroFalso()
    registro.denegar = True
    servicio = ArranqueWindows(tmp_path, registro=registro)
    monkeypatch.setattr(servicio, "comando_actual", lambda: "NOVA.exe")
    ventana = Ajustes(servicio)
    ventana.recargar()
    ventana.inicio.click()
    assert not ventana.inicio.isChecked()
    assert "denegó" in ventana.aviso.text()
    panel.close()
    ventana.close()
    app.processEvents()


def test_arranque_real_de_nova_solo_inicia_el_oyente(monkeypatch):
    from PyQt5.QtWidgets import QApplication

    import nova.app as mod
    import nova.recursos as recursos
    from nova.config import Config
    from nova.hardware import GIB, PerfilHardware
    from nova.voice.diferido import TranscriptorDiferido

    app = QApplication.instance() or QApplication([])

    def prohibido(*args, **kwargs):
        pytest.fail("El arranque profundo abrió un servicio pesado")

    monkeypatch.setattr(Config, "ensure_dirs", lambda self: None)
    monkeypatch.setattr(recursos, "_residencia_compartida", None)
    for herramienta in (mod.voz, mod.tool_plugins, mod.cerebro):
        monkeypatch.setattr(herramienta, "conectar", lambda *args: None)
    monkeypatch.setattr(mod, "perfilar", lambda: PerfilHardware(4, 2, 8 * GIB, ()))
    monkeypatch.setattr(mod.Interfaz, "_leer_estado_guardado", lambda self: {"colapsada": True})
    monkeypatch.setattr(mod.Interfaz, "_guardar_estado", lambda self: None)
    monkeypatch.setattr(ArranqueWindows, "reparar_ruta_registrada", lambda self: None)
    for metodo in ("available", "precalentar", "residencia_detallada", "descargar"):
        monkeypatch.setattr(mod.OllamaClient, metodo, prohibido)
    monkeypatch.setattr(mod.Speaker, "start", prohibido)
    monkeypatch.setattr(mod.Awareness, "start", prohibido)
    monkeypatch.setattr(mod.apps, "precalentar_indice", prohibido)
    oyentes = []
    monkeypatch.setattr(mod.VoiceListener, "start", lambda self: oyentes.append(self) or True)
    transcriptor = TranscriptorDiferido(device="cpu")
    nova = mod.Nova(app, transcriptor)
    try:
        nova.start()
        app.processEvents()
        assert len(oyentes) == 1 and not transcriptor.cargado
        assert nova.energia.nivel == NivelEnergia.FONDO_PROFUNDO
        assert not nova._worker._servicios_listos
        assert not nova._reloj.isActive() and not nova._latido.isActive()
        assert not nova.ui.orb._timer.isActive() and not nova.ui.panel._timer.isActive()
    finally:
        nova._hilo.quit()
        assert nova._hilo.wait(2000)
        nova.ui.cerrar()
        nova.llm.close()
        transcriptor.cerrar()
        app.processEvents()
