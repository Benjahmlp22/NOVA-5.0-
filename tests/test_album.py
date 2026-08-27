"""Buscar imágenes por lo que se ve: el índice y el tokenizador.

Los modelos (607 MB) no hacen falta para casi nada de esto: lo que puede
romperse es el índice incremental, el filtro de lo que no merece la pena
mirar, y el umbral por debajo del cual no hay que decir que se ha
encontrado algo.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from nova.vista import album as mod
from nova.vista.album import Album, Progreso


def _direccion(n: float) -> np.ndarray:
    """Un vector unitario cuyo ÁNGULO depende del número que se le dé.

    Con ángulos, dos cosas distintas dan vectores de verdad distintos.
    Un vector tipo [n, 1, 0] normalizado no vale: para n grande todos
    apuntan casi al mismo sitio y el parecido sale 0.999 siempre, con lo
    que un test de umbral no probaría nada.
    """
    a = float(n)
    return np.array([np.cos(a), np.sin(a), 0.0], dtype=np.float32)


class _CodificadorFalso:
    """Un vector por imagen que depende sólo de su nombre.

    Así se puede comprobar que cada imagen guarda LO SUYO, que es el
    fallo silencioso que tendría este módulo: vectores traspuestos y
    búsquedas que devuelven cualquier cosa con total seguridad.
    """

    def __init__(self) -> None:
        self.vistas: list = []

    def de_imagenes(self, tensores):  # noqa: ANN001
        self.vistas.extend(tensores)
        return np.stack([_direccion(t.flat[0]) for t in tensores])

    def de_texto(self, frase):  # noqa: ANN001
        return _direccion(len(frase))


@pytest.fixture
def carpeta(tmp_path, monkeypatch):
    imagenes = tmp_path / "Pictures"
    imagenes.mkdir()
    # El contenido no importa: `clip.preparar` está falseado.
    for nombre in ("uno.png", "dos.jpg", "tres.webp"):
        (imagenes / nombre).write_bytes(b"x" * 20_000)
    # Pequeña: por debajo del mínimo, no se mira.
    (imagenes / "icono.png").write_bytes(b"x" * 500)
    # No es una imagen.
    (imagenes / "leeme.txt").write_bytes(b"x" * 20_000)

    monkeypatch.setattr(mod, "_raiz_album", lambda: tmp_path / "album")
    monkeypatch.setattr(
        mod.clip, "preparar",
        lambda ruta: np.full((3, 4, 4), float(len(ruta.name)), dtype=np.float32),
    )
    return imagenes


def _album(carpeta) -> Album:
    a = Album(carpetas=[carpeta])
    a._codificador = _CodificadorFalso()
    return a


# ── Qué se mira y qué no ─────────────────────────────────────────────

def test_mira_las_imagenes_y_nada_mas(carpeta):
    a = _album(carpeta)
    nombres = {p.name for p in a.pendientes()}
    assert nombres == {"uno.png", "dos.jpg", "tres.webp"}


def test_los_iconos_no_cuentan(carpeta):
    """Un archivo de 500 bytes es un icono o un botón, no una foto que
    alguien busque."""
    a = _album(carpeta)
    assert "icono.png" not in {p.name for p in a.pendientes()}


def test_no_entra_en_node_modules(tmp_path, carpeta):
    basura = carpeta / "node_modules"
    basura.mkdir()
    (basura / "logo.png").write_bytes(b"x" * 20_000)
    a = _album(carpeta)
    assert "logo.png" not in {p.name for p in a.pendientes()}


def test_el_escritorio_no_entra_por_defecto():
    """Aquí tiene 10.288 imágenes que son recursos de proyectos: dos
    tercios del tiempo de indexado a cambio de ruido."""
    nombres = {c.name.lower() for c in mod.carpetas_por_defecto()}
    assert "desktop" not in nombres
    assert "escritorio" not in nombres


# ── Indexar ──────────────────────────────────────────────────────────

def test_indexar_guarda_cada_imagen_con_su_vector(carpeta):
    a = _album(carpeta)
    assert a.indexar() == 3
    assert a.listo
    assert a.cuantas == 3
    # Cada una guarda LO SUYO: el falso codifica el nombre en el ángulo.
    for ruta, vector in zip(a._rutas, a._vectores, strict=True):
        esperado = _direccion(len(mod.Path(ruta).name))
        assert float(vector @ esperado) == pytest.approx(1.0, abs=1e-4)


def test_la_segunda_vez_no_repite_trabajo(carpeta):
    a = _album(carpeta)
    a.indexar()
    a._codificador.vistas.clear()
    assert a.indexar() == 0
    assert a._codificador.vistas == []


def test_una_imagen_cambiada_se_vuelve_a_mirar(carpeta):
    a = _album(carpeta)
    a.indexar()
    (carpeta / "uno.png").write_bytes(b"y" * 30_000)   # otro tamaño
    assert {p.name for p in a.pendientes()} == {"uno.png"}
    assert a.indexar() == 1
    assert a.cuantas == 3          # se reemplaza, no se duplica


def test_el_indice_sobrevive_al_reinicio(carpeta):
    a = _album(carpeta)
    a.indexar()
    otro = _album(carpeta)
    assert otro.cuantas == 3
    assert otro.listo


def test_un_indice_corrupto_no_tumba_nada(carpeta):
    a = _album(carpeta)
    a.indexar()
    (a._carpeta / "fichas.json").write_text("{esto no es json", encoding="utf-8")
    otro = _album(carpeta)
    assert not otro.listo
    assert otro.indexar() == 3


def test_un_indice_descuadrado_se_tira(carpeta):
    """Si las rutas y los vectores no cuadran, cada búsqueda devolvería
    la imagen equivocada con total seguridad."""
    a = _album(carpeta)
    a.indexar()
    datos = json.loads((a._carpeta / "fichas.json").read_text(encoding="utf-8"))
    datos["rutas"].append("inventada.png")
    (a._carpeta / "fichas.json").write_text(json.dumps(datos), encoding="utf-8")
    assert not _album(carpeta).listo


def test_se_puede_parar_a_medias(carpeta):
    a = _album(carpeta)
    a.indexar(parar=lambda: True)
    assert a.cuantas == 0


def test_una_imagen_ilegible_no_para_el_resto(carpeta, monkeypatch):
    """Un PNG corrupto entre 16.000 no puede tumbar el indexado."""
    original = mod.clip.preparar
    monkeypatch.setattr(
        mod.clip, "preparar",
        lambda ruta: None if ruta.name == "dos.jpg" else original(ruta),
    )
    a = _album(carpeta)
    assert a.indexar() == 2


def test_olvida_las_que_ya_no_estan(carpeta):
    a = _album(carpeta)
    a.indexar()
    (carpeta / "uno.png").unlink()
    assert a.olvidar_las_que_ya_no_estan() == 1
    assert a.cuantas == 2


# ── Buscar ───────────────────────────────────────────────────────────

def test_sin_indice_no_devuelve_nada(carpeta):
    assert _album(carpeta).buscar("lo que sea") == []


def test_devuelve_ordenado_por_parecido(carpeta):
    a = _album(carpeta)
    a.indexar()
    resultados = a.buscar("x" * 8, cuantas=3)
    assert resultados
    puntos = [c.parecido for c in resultados]
    assert puntos == sorted(puntos, reverse=True)


def test_siempre_trae_una_confianza(carpeta):
    """CLIP no sabe decir "no la tengo": sus números no son comparables
    entre consultas. Medido sobre 3.793 imágenes reales, "an underwater
    photo of a coral reef" (que no había) sacó 0.266 y "a screenshot of
    Minecraft" (que sí) sacó 0.300.

    Por eso no se filtra en silencio: cada resultado dice cuánta
    confianza merece y NOVA lo repite en voz alta."""
    a = _album(carpeta)
    a.indexar()
    for c in a.buscar("x" * 8, cuantas=3):
        assert 0.0 <= c.confianza <= 1.0
        assert c.segura == (c.confianza >= mod.CONFIANZA_SEGURA)


def test_lo_que_encaja_sale_con_mas_confianza_que_lo_que_no(carpeta):
    a = _album(carpeta)
    a.indexar()
    # El falso codifica el nombre en el ángulo: una consulta del mismo
    # largo que un nombre apunta justo a esa imagen.
    justo = a.buscar("x" * len("uno.png"), cuantas=1)[0]
    lejos = a.buscar("x" * 40, cuantas=1)[0]
    assert justo.confianza > lejos.confianza


# ── Progreso ─────────────────────────────────────────────────────────

def test_el_porcentaje_no_revienta_sin_nada_que_hacer():
    assert Progreso().porcentaje == 0
    assert Progreso(hechas=3, total=4).porcentaje == 75


# ── El tokenizador de CLIP ───────────────────────────────────────────
#
# Escrito a mano para no traerse `transformers` (que arrastra torch, 2.5
# GB, para ejecutar un modelo ya entrenado). Si esto se desvía, los
# vectores de texto salen mal y las búsquedas devuelven cualquier cosa
# sin que nada falle a la vista.

@pytest.fixture
def con_modelos():
    from nova.vista import clip
    if not clip.hay_modelos():
        pytest.skip("no están descargados los modelos de CLIP")
    return clip


def test_los_tokens_van_entre_las_marcas_de_principio_y_fin(con_modelos):
    ids = con_modelos.tokenizar("a photo of a dog")[0].tolist()
    assert ids[:6] == [49406, 320, 1125, 539, 320, 1929]
    assert ids[6] == 49407


def test_siempre_devuelve_el_contexto_completo(con_modelos):
    for frase in ("", "hola", "a " * 200):
        assert con_modelos.tokenizar(frase).shape == (1, con_modelos.CONTEXTO)


def test_una_frase_larguisima_conserva_la_marca_de_fin(con_modelos):
    """Sin ella el modelo no sabe dónde acaba la frase y el vector sale
    mal, sin que nada avise."""
    ids = con_modelos.tokenizar("perro " * 300)[0].tolist()
    assert ids[-1] == 49407


def test_los_acentos_no_lo_rompen(con_modelos):
    assert con_modelos.tokenizar("un cañón en Añover")[0][0] == 49406
