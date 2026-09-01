"""NOVA como compañera de programación: mirar, ejecutar y probar.

Lo que se comprueba aquí es sobre todo lo que NO debe pasar: salirse de
la carpeta de proyectos, colgarse esperando a un script que no termina, y
ponerse a compilar mientras Benja está jugando.
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest

from nova.tools import codigo


@pytest.fixture
def proyectos(tmp_path, monkeypatch):
    """Dos proyectos de mentira, con la misma forma que los de verdad."""
    raiz = tmp_path / "proyectos"
    web = raiz / "01_Juegos_Web" / "battle-dither"
    web.mkdir(parents=True)
    (web / "index.html").write_text("<h1>hola</h1>\nconst jugador = 1;\n", encoding="utf-8")
    (web / "package.json").write_text('{"scripts": {"start": "vite"}}', encoding="utf-8")

    py = raiz / "09_Scripts" / "conversor"
    py.mkdir(parents=True)
    (py / "main.py").write_text("print('funciona')\n", encoding="utf-8")
    (py / "requirements.txt").write_text("", encoding="utf-8")

    # Ruido: lo que NUNCA hay que recorrer.
    hondo = web / "node_modules" / "cosa"
    hondo.mkdir(parents=True)
    (hondo / "package.json").write_text("{}", encoding="utf-8")

    escritorio = tmp_path / "Escritorio"
    escritorio.mkdir()
    monkeypatch.setattr(
        codigo, "CONFIG",
        SimpleNamespace(proyectos_dir=raiz, escritorio_dir=escritorio, codigo_timeout=15.0),
    )
    # Por defecto, PC libre: cada prueba que quiera un juego lo pone.
    monkeypatch.setattr(codigo, "_hay_sitio", lambda igualmente: None)
    return raiz


# ── Encontrar el proyecto ────────────────────────────────────────────

def test_encuentra_los_proyectos_sin_bajar_a_node_modules(proyectos):
    todos = codigo.proyectos()
    assert set(todos) == {"battledither", "conversor"}


def test_lo_encuentra_aunque_lo_diga_hablando(proyectos):
    """Por el micrófono llega «battle dither», no «battle-dither»."""
    assert codigo._resolver("battle dither").name == "battle-dither"
    assert codigo._resolver("BATTLE DITHER").name == "battle-dither"
    assert codigo._resolver("conversor").name == "conversor"


def test_lo_que_no_existe_se_dice_claro(proyectos):
    r = codigo.listar("un proyecto que no tengo")
    assert not r.ok
    assert "No encuentro" in r.message


# ── Mirar ────────────────────────────────────────────────────────────

def test_ver_un_archivo_por_nombre_suelto(proyectos):
    """«enséñame el main» — sin ruta, como se dice hablando."""
    r = codigo.ver("conversor", "main")
    assert r.ok
    assert "funciona" in r.message


def test_buscar_contesta_una_frase_que_se_pueda_decir(proyectos):
    """El detalle va en `data`; el mensaje lo va a leer NOVA en alto.

    Con `responde_sola`, el mensaje ES la respuesta hablada, y al pulirlo
    para voz se queda con la primera línea. Cuando era una lista de
    rutas, NOVA decía «pixijs aparece en 7 archivos:» y ahí se cortaba.
    """
    r = codigo.buscar("battle dither", "jugador")
    assert r.ok
    assert chr(10) not in r.message
    assert "1 archivo de battle-dither" in r.message   # ni «1 archivos»
    assert "index.html" in r.message
    # Y el archivo con su línea sigue estando, para quien lo necesite.
    assert r.data["hallazgos"] == ["index.html:2: const jugador = 1;"]


def test_ver_lo_que_no_esta_dice_lo_que_si(proyectos):
    """Sin la lista, el modelo se inventa otro nombre y vuelve a fallar."""
    r = codigo.ver("conversor", "main.tsx")
    assert not r.ok
    assert "main.py" in r.message


def test_ejecutar_sin_decir_que_no_pide_permiso_para_nada(proyectos):
    """«¿Confirmas que quiero ejecutar algo?» no es una pregunta válida."""
    r = codigo.ejecutar("conversor")
    assert not r.ok
    assert "Qué quieres que ejecute" in r.message


# ── Los límites ──────────────────────────────────────────────────────

def test_no_se_sale_de_la_carpeta_de_proyectos(proyectos):
    """Un modelo de 4B no puede acabar ejecutando algo de System32."""
    r = codigo.ejecutar("conversor", archivo="../../../../Windows/System32/calc.exe")
    assert not r.ok
    assert "No encuentro" in r.message


def test_dentro_rechaza_lo_de_fuera(proyectos, tmp_path):
    assert codigo._dentro(proyectos / "conversor" / "main.py")
    assert not codigo._dentro(tmp_path / "otra-cosa" / "x.py")


def test_un_script_que_no_termina_se_corta(proyectos, monkeypatch):
    """Sin esto NOVA se queda en «pensando» para siempre."""
    monkeypatch.setattr(
        codigo, "CONFIG",
        SimpleNamespace(proyectos_dir=proyectos, escritorio_dir=proyectos / "_desktop_falso",
                        codigo_timeout=1.0),
    )
    t0 = time.monotonic()
    r = codigo.ejecutar(
        "conversor",
        comando=f'"{sys.executable}" -c "import time; time.sleep(30)"',
    )
    tardado = time.monotonic() - t0

    assert not r.ok
    assert r.data.get("timeout")
    assert "cortado" in r.message
    # Y sobre todo: vuelve YA. Con `subprocess.run(timeout=...)` esto
    # tardaba los 30 segundos enteros, porque mataba el cmd y se quedaba
    # esperando al python de dentro. NOVA habría seguido en "pensando".
    assert tardado < 10, f"tardó {tardado:.1f}s en cortar algo con plazo de 1s"


# ── No estorbar mientras juega ───────────────────────────────────────

class _EstadoFalso:
    def __init__(self, modo: str, juego: str = "") -> None:
        self._modo, self.juego, self.cpu = modo, juego, 95.0

    def modo(self) -> str:
        return self._modo

    @property
    def hay_juego(self) -> bool:
        return bool(self.juego)


def test_con_un_juego_delante_dice_que_no(monkeypatch):
    """Correr los tests mientras juegas son fotogramas suyos, no de NOVA."""
    monkeypatch.setattr(
        codigo._vigilante, "estado",
        lambda *a, **k: _EstadoFalso("apretado", "StarCitizen"),
    )
    r = codigo._hay_sitio(igualmente=False)
    assert r is not None
    assert "StarCitizen" in r.message
    assert "igualmente" in r.message


def test_pero_si_insiste_lo_hace(monkeypatch):
    monkeypatch.setattr(
        codigo._vigilante, "estado",
        lambda *a, **k: _EstadoFalso("apretado", "StarCitizen"),
    )
    assert codigo._hay_sitio(igualmente=True) is None


def test_con_el_pc_libre_no_pregunta_nada(monkeypatch):
    monkeypatch.setattr(
        codigo._vigilante, "estado", lambda *a, **k: _EstadoFalso("holgado")
    )
    assert codigo._hay_sitio(igualmente=False) is None


# ── Leer el resultado de los tests ───────────────────────────────────

def test_resume_los_tests_en_espanol():
    assert "553 en verde" in codigo._resumir_tests("=== 553 passed in 8.62s ===")
    resumen = codigo._resumir_tests("=== 3 failed, 550 passed in 9s ===")
    assert "3 fallando" in resumen
    assert "550 en verde" in resumen


def test_no_confunde_el_nombre_de_un_test_con_el_resultado():
    """`passed` aparece en el nombre de un test; la cuenta está al final."""
    salida = "tests/test_x.py::test_que_paso_cuando_passed PASSED\n1 passed in 0.1s\n"
    assert codigo._resumir_tests(salida) == "1 en verde."


def test_un_proyecto_sin_tests_lo_dice(proyectos):
    r = codigo.probar("battle dither")
    assert not r.ok
    assert "no tiene tests" in r.message


# ── Decir el fallo, no leer el traceback ─────────────────────────────
#
# Probándolo en vivo, NOVA leyó en alto veinte líneas de andamiaje de
# importación de Python. De todas ellas, la única que dice algo es el
# ImportError del final.

_SALIDA_PYTEST = """=================================== ERRORS ====================================
___________________ ERROR collecting tests/test_tooling.py ____________________
ImportError while importing test module 'C:/x/tests/test_tooling.py'.
Traceback:
  return _bootstrap._gcd_import(name[level:], package, level)
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_tooling.py:4: in <module>
    import tools
tools.py:9: in <module>
    from memory import MemoryManager
E   ModuleNotFoundError: No module named 'chromadb'
=========================== short test summary info ===========================
ERROR tests/test_tooling.py
"""


def test_del_traceback_solo_se_queda_lo_que_explica():
    dicho = codigo._por_que_falla(_SALIDA_PYTEST)
    assert "ModuleNotFoundError" in dicho
    assert "chromadb" in dicho
    # Y nada del andamiaje.
    assert "_gcd_import" not in dicho
    assert "^^^" not in dicho
    assert len(dicho.splitlines()) <= 6


def test_un_assert_que_falla_tambien_se_reconoce():
    salida = "tests/test_x.py:12: in test_suma\n    assert 2 + 2 == 5\nE   assert 4 == 5\n"
    assert "assert 4 == 5" in codigo._por_que_falla(salida)


def test_si_no_reconoce_nada_dice_lo_que_haya():
    """Peor que leer de más es no decir nada."""
    assert codigo._por_que_falla("se rompió y no sé por qué") == "se rompió y no sé por qué"


# ── Escribir código ──────────────────────────────────────────────────
#
# Hasta el 01/09 NOVA contestaba «no sé programar ni hacer juegos», y era
# verdad: no tenía forma de crear un archivo fuera de workspace/. Pedirle
# un juego terminaba en una carpeta vacía.

def test_escribe_un_archivo_en_un_proyecto_que_ya_existe(proyectos):
    r = codigo.escribir("conversor", "juego.html", "<canvas id='j'></canvas>")
    assert r.ok
    assert (proyectos / "09_Scripts" / "conversor" / "juego.html").is_file()


def test_crea_el_proyecto_si_no_existe(proyectos):
    """«hazme un juego de la serpiente» no puede exigir que ya hubiera carpeta."""
    r = codigo.escribir("serpiente", "index.html", "<h1>snake</h1>")
    assert r.ok
    assert "He creado el proyecto" in r.message
    assert (proyectos / "serpiente" / "index.html").read_text(encoding="utf-8") == "<h1>snake</h1>"


def test_no_machaca_nada_sin_permiso(proyectos):
    """Un modelo que se equivoca de nombre y borra tu index.html es un
    fallo del que no se vuelve: aquí no hay git que lo salve."""
    r = codigo.escribir("battle dither", "index.html", "otra cosa")
    assert not r.ok
    assert "Ya existe" in r.message
    # Y el original sigue intacto.
    original = (proyectos / "01_Juegos_Web" / "battle-dither" / "index.html")
    assert "hola" in original.read_text(encoding="utf-8")


def test_sobrescribe_si_se_lo_dicen(proyectos):
    r = codigo.escribir("battle dither", "index.html", "nuevo", sobrescribir=True)
    assert r.ok
    original = (proyectos / "01_Juegos_Web" / "battle-dither" / "index.html")
    assert original.read_text(encoding="utf-8") == "nuevo"


def test_no_escribe_ejecutables(proyectos):
    """Un modelo hablando por un micro no deja un .bat en el disco."""
    for malo in ("virus.exe", "cosa.bat", "algo.ps1", "x.dll"):
        r = codigo.escribir("conversor", malo, "lo que sea")
        assert not r.ok, malo
        assert "Sólo código y texto" in r.message


def test_escribir_tampoco_se_sale_de_la_carpeta(proyectos, tmp_path):
    r = codigo.escribir("conversor", "../../../../fuera.html", "<h1>no</h1>")
    assert not r.ok
    assert not (tmp_path.parent / "fuera.html").exists()


# ── Leer mucho código ────────────────────────────────────────────────

def test_ver_numera_las_lineas(proyectos):
    """Sin número, pedir el tramo siguiente es adivinar."""
    r = codigo.ver("battle dither", "index.html")
    assert r.ok
    assert "1: <h1>hola</h1>" in r.message


def test_ver_un_tramo_concreto(proyectos):
    r = codigo.ver("battle dither", "index.html", desde=2, hasta=2)
    assert r.ok
    assert "2: const jugador = 1;" in r.message
    assert "hola" not in r.message
    assert "líneas 2-2 de 2" in r.message


def test_pedir_un_tramo_que_no_existe_lo_dice(proyectos):
    r = codigo.ver("battle dither", "index.html", desde=500)
    assert not r.ok
    assert "sólo tiene 2 líneas" in r.message


def test_un_archivo_enorme_dice_dónde_se_cortó(proyectos, monkeypatch):
    """Para poder pedir el tramo siguiente en vez de opinar sobre la mitad."""
    monkeypatch.setattr(codigo, "CARACTERES_ARCHIVO", 200)
    largo = proyectos / "09_Scripts" / "conversor" / "largo.py"
    largo.write_text("\n".join(f"linea_{i} = {i}" for i in range(1, 300)), encoding="utf-8")

    r = codigo.ver("conversor", "largo.py")
    assert r.ok
    assert r.data["lineas"] == 299
    assert "cortado en la línea" in r.message
    assert "desde y hasta" in r.message


def test_al_escribir_no_vale_un_parecido_lejano(proyectos):
    """El fallo en vivo: «un proyecto que se llame serpiente-nova»
    acabó escribiendo dentro del proyecto NOVA, porque normalizado
    "nova" está contenido en "serpientenova"."""
    nova_vieja = proyectos / "NOVA"
    nova_vieja.mkdir()
    (nova_vieja / "main.py").write_text("# proyecto de verdad", encoding="utf-8")

    # Leyendo, el emparejamiento flojo es una comodidad.
    assert codigo._resolver("nova").name == "NOVA"
    # Escribiendo, no: «serpiente-nova» NO es «NOVA».
    assert codigo._resolver("serpiente-nova", estricto=True) is None

    r = codigo.escribir("serpiente-nova", "index.html", "<h1>snake</h1>")
    assert r.ok
    assert "He creado el proyecto" in r.message
    assert (proyectos / "serpiente-nova" / "index.html").is_file()
    # Y el proyecto que ya existía se queda como estaba.
    assert not (nova_vieja / "index.html").exists()


def test_lo_que_si_encaja_sigue_encajando_al_escribir(proyectos):
    """«nodika» tiene que seguir encontrando «nodika-motor»."""
    (proyectos / "01_Juegos_Web" / "nodika-motor").mkdir()
    (proyectos / "01_Juegos_Web" / "nodika-motor" / "package.json").write_text(
        "{}", encoding="utf-8"
    )
    r = codigo.escribir("nodika", "nuevo.js", "console.log(1)")
    assert r.ok
    assert "He creado el proyecto" not in r.message
    assert (proyectos / "01_Juegos_Web" / "nodika-motor" / "nuevo.js").is_file()


# ── Editar en vez de reescribir ──────────────────────────────────────
#
# Con sólo `escribir`, un "cámbiale el color a la serpiente" obliga a
# regenerar las 120 líneas de memoria: veinte segundos, dos mil tokens, y
# se lleva por delante cualquier cosa que Benja hubiera tocado a mano.

def test_editar_cambia_solo_lo_pedido(proyectos):
    js = proyectos / "09_Scripts" / "conversor" / "juego.js"
    js.write_text('const color = "verde";\nconst velocidad = 5;\n', encoding="utf-8")

    r = codigo.editar("conversor", "juego.js", '"verde"', '"magenta"')
    assert r.ok
    # Lo pedido cambia...
    assert '"magenta"' in js.read_text(encoding="utf-8")
    # ...y lo demás se queda exactamente igual.
    assert "const velocidad = 5;" in js.read_text(encoding="utf-8")


def test_no_toca_nada_si_el_trozo_sale_varias_veces(proyectos):
    """Cambiar la primera de cinco deja el archivo a medias, y aquí no
    hay git que lo deshaga."""
    js = proyectos / "09_Scripts" / "conversor" / "repes.js"
    js.write_text("let x = 1;\nlet y = 1;\nlet z = 1;\n", encoding="utf-8")
    antes = js.read_text(encoding="utf-8")

    r = codigo.editar("conversor", "repes.js", "= 1;", "= 2;")
    assert not r.ok
    assert "3 veces" in r.message
    assert js.read_text(encoding="utf-8") == antes


def test_si_el_trozo_no_esta_lo_dice_sin_inventar(proyectos):
    r = codigo.editar("conversor", "main.py", "esto no existe", "x")
    assert not r.ok
    assert "no está" in r.message


def test_editar_sin_reemplazo_borra_el_trozo(proyectos):
    js = proyectos / "09_Scripts" / "conversor" / "sobra.js"
    js.write_text("bueno();\nconsole.log('depuracion');\nmas();\n", encoding="utf-8")

    r = codigo.editar("conversor", "sobra.js", "console.log('depuracion');\n")
    assert r.ok
    texto = js.read_text(encoding="utf-8")
    assert "depuracion" not in texto
    assert "bueno();" in texto and "mas();" in texto


def test_editar_tampoco_se_sale_de_la_carpeta(proyectos):
    r = codigo.editar("conversor", "../../../../algo.txt", "a", "b")
    assert not r.ok
    assert "No encuentro" in r.message


# ── "En el escritorio" va al Escritorio DE VERDAD ────────────────────
#
# En directo, el 02/09: pedido "en el escritorio", el modelo llamaba a
# `folder.create` -que escribe en nova/workspace/, invisible para
# Benja- y encima eso forzaba una SEGUNDA vuelta a la API para redactar
# la respuesta, que fue lo que agotó el resto del presupuesto de tokens
# de la nube. Estos tests cubren la ruta que reemplaza a ese camino.

def test_escritorio_crea_en_el_escritorio_real(proyectos):
    r = codigo.escribir("PongPrueba", "index.html", "<h1>pong</h1>", escritorio=True)
    assert r.ok
    assert r.data["escritorio"] is True
    destino = codigo.CONFIG.escritorio_dir / "PongPrueba" / "index.html"
    assert destino.is_file()
    assert "pong" in destino.read_text(encoding="utf-8")
    # Y NO ha tocado la carpeta normal de proyectos.
    assert not (proyectos / "PongPrueba").exists()


def test_sin_escritorio_sigue_yendo_a_proyectos_como_siempre(proyectos):
    r = codigo.escribir("normal", "index.html", "<h1>x</h1>")
    assert r.ok
    assert r.data["escritorio"] is False
    assert (proyectos / "normal" / "index.html").is_file()


def test_lo_del_escritorio_tambien_se_puede_leer_y_abrir(proyectos):
    codigo.escribir("PongPrueba", "index.html", "<h1>pong</h1>", escritorio=True)

    # _resolver lo encuentra sin haber pasado por escritorio=True otra vez.
    encontrado = codigo._resolver("pongprueba")
    assert encontrado == codigo.CONFIG.escritorio_dir / "PongPrueba"

    r = codigo.ver("PongPrueba", "index.html")
    assert r.ok
    assert "pong" in r.message


def test_el_escritorio_no_se_cuela_en_codigo_proyectos(proyectos):
    """Lo del Escritorio de Benja es suyo, no un catálogo de NOVA."""
    codigo.escribir("PongPrueba", "index.html", "<h1>pong</h1>", escritorio=True)
    todos = codigo.proyectos()
    assert "pongprueba" not in todos


def test_escritorio_tampoco_se_sale_de_si_mismo(proyectos):
    r = codigo.escribir("../../../../fuera", "x.html", "<h1>no</h1>", escritorio=True)
    # El nombre se limpia de caracteres raros, así que esto crea una
    # carpeta llamada literalmente sin los puntos y barras, no escapa.
    assert r.ok
    assert not (codigo.CONFIG.escritorio_dir.parent / "fuera").exists()
