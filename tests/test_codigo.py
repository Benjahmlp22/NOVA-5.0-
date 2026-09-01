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

    monkeypatch.setattr(
        codigo, "CONFIG", SimpleNamespace(proyectos_dir=raiz, codigo_timeout=15.0)
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
        codigo, "CONFIG", SimpleNamespace(proyectos_dir=proyectos, codigo_timeout=1.0)
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
