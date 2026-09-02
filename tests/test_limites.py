"""Los límites duros, todos juntos y en un solo sitio.

El README promete que NOVA no puede tocar el disco fuera de dos
carpetas. Esa promesa la hace un asistente que recibe órdenes de un
modelo de 4B a través de un micrófono, así que conviene que esté
demostrada y no sólo escrita.

Cada prueba de aquí es un intento de salirse. Ninguna debe conseguirlo.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nova.tools import codigo, files

# ── Las herramientas de código: proyectos y Escritorio ───────────────

@pytest.fixture
def cercado(tmp_path, monkeypatch):
    """Las dos carpetas permitidas, y una tercera prohibida al lado."""
    proyectos = tmp_path / "proyectos"
    (proyectos / "victima").mkdir(parents=True)
    (proyectos / "victima" / "index.html").write_text("original", encoding="utf-8")

    escritorio = tmp_path / "Escritorio"
    escritorio.mkdir()

    prohibida = tmp_path / "Windows" / "System32"
    prohibida.mkdir(parents=True)
    (prohibida / "importante.txt").write_text("no tocar", encoding="utf-8")

    monkeypatch.setattr(
        codigo, "CONFIG",
        SimpleNamespace(proyectos_dir=proyectos, escritorio_dir=escritorio,
                        codigo_timeout=5.0),
    )
    monkeypatch.setattr(codigo, "_hay_sitio", lambda igualmente: None)
    return SimpleNamespace(proyectos=proyectos, escritorio=escritorio,
                           prohibida=prohibida)


# Las formas de intentar escaparse que se le pueden ocurrir a un modelo
# escribiendo una ruta a partir de algo que oyó por un micrófono.
ESCAPES = [
    "../fuera.html",
    "../../fuera.html",
    "../../../../Windows/System32/fuera.html",
    "..\\..\\fuera.html",
    "subcarpeta/../../fuera.html",
]


@pytest.mark.parametrize("ruta", ESCAPES)
def test_escribir_no_sale_de_los_proyectos(cercado, ruta):
    codigo.escribir("victima", ruta, "<h1>dentro</h1>")
    # Da igual si contestó ok o no: lo que no puede es haber creado nada
    # fuera de la carpeta permitida.
    assert not list(cercado.proyectos.parent.glob("fuera.html"))
    assert not list(cercado.prohibida.glob("fuera.html"))


@pytest.mark.parametrize("ruta", ESCAPES)
def test_escribir_en_el_escritorio_tampoco_sale(cercado, ruta):
    codigo.escribir("carpeta", ruta, "<h1>dentro</h1>", escritorio=True)
    assert not list(cercado.escritorio.parent.glob("fuera.html"))
    assert not list(cercado.prohibida.glob("fuera.html"))


@pytest.mark.parametrize("ruta", ESCAPES)
def test_leer_no_sale_de_los_proyectos(cercado, ruta):
    r = codigo.ver("victima", ruta)
    assert not r.ok
    assert "no tocar" not in r.message


@pytest.mark.parametrize("ruta", ESCAPES)
def test_ejecutar_no_sale_de_los_proyectos(cercado, ruta):
    r = codigo.ejecutar("victima", archivo=ruta)
    assert not r.ok


def test_editar_no_sale_de_los_proyectos(cercado):
    r = codigo.editar("victima", "../../../../Windows/System32/importante.txt",
                      "no tocar", "TOCADO")
    assert not r.ok
    texto = (cercado.prohibida / "importante.txt").read_text(encoding="utf-8")
    assert texto == "no tocar"


def test_una_ruta_absoluta_tampoco_cuela(cercado):
    r = codigo.ver("victima", str(cercado.prohibida / "importante.txt"))
    assert not r.ok


# ── Lo que NUNCA se escribe, esté donde esté ─────────────────────────

@pytest.mark.parametrize("nombre", [
    "virus.exe", "arranque.bat", "algo.cmd", "carga.dll", "script.ps1",
    "instalador.msi", "cosa.scr", "x.vbs",
])
def test_no_escribe_nada_ejecutable(cercado, nombre):
    """Un modelo hablando por un micro no deja un .exe en el disco."""
    r = codigo.escribir("victima", nombre, "lo que sea")
    assert not r.ok
    assert not (cercado.proyectos / "victima" / nombre).exists()


def test_pero_el_codigo_normal_si_se_escribe(cercado):
    """El cerco no puede ser tan estrecho que no deje trabajar."""
    for nombre in ("nuevo.html", "juego.js", "main.py", "estilo.css", "datos.json"):
        r = codigo.escribir("victima", nombre, "contenido")
        assert r.ok, nombre


def test_no_machaca_lo_que_ya_estaba_sin_permiso(cercado):
    """Aquí no hay git que deshaga un fichero pisado por error."""
    r = codigo.escribir("victima", "index.html", "PISADO")
    assert not r.ok
    original = cercado.proyectos / "victima" / "index.html"
    assert original.read_text(encoding="utf-8") == "original"


# ── El sandbox de los apuntes internos ───────────────────────────────

@pytest.fixture
def apuntes(tmp_path, monkeypatch):
    dentro = tmp_path / "workspace"
    dentro.mkdir()
    monkeypatch.setattr(files, "CONFIG", SimpleNamespace(workspace=dentro))
    return dentro


@pytest.mark.parametrize("ruta", [
    "../fuera.txt", "../../fuera.txt", "C:\\Windows\\fuera.txt",
    "/etc/passwd", "sub/../../fuera.txt",
])
def test_los_apuntes_no_salen_de_su_carpeta(apuntes, ruta):
    files.create_file(ruta, "contenido")
    assert not list(apuntes.parent.glob("fuera.txt"))


def test_los_apuntes_normales_si_funcionan(apuntes):
    r = files.create_file("notas/hoy.txt", "hola")
    assert r.ok
    assert (apuntes / "notas" / "hoy.txt").read_text(encoding="utf-8") == "hola"


# ── Lo que pide permiso antes de pasar ───────────────────────────────

def test_lo_peligroso_siempre_confirma_pase_lo_que_pase():
    """`Risk.DANGEROUS` no lo salta ninguna política, ni la más laxa."""
    from nova.tools import build_registry
    from nova.tools.registry import Risk

    for politica in ("solo_peligroso", "estricto", "nunca", ""):
        reg = build_registry(politica)
        peligrosas = [reg.get(n) for n in reg.names()
                      if reg.get(n).risk is Risk.DANGEROUS]
        assert peligrosas, "alguna herramienta tiene que serlo"
        for tool in peligrosas:
            assert reg.needs_confirmation(tool), f"{tool.name} con política {politica!r}"


def test_ejecutar_codigo_y_borrar_estan_entre_las_peligrosas():
    """Si alguna de éstas deja de pedir permiso, es un fallo grave."""
    from nova.tools import build_registry
    from nova.tools.registry import Risk

    reg = build_registry()
    for nombre in ("codigo.ejecutar", "codigo.probar"):
        assert reg.get(nombre).risk is Risk.DANGEROUS, nombre
