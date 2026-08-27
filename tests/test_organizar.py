"""Ordenar Descargas: lo que importa aquí es lo que NO hace.

Mueve 600 archivos del usuario de una vez. Cada test de abajo existe
porque el fallo correspondiente sería irreversible o muy molesto.
"""

from __future__ import annotations

import pytest

from nova.tools import organizar


@pytest.fixture
def descargas(tmp_path, monkeypatch):
    raiz = tmp_path / "Downloads"
    raiz.mkdir()
    for nombre in ("cancion.mp3", "foto.png", "manual.pdf", "juego.exe",
                   "pieza.3mf", "notas.txt", "raro.qwerty", "sinextension"):
        (raiz / nombre).write_text("x")
    # Accesos directos y archivos de sistema: intocables.
    (raiz / "atajo.lnk").write_text("x")
    (raiz / "desktop.ini").write_text("x")
    (raiz / ".oculto.png").write_text("x")
    # Una subcarpeta que YA estaba ordenada por el usuario.
    (raiz / "Mi carpeta").mkdir()
    (raiz / "Mi carpeta" / "dentro.mp3").write_text("x")

    monkeypatch.setattr(organizar, "_carpeta", lambda nombre="": raiz)
    monkeypatch.setattr(organizar, "_diario", lambda: tmp_path / "organizado.json")
    return raiz


# ── Lo que sí hace ───────────────────────────────────────────────────

def test_ordena_por_tipo(descargas):
    assert organizar.organizar().ok
    assert (descargas / "Música" / "cancion.mp3").exists()
    assert (descargas / "Imágenes" / "foto.png").exists()
    assert (descargas / "Documentos" / "manual.pdf").exists()
    assert (descargas / "Programas" / "juego.exe").exists()
    assert (descargas / "Modelos 3D" / "pieza.3mf").exists()


def test_revisar_no_toca_nada(descargas):
    """Es SAFE justamente porque no mueve: mirar no pide permiso."""
    antes = sorted(p.name for p in descargas.iterdir())
    r = organizar.revisar()
    assert r.ok
    assert sorted(p.name for p in descargas.iterdir()) == antes


# ── Lo que NO hace ───────────────────────────────────────────────────

def test_no_borra_nada(descargas):
    def contar(raiz):
        return sum(1 for p in raiz.rglob("*") if p.is_file())

    antes = contar(descargas)
    organizar.organizar()
    assert contar(descargas) == antes


def test_no_toca_los_accesos_directos(descargas):
    """Mover un .lnk del escritorio es justo lo que nadie quiere."""
    organizar.organizar()
    assert (descargas / "atajo.lnk").exists()


def test_no_toca_los_archivos_de_windows(descargas):
    organizar.organizar()
    assert (descargas / "desktop.ini").exists()


def test_no_toca_los_ocultos(descargas):
    organizar.organizar()
    assert (descargas / ".oculto.png").exists()


def test_no_entra_en_lo_que_ya_ordenaste_tu(descargas):
    organizar.organizar()
    assert (descargas / "Mi carpeta" / "dentro.mp3").exists()


def test_lo_que_no_reconoce_se_queda_quieto(descargas):
    """Sin carpeta «Otros»: un cajón de sastre mueve el problema, no lo
    resuelve."""
    organizar.organizar()
    assert (descargas / "raro.qwerty").exists()
    assert (descargas / "sinextension").exists()
    assert not (descargas / "Otros").exists()


def test_nunca_pisa_un_archivo_que_ya_estaba(descargas):
    destino = descargas / "Música"
    destino.mkdir()
    (destino / "cancion.mp3").write_text("EL BUENO")
    organizar.organizar()
    assert (destino / "cancion.mp3").read_text() == "EL BUENO"
    assert (destino / "cancion (2).mp3").exists()


# ── Deshacer ─────────────────────────────────────────────────────────

def test_se_puede_deshacer(descargas):
    antes = sorted(p.name for p in descargas.iterdir() if p.is_file())
    organizar.organizar()
    assert organizar.deshacer().ok
    assert sorted(p.name for p in descargas.iterdir() if p.is_file()) == antes


def test_deshacer_dos_veces_no_lía_nada(descargas):
    organizar.organizar()
    organizar.deshacer()
    r = organizar.deshacer()
    assert not r.ok
    assert "nada que deshacer" in r.message


def test_deshacer_respeta_lo_que_hayas_movido_tu(descargas):
    """Si después lo cambiaste de sitio, es tuyo: ni se toca ni se
    inventa nada."""
    organizar.organizar()
    (descargas / "Música" / "cancion.mp3").unlink()
    r = organizar.deshacer()
    assert r.ok
    assert "ya no estaban" in r.message


# ── Los límites ──────────────────────────────────────────────────────

def test_solo_descargas_y_escritorio():
    """«Ordena C:\\Windows» no es una orden que deba existir."""
    assert organizar._carpeta("C:/Windows") is None
    assert organizar._carpeta("mis fotos secretas") is None
    assert organizar._carpeta("") is None


def test_una_carpeta_ya_ordenada_no_es_un_error(descargas):
    organizar.organizar()
    r = organizar.organizar()
    assert r.ok
    assert "ya está ordenada" in r.message


def test_avisa_de_cuantos_antes_de_pedir_permiso(descargas):
    """«¿Confirmas que quiero ordenar descargas?» no avisa de nada."""
    frase = organizar._explicar({"carpeta": "descargas"})
    # mp3, png, pdf, exe, 3mf y txt.
    assert "6 archivos" in frase


def test_el_singular_esta_bien_escrito():
    assert organizar._plural(1, "archivo", "archivos") == "1 archivo"
    assert organizar._plural(2, "archivo", "archivos") == "2 archivos"
