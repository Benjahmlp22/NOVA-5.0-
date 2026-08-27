"""Saber cuándo NO hay nada que hacer, sólo algo que contestar.

Un "hola", un "gracias" o un "adiós" no necesitan ninguna herramienta.
Parece obvio, pero medido con el modelo real y el catálogo entero:

    «adiós»          -> memory.forget      (¡borrar cosas!)
    «hola»           -> pc.status, pc.active_window, pc.running_apps
    «qué tal el día» -> pc.status

Y no es culpa de tener muchas herramientas: con las 27 de antes pasaba
exactamente igual (17/20 aciertos en los dos casos).  Es que un modelo
pequeño, si le pones un catálogo delante, quiere usarlo.

Así que se aplica la misma regla que al relleno y a los permisos: **con
código, no con el prompt**.  Si lo que has dicho es charla y nada más,
al modelo se le pregunta SIN herramientas, y entonces le es imposible
llamar a ninguna.

Conservador a propósito.  Ante la duda, NO es charla: dejar sin hacer
una orden de verdad es mucho peor que ofrecer herramientas de más.
"""

from __future__ import annotations

import re
import unicodedata

# Como mucho estas palabras. Una orden de verdad casi nunca cabe en tan
# poco ("abre Discord" son dos, pero no está en ninguna lista de abajo).
MAX_PALABRAS = 5

_SALUDOS = (
    "hola", "holi", "buenas", "hey", "ey", "buenos dias", "buenas tardes",
    "buenas noches", "que tal", "que pasa", "que hay", "como estas",
    "como te va", "que tal el dia", "como vas", "todo bien",
)
_GRACIAS = (
    "gracias", "muchas gracias", "mil gracias", "gracias nova",
    "te lo agradezco", "muy amable", "grande",
)
_DESPEDIDAS = (
    "adios", "hasta luego", "hasta mañana", "chao", "chau", "nos vemos",
    "me voy", "hasta la proxima", "cuidate",
)
_ASENTIMIENTOS = (
    "vale", "ok", "okey", "bien", "guay", "genial", "perfecto", "entendido",
    "listo", "ya esta", "nada", "no nada", "nada mas", "ninguna", "no",
    "si", "claro", "exacto", "eso es", "correcto", "de acuerdo",
)
_SOBRE_ELLA = (
    "quien eres", "como te llamas", "que eres", "quien te hizo",
    "que sabes hacer", "para que sirves",
)

_CHARLA = frozenset(_SALUDOS + _GRACIAS + _DESPEDIDAS + _ASENTIMIENTOS + _SOBRE_ELLA)

# Coletillas que la gente pega delante o detrás sin cambiar el sentido.
_ADORNOS = re.compile(r"\b(nova|porfa|por favor|pues|bueno|oye|eh|venga|tia|tio)\b")


def _normalizar(texto: str) -> str:
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    limpio = re.sub(r"[^\w\s]", " ", sin_tildes)
    limpio = _ADORNOS.sub(" ", limpio)
    return " ".join(limpio.split())


def es_pura_charla(texto: str) -> bool:
    """¿Esto se contesta y ya, sin tocar nada del PC?

    Sólo dice que sí cuando la frase entera, quitados los adornos, ES
    una de las conocidas. "hola" sí; "hola, abre Discord" no, porque lo
    que queda después de "hola" ya no está en la lista.
    """
    limpio = _normalizar(texto)
    if not limpio or len(limpio.split()) > MAX_PALABRAS:
        return False
    if limpio in _CHARLA:
        return True
    # Dos seguidas también valen: "hola gracias", "vale adios".
    trozos = limpio.split()
    for corte in range(1, len(trozos)):
        if " ".join(trozos[:corte]) in _CHARLA and " ".join(trozos[corte:]) in _CHARLA:
            return True
    return False
