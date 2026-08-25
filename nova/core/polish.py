"""Pulido de la respuesta antes de enseñarla o decirla.

Un modelo local de 3B entiende bien las órdenes pero se le escapan tics
de chatbot por mucho que el prompt los prohíba.  Medido con el modelo
real, en 7 frases de prueba: 5 acababan en "¿Necesitas algo más?" y una
captura de pantalla se anunció con `![imagen](https://yourserverurl/...)`
— una URL que no existe.

Pedirlo mejor en el prompt no lo arregla del todo; quitarlo después, sí.
Este módulo es determinista: mismo texto de entrada, mismo resultado.
"""

from __future__ import annotations

import re

# Coletillas de relleno al final de la respuesta. Se quitan solo si van
# al FINAL: "¿necesitas algo más?" como pregunta central sí es válida.
#
# El grupo 1 captura el punto de la frase ANTERIOR para devolverlo en la
# sustitución; si se consumiera sin más, "Son las 10:36. ¿Algo más?"
# quedaría como "Son las 10:36" sin punto final.
_RELLENO_FINAL = re.compile(
    r"([.!?])?\s*[¿¡]?\s*(?:"
    r"necesitas (?:algo|alguna cosa) m[aá]s|"
    r"hay algo m[aá]s (?:en (?:lo )?que )?(?:pueda|puedo) (?:ayudarte|hacer)|"
    r"en qu[eé] (?:m[aá]s )?(?:puedo|pueda) ayudarte(?: hoy)?|"
    r"puedo ayudarte (?:en|con) (?:algo|alguna cosa) m[aá]s|"
    r"(?:h[aá]zme|d[ií]melo|av[ií]same) si necesitas (?:ayuda|algo)"
    r"[^.!?]*|"
    r"estoy (?:aqu[ií]|siempre aqu[ií]) para ayudarte|"
    r"no dudes en (?:preguntar|dec[ií]rmelo|consultarme)[^.!?]*"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# Red de seguridad genérica: cualquier PREGUNTA final cuyo tema sea
# "ayudarte/asistirte/servirte" es relleno, escríbala como la escriba.
# Sale de ver al modelo inventar variantes nuevas cada vez ("¿cómo puedo
# asistirte hoy?", "¿algo en lo que pueda ayudarte específicamente?"):
# perseguirlas una a una no acaba nunca.
_PREGUNTA_DE_AYUDA = re.compile(
    r"(?:^|(?<=[.!?]))\s*[¿]?[^.!?]{0,80}?"
    r"\b(?:ayudar(?:te|le)|asistir(?:te|le)|servir(?:te|le))\b"
    r"[^.!?]{0,40}\?\s*$",
    re.IGNORECASE,
)

# Imágenes/enlaces markdown inventados: el modelo no puede mostrar
# imágenes ni conoce URLs públicas de los archivos locales.
_IMG_MARKDOWN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_MARKDOWN = re.compile(r"\[([^\]]+)\]\((?:https?://)?[^)]*\)")
# URLs claramente alucinadas (placeholders de ejemplo).
_URL_FALSA = re.compile(
    r"https?://(?:yourserver\w*|example\.com|tu-?servidor|localhost)\S*",
    re.IGNORECASE,
)


def pulir(texto: str) -> str:
    """Quita tics de chatbot y formato inventado. Nunca deja vacío."""
    if not texto:
        return ""

    t = texto.strip()

    # Formato inventado primero: al quitarlo puede quedar relleno detrás.
    t = _IMG_MARKDOWN.sub("", t)
    t = _LINK_MARKDOWN.sub(r"\1", t)   # deja el texto, tira el enlace
    t = _URL_FALSA.sub("", t)

    # El relleno puede venir encadenado ("... ¿Algo más? ¿Puedo ayudarte?").
    # `\1` devuelve la puntuación de la frase legítima que iba delante
    # (cadena vacía si no había, comportamiento estándar desde Python 3.5).
    for _ in range(3):
        nuevo = _RELLENO_FINAL.sub(r"\1", t).strip()
        nuevo = _PREGUNTA_DE_AYUDA.sub("", nuevo).strip()
        if nuevo == t:
            break
        t = nuevo

    t = re.sub(r"\s{2,}", " ", t)
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    t = t.strip(" \t\n-—·")

    # Si de tanto limpiar no queda nada, es mejor un acuse breve que un
    # silencio: el usuario ha pedido algo y merece señal de que se hizo.
    return t or "Listo."
