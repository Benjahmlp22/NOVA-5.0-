"""Plugins: el manifiesto, la revisión del código y el gestor.

Lo que más importa aquí no es que los plugins funcionen: es que **no se
ejecute nada que el usuario no haya activado a sabiendas**. Casi todos
los tests de abajo son de eso.
"""

from __future__ import annotations

import json

import pytest

from nova.plugins import gestor as mod
from nova.plugins import revisar
from nova.plugins.manifiesto import leer


def _escribir(carpeta, datos: dict, codigo: str = "") -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "plugin.json").write_text(json.dumps(datos, ensure_ascii=False),
                                         encoding="utf-8")
    if codigo:
        (carpeta / "plugin.py").write_text(codigo, encoding="utf-8")


@pytest.fixture
def taller(tmp_path, monkeypatch):
    """Un sitio donde inventar plugins sin tocar los de verdad."""
    incluidos = tmp_path / "plugins"
    usuario = tmp_path / "data" / "plugins"
    incluidos.mkdir(parents=True)
    usuario.mkdir(parents=True)
    monkeypatch.setattr(mod, "carpeta_incluidos", lambda: incluidos)
    monkeypatch.setattr(mod, "carpeta_usuario", lambda: usuario)
    monkeypatch.setattr(mod, "_archivo_estado", lambda: tmp_path / "plugins.json")
    return incluidos, usuario


# ── Leer el manifiesto ───────────────────────────────────────────────

def test_una_carpeta_sin_manifiesto_no_es_un_plugin(tmp_path):
    (tmp_path / "cualquiera").mkdir()
    assert leer(tmp_path / "cualquiera") is None


def test_un_manifiesto_roto_se_lee_como_plugin_con_errores(tmp_path):
    """No es lo mismo «no es un plugin» que «es un plugin y está mal»:
    lo segundo hay que enseñárselo al usuario."""
    carpeta = tmp_path / "roto"
    carpeta.mkdir()
    (carpeta / "plugin.json").write_text("{esto no es json", encoding="utf-8")
    plugin = leer(carpeta)
    assert plugin is not None
    assert not plugin.valido


def test_le_faltan_campos_obligatorios(tmp_path):
    carpeta = tmp_path / "pelado"
    _escribir(carpeta, {"id": "pelado"})
    plugin = leer(carpeta)
    assert not plugin.valido
    assert any("nombre" in e for e in plugin.errores)


def test_un_permiso_inventado_es_un_error(tmp_path):
    carpeta = tmp_path / "listo"
    _escribir(carpeta, {"id": "x", "nombre": "X", "descripcion": "d",
                        "permisos": ["personalidad", "hackear"]})
    plugin = leer(carpeta)
    assert not plugin.valido
    assert "hackear" not in plugin.permisos


def test_lo_que_hace_tiene_que_estar_declarado(tmp_path):
    """Cambiar la personalidad sin pedir ese permiso no es malicioso,
    pero hace que la lista de permisos mienta — y esa lista es lo único
    que el usuario va a leer."""
    carpeta = tmp_path / "callado"
    _escribir(carpeta, {"id": "x", "nombre": "X", "descripcion": "d",
                        "permisos": [], "personalidad": "sé un pirata"})
    assert not leer(carpeta).valido


def test_traer_codigo_sin_pedir_el_permiso_es_un_error(tmp_path):
    carpeta = tmp_path / "furtivo"
    _escribir(carpeta, {"id": "x", "nombre": "X", "descripcion": "d", "permisos": []},
              codigo="HERRAMIENTAS = []")
    plugin = leer(carpeta)
    assert not plugin.valido
    assert plugin.tiene_codigo


def test_uno_de_solo_datos_no_es_delicado(tmp_path):
    carpeta = tmp_path / "bueno"
    _escribir(carpeta, {"id": "x", "nombre": "X", "descripcion": "d",
                        "permisos": ["personalidad"], "personalidad": "sé breve"})
    plugin = leer(carpeta)
    assert plugin.valido
    assert not plugin.es_delicado


@pytest.mark.parametrize("permiso", ["archivos", "red", "microfono", "sistema"])
def test_los_permisos_delicados_se_marcan(tmp_path, permiso):
    carpeta = tmp_path / f"p{permiso}"
    _escribir(carpeta, {"id": "x", "nombre": "X", "descripcion": "d",
                        "permisos": [permiso]})
    assert leer(carpeta).es_delicado


# ── La revisión del código ───────────────────────────────────────────

@pytest.mark.parametrize("codigo,esperado", [
    ("import subprocess", "subprocess"),
    ("import socket", "socket"),
    ("from shutil import rmtree", "shutil"),
    ("import httpx", "httpx"),
])
def test_avisa_de_los_modulos_con_poder(tmp_path, codigo, esperado):
    archivo = tmp_path / "plugin.py"
    archivo.write_text(codigo, encoding="utf-8")
    revision = revisar(archivo)
    assert any(esperado in h.que for h in revision.hallazgos)


@pytest.mark.parametrize("codigo", [
    "eval('1+1')",
    "exec('x=1')",
    "import shutil\nshutil.rmtree('/')",
    "getattr(__builtins__, 'e' + 'val')",
])
def test_lo_que_puede_hacer_dano_sale_como_grave(tmp_path, codigo):
    archivo = tmp_path / "plugin.py"
    archivo.write_text(codigo, encoding="utf-8")
    assert revisar(archivo).graves


def test_el_ofuscado_de_manual_tambien_se_caza(tmp_path):
    """`getattr(__builtins__, "e" + "val")` es el truco típico para
    llegar a eval sin escribirlo. La primera versión se lo tragaba:
    ahí __builtins__ es un Name, no un Attribute."""
    archivo = tmp_path / "plugin.py"
    archivo.write_text('getattr(__builtins__, "e" + "val")("1+1")', encoding="utf-8")
    graves = revisar(archivo).graves
    assert len(graves) >= 2      # el getattr calculado y el __builtins__


def test_un_plugin_normal_sale_limpio(tmp_path):
    archivo = tmp_path / "plugin.py"
    archivo.write_text(
        "from pathlib import Path\n"
        "def hola(nombre):\n"
        "    return {'ok': True, 'message': f'hola {nombre}'}\n"
        "HERRAMIENTAS = [{'name': 'hola', 'handler': hola}]\n",
        encoding="utf-8")
    assert revisar(archivo).limpio


def test_un_codigo_que_no_compila_no_revienta(tmp_path):
    archivo = tmp_path / "plugin.py"
    archivo.write_text("def roto(:", encoding="utf-8")
    revision = revisar(archivo)
    assert revision.error
    assert "no pude leer" in revision.resumen().lower()


def test_no_se_confunde_con_un_comentario(tmp_path):
    """Por eso se analiza el árbol sintáctico y no el texto: buscando
    palabras, cualquier comentario que mencione eval daría un susto."""
    archivo = tmp_path / "plugin.py"
    archivo.write_text('# nunca uses eval() ni subprocess\nx = 1\n', encoding="utf-8")
    assert revisar(archivo).limpio


# ── El gestor ────────────────────────────────────────────────────────

def test_encuentra_los_de_las_dos_carpetas(taller):
    incluidos, usuario = taller
    _escribir(incluidos / "uno", {"id": "uno", "nombre": "Uno", "descripcion": "d"})
    _escribir(usuario / "dos", {"id": "dos", "nombre": "Dos", "descripcion": "d"})
    assert {p.id for p in mod.Gestor().todos()} == {"uno", "dos"}


def test_el_del_usuario_reemplaza_al_incluido(taller):
    incluidos, usuario = taller
    _escribir(incluidos / "x", {"id": "x", "nombre": "De serie", "descripcion": "d"})
    _escribir(usuario / "x", {"id": "x", "nombre": "El mío", "descripcion": "d"})
    assert mod.Gestor().obtener("x").nombre == "El mío"


def test_recien_encontrado_esta_apagado(taller):
    """Lo importante de todo el módulo: nada se activa solo."""
    incluidos, _ = taller
    _escribir(incluidos / "uno", {"id": "uno", "nombre": "Uno", "descripcion": "d"})
    g = mod.Gestor()
    assert not g.esta_activo("uno")
    assert g.activos() == []


def test_activar_y_desactivar_sobrevive_al_reinicio(taller):
    incluidos, _ = taller
    _escribir(incluidos / "uno", {"id": "uno", "nombre": "Uno", "descripcion": "d"})
    g = mod.Gestor()
    g.activar("uno")
    assert mod.Gestor().esta_activo("uno")
    g.desactivar("uno")
    assert not mod.Gestor().esta_activo("uno")


def test_uno_roto_no_se_puede_activar(taller):
    incluidos, _ = taller
    _escribir(incluidos / "malo", {"id": "malo"})
    hecho, mensaje = mod.Gestor().activar("malo")
    assert not hecho
    assert "mal hecho" in mensaje


def test_si_un_activo_desaparece_deja_de_estar_activo(taller):
    """Si no, el panel diría que está corriendo algo que ya no existe."""
    incluidos, _ = taller
    carpeta = incluidos / "uno"
    _escribir(carpeta, {"id": "uno", "nombre": "Uno", "descripcion": "d"})
    mod.Gestor().activar("uno")

    (carpeta / "plugin.json").unlink()
    carpeta.rmdir()
    assert mod.Gestor().activos() == []


def test_se_encuentra_por_nombre_como_lo_diria_una_persona(taller):
    incluidos, _ = taller
    _escribir(incluidos / "may", {"id": "may", "nombre": "Mayordomo", "descripcion": "d"})
    g = mod.Gestor()
    assert g.obtener("mayordomo").id == "may"
    assert g.obtener("MAYORDOMO").id == "may"
    assert g.obtener("no existe") is None


# ── Lo que aportan ───────────────────────────────────────────────────

def test_solo_aportan_los_activos(taller):
    incluidos, _ = taller
    _escribir(incluidos / "uno", {"id": "uno", "nombre": "Uno", "descripcion": "d",
                                  "permisos": ["personalidad"], "personalidad": "sé breve"})
    g = mod.Gestor()
    assert g.personalidad() == ""
    g.activar("uno")
    assert "sé breve" in g.personalidad()


def test_dos_personalidades_se_suman(taller):
    incluidos, _ = taller
    for i in ("a", "b"):
        _escribir(incluidos / i, {"id": i, "nombre": i, "descripcion": "d",
                                  "permisos": ["personalidad"], "personalidad": f"rasgo {i}"})
    g = mod.Gestor()
    g.activar("a")
    g.activar("b")
    assert "rasgo a" in g.personalidad()
    assert "rasgo b" in g.personalidad()


def test_la_voz_la_pone_el_ultimo_que_la_pida(taller):
    incluidos, _ = taller
    _escribir(incluidos / "a", {"id": "a", "nombre": "a", "descripcion": "d",
                                "permisos": ["voz"], "voz": "Microsoft Pablo"})
    _escribir(incluidos / "b", {"id": "b", "nombre": "b", "descripcion": "d",
                                "permisos": ["personalidad"], "personalidad": "x"})
    g = mod.Gestor()
    g.activar("b")
    assert g.voz_preferida() == ""
    g.activar("a")
    assert g.voz_preferida() == "Microsoft Pablo"
