"""Mirar y abrir tus carpetas.

Cada caso sale de una frase que NOVA no supo resolver: «abre la carpeta
de descargas» llamaba a folder.create y CREABA una, «cuántas descargas
tengo» miraba workspace/, y «cuál es el archivo más grande» no tenía
forma de saberlo.
"""

from __future__ import annotations

import time

import pytest

from nova.tools import carpetas


@pytest.fixture
def descargas(tmp_path, monkeypatch):
    raiz = tmp_path / "Downloads"
    (raiz / "una carpeta").mkdir(parents=True)
    (raiz / "grande.zip").write_bytes(b"x" * 5_000_000)
    (raiz / "mediano.pdf").write_bytes(b"x" * 2_000_000)
    (raiz / "pequeno.txt").write_bytes(b"x" * 1_000)
    (raiz / "otro.pdf").write_bytes(b"x" * 500)
    # Uno viejo, de hace un mes.
    viejo = raiz / "antiguo.mp3"
    viejo.write_bytes(b"x" * 3_000)
    hace_un_mes = time.time() - 30 * 86400
    import os
    os.utime(viejo, (hace_un_mes, hace_un_mes))
    monkeypatch.setattr(carpetas, "_resolver", lambda nombre="": raiz)
    return raiz


# ── Resumen ──────────────────────────────────────────────────────────

def test_cuenta_archivos_carpetas_y_tamano(descargas):
    r = carpetas.resumen("descargas")
    assert r.ok
    assert r.data["archivos"] == 5
    assert "1 carpetas" in r.message or "1 carpeta" in r.message
    assert "megas" in r.message


def test_dice_de_qué_tipo_son(descargas):
    assert "pdf" in carpetas.resumen("descargas").message


def test_no_entra_en_las_subcarpetas(descargas):
    """Lo de dentro de una subcarpeta ya lo ordenaste tú."""
    (descargas / "una carpeta" / "escondido.zip").write_bytes(b"x" * 9_000_000)
    assert carpetas.resumen("descargas").data["archivos"] == 5


# ── Lo más grande ────────────────────────────────────────────────────

def test_el_mas_grande_va_primero(descargas):
    mensaje = carpetas.mas_grandes("descargas").message
    assert mensaje.index("grande.zip") < mensaje.index("mediano.pdf")


# ── Lo reciente ──────────────────────────────────────────────────────

def test_lo_viejo_no_es_reciente(descargas):
    assert "antiguo.mp3" not in carpetas.recientes("descargas", dias=1).message


def test_con_mas_dias_ya_entra(descargas):
    assert "antiguo.mp3" in carpetas.recientes("descargas", dias=60).message


def test_una_carpeta_sin_nada_nuevo_no_es_un_error(tmp_path, monkeypatch):
    vacia = tmp_path / "Downloads"
    vacia.mkdir()
    monkeypatch.setattr(carpetas, "_resolver", lambda nombre="": vacia)
    r = carpetas.recientes("descargas")
    assert r.ok
    assert "no ha llegado nada" in r.message.lower()


# ── Los límites ──────────────────────────────────────────────────────

def test_solo_las_carpetas_conocidas():
    r"""«Abre C:\Windows\System32» no es una orden que deba existir."""
    assert carpetas._resolver("C:/Windows/System32") is None
    assert carpetas._resolver("la nasa") is None
    assert carpetas._resolver("") is None


def test_una_carpeta_que_no_conoce_lo_dice_y_ofrece_las_que_si(monkeypatch):
    monkeypatch.setattr(carpetas, "_resolver", lambda nombre="": None)
    r = carpetas.resumen("la nasa")
    assert not r.ok
    assert "Descargas" in r.message


# ── Cómo se dicen los tamaños ────────────────────────────────────────

@pytest.mark.parametrize("bytes_,esperado", [
    (1_800_000_000, "1.8 gigas"),
    (5_000_000, "5 megas"),
    (2_000, "2 kilobytes"),
    (10, "1 kilobytes"),        # nada se dice en bytes: no se retiene
])
def test_los_tamanos_se_dicen_como_los_diria_una_persona(bytes_, esperado):
    assert carpetas._tamano(bytes_) == esperado
