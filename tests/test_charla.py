"""Saber cuándo no hay nada que hacer.

Existe porque, medido con el modelo real y el catálogo delante, "adiós"
llamaba a `memory.forget` — que borra cosas — y "hola" pedía tres
herramientas de estado a la vez. El prompt ya lo prohibía; no bastó.

El detector es deliberadamente conservador: un falso positivo (tomar una
orden por charla) deja a NOVA sin hacer lo que le pediste, que es mucho
peor que ofrecer herramientas de más.
"""

from __future__ import annotations

import pytest

from nova.core.charla import es_pura_charla


@pytest.mark.parametrize("frase", [
    "hola", "Hola", "  hola  ", "holi", "buenas", "hey",
    "buenos días", "buenas tardes", "qué tal", "cómo estás", "cómo te va",
    "gracias", "muchas gracias", "mil gracias", "gracias nova",
    "adiós", "hasta luego", "chao", "nos vemos", "me voy",
    "vale", "ok", "okey", "perfecto", "genial", "entendido",
    "nada", "no, nada", "ninguna", "no",
    "quién eres", "cómo te llamas",
    "hola nova", "vale gracias", "muchas gracias nova", "vale, adiós",
])
def test_esto_es_charla(frase):
    assert es_pura_charla(frase)


@pytest.mark.parametrize("frase", [
    "abre Discord",
    "hola, abre Discord",
    "gracias por abrir Chrome",
    "qué hora es",
    "dime la hora",
    "cierra Spotify",
    "ponte voz de hombre",
    "busca en internet el precio de la 5070",
    "no abras nada",
    "quién eres tú y qué haces en mi PC",
    "olvida lo que sabes de mí",
    "recuérdame comprar pan",
    "vale, ahora ordena mis descargas",
])
def test_esto_es_una_orden(frase):
    assert not es_pura_charla(frase)


def test_una_frase_larga_nunca_es_charla():
    """El tope de palabras es la red de seguridad: una orden de verdad no
    cabe en cinco palabras de la lista."""
    assert not es_pura_charla("hola hola hola hola hola hola hola")


def test_ni_lo_vacio_ni_lo_raro_revientan():
    assert not es_pura_charla("")
    assert not es_pura_charla("   ")
    assert not es_pura_charla("???")
    assert not es_pura_charla(None)  # type: ignore[arg-type]
