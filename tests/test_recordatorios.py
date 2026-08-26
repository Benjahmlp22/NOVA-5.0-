"""Recordatorios: entender el "cuándo" y no interrumpir cuando no toca."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nova.tools import recordatorios as rec


@pytest.fixture
def almacen_temporal(tmp_path, monkeypatch):
    """Apunta el JSON a un temporal, sin tocar los del usuario."""
    monkeypatch.setattr(rec, "_path", lambda: tmp_path / "recordatorios.json")
    return tmp_path


AHORA = datetime(2026, 8, 27, 15, 30, 0)


# ── Entender cuándo ──────────────────────────────────────────────────

@pytest.mark.parametrize("frase,minutos", [
    ("en 10 minutos", 10),
    ("en 1 minuto", 1),
    ("dentro de 5 minutos", 5),
    ("en un minuto", 1),
    ("en media hora", 30),
    ("en 2 horas", 120),
])
def test_en_un_rato(frase, minutos):
    cuando, _alarma = rec.interpretar_cuando(frase, AHORA)
    assert cuando == AHORA + timedelta(minutes=minutos)


def test_un_rato_no_es_una_alarma():
    """"Recuérdame en 10 minutos" puede esperar a que le hables.

    Es la diferencia que hace útil todo esto: sólo lo que tiene hora
    fija te interrumpe.
    """
    _cuando, alarma = rec.interpretar_cuando("en 10 minutos", AHORA)
    assert not alarma


@pytest.mark.parametrize("frase,hora,minuto", [
    ("a las 8", 8, 0),
    ("a las 20:30", 20, 30),
    ("a las 8 de la tarde", 20, 0),
    ("a las 9 de la noche", 21, 0),
])
def test_a_una_hora_concreta(frase, hora, minuto):
    cuando, alarma = rec.interpretar_cuando(frase, AHORA)
    assert cuando.hour == hora
    assert cuando.minute == minuto
    assert alarma, "una hora fija SÍ interrumpe: para eso se pone"


def test_una_hora_que_ya_paso_es_de_manana():
    """Nadie pone una alarma para hace dos horas."""
    cuando, _ = rec.interpretar_cuando("a las 8", AHORA)   # son las 15:30
    assert cuando.date() == (AHORA + timedelta(days=1)).date()


def test_una_hora_que_no_ha_llegado_es_de_hoy():
    cuando, _ = rec.interpretar_cuando("a las 20:00", AHORA)
    assert cuando.date() == AHORA.date()


def test_manana_a_una_hora():
    cuando, _ = rec.interpretar_cuando("manana a las 20:00", AHORA)
    assert cuando.date() == (AHORA + timedelta(days=1)).date()


def test_lo_que_no_se_entiende_se_dice():
    """Inventar una hora sería peor que reconocer que no se entendió."""
    cuando, _ = rec.interpretar_cuando("cuando salga el sol", AHORA)
    assert cuando is None


def test_el_modelo_puede_mandar_una_fecha_iso():
    cuando, alarma = rec.interpretar_cuando("2026-08-28T09:00:00", AHORA)
    assert cuando == datetime(2026, 8, 28, 9, 0)
    assert alarma


# ── Cómo se cuenta ───────────────────────────────────────────────────

@pytest.mark.parametrize("delta,esperado", [
    (timedelta(seconds=30), "en menos de un minuto"),
    (timedelta(minutes=10), "en 10 minutos"),
    (timedelta(hours=3), "hoy a las 18:30"),
])
def test_describir_cuando(delta, esperado):
    assert rec.describir_cuando(AHORA + delta, AHORA) == esperado


def test_describir_manana():
    manana = (AHORA + timedelta(days=1)).replace(hour=8, minute=0)
    assert rec.describir_cuando(manana, AHORA) == "mañana a las 08:00"


# ── Guardar, vencer, cancelar ────────────────────────────────────────

def test_crear_y_listar(almacen_temporal):
    r = rec.crear("sacar la basura", "en 10 minutos")
    assert r.ok
    assert "10 minutos" in r.message
    assert "sacar la basura" in rec.listar().message


def test_sin_nada_apuntado_lo_dice(almacen_temporal):
    assert "no tienes nada" in rec.listar().message.lower()


def test_no_se_apunta_algo_del_pasado(almacen_temporal):
    r = rec.crear("algo", "2020-01-01T00:00:00")
    assert not r.ok
    assert "ya ha pasado" in r.message


def test_sin_texto_pregunta(almacen_temporal):
    assert not rec.crear("", "en 10 minutos").ok


def test_un_cuando_ininteligible_no_se_apunta(almacen_temporal):
    r = rec.crear("algo", "cuando termine la partida")
    assert not r.ok
    assert "no he entendido" in r.message.lower()


def test_vence_cuando_toca(almacen_temporal):
    rec.crear("mirar el horno", "en 1 minuto")
    assert rec.pendientes(datetime.now()) == []
    vencidos = rec.pendientes(datetime.now() + timedelta(minutes=2))
    assert len(vencidos) == 1
    assert vencidos[0].texto == "mirar el horno"


def test_lo_ya_avisado_no_vuelve(almacen_temporal):
    """Repetir un recordatorio cada cinco segundos sería una tortura."""
    rec.crear("mirar el horno", "en 1 minuto")
    despues = datetime.now() + timedelta(minutes=2)
    rec.marcar_avisados(["mirar el horno"])
    assert rec.pendientes(despues) == []


def test_cancelar(almacen_temporal):
    rec.crear("llamar al banco", "en 10 minutos")
    assert rec.olvidar("banco").ok
    assert "no tienes nada" in rec.listar().message.lower()


def test_cancelar_algo_que_no_existe_lo_dice(almacen_temporal):
    assert not rec.olvidar("lo que sea").ok


def test_un_json_roto_no_tumba_nada(almacen_temporal):
    (almacen_temporal / "recordatorios.json").write_text("{roto", encoding="utf-8")
    assert rec.cargar() == []
    assert rec.crear("algo", "en 5 minutos").ok
