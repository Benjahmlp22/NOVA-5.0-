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
    # "estoy aquí para ayudarte", pero también "¡aquí estoy para ayudarte
    # desde aquí!" — visto tal cual en el log real del 25/07. El orden de
    # las palabras cambia en cada generación; la forma fija es el
    # "estoy ... para ayudar", así que es eso lo que se ancla.
    r"(?:aqu[ií]\s+)?estoy(?:\s+(?:siempre\s+)?aqu[ií])?\s+para\s+ayudar(?:te|le)?[^.!?]*|"
    r"no dudes en (?:preguntar|dec[ií]rmelo|consultarme)[^.!?]*"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE,
)

# Red de seguridad genérica: cualquier PREGUNTA final cuyo tema sea
# "ayudarte/asistirte/servirte" es relleno, escríbala como la escriba.
# Sale de ver al modelo inventar variantes nuevas cada vez ("¿cómo puedo
# asistirte hoy?", "¿algo en lo que pueda ayudarte específicamente?"):
# perseguirlas una a una no acaba nunca.
#
# El clítico va OPCIONAL (`ayudar(?:te|le)?`) desde NOVA5. Exigirlo dejaba
# pasar "¿En qué te puedo ayudar hoy?" — el pronombre se le había
# adelantado al verbo — que es literalmente lo que NOVA respondió a
# "¿cómo estás?" en el log del 25/07 y en el smoke del 26/08.
_PREGUNTA_DE_AYUDA = re.compile(
    r"(?:^|(?<=[.!?]))\s*[¿]?[^.!?]{0,80}?"
    r"\b(?:ayudar(?:te|le)?|asistir(?:te|le)?|servir(?:te|le)?)\b"
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


# ── Longitud de lo que se dice en voz alta ───────────────────────────
#
# Medido en el log real del 25/07 (voz Sabina, NOVA_TTS_RATE=195):
#
#       5 caracteres →  1.66 s        39 caracteres →  3.08 s
#     148 caracteres →  8.08 s       200 caracteres → 12.34 s
#
# Ajustando esos cuatro puntos: ~1.4 s fijos de arrancar el motor SAPI
# más ~0.055 s por carácter.  Y mientras NOVA habla el micrófono está
# mudo, así que cada carácter de más es un carácter de sordera.  En ese
# log soltó 200 + 148 caracteres seguidos: 20 s sin oír nada, y lo
# siguiente que entendió fue el fragmento suelto "principio".
#
# El prompt ya pide "1 o 2 frases" y el modelo se lo salta igual — el
# mismo motivo por el que las coletillas se quitan con código y no
# pidiéndolo mejor.  Esto es esa regla aplicada a la longitud.
#
# 180 caracteres ≈ 11 s en el peor caso.  El tope de frases es el que
# hace el trabajo de verdad; el de caracteres es la red por si el modelo
# suelta una sola frase kilométrica sin puntuación intermedia.
MAX_FRASES_VOZ = 2
MAX_CARACTERES_VOZ = 180

_FIN_DE_FRASE = re.compile(r"(?<=[.!?…])\s+")
_TERMINADORES = ".!?…"


def recortar_para_voz(
    texto: str,
    *,
    max_frases: int = MAX_FRASES_VOZ,
    max_caracteres: int = MAX_CARACTERES_VOZ,
) -> str:
    """Acorta lo que se va a decir en alto. El texto completo no se toca.

    Se aplica SOLO en el camino del TTS: los subtítulos y el historial
    siguen viendo la respuesta entera.  Recortar lo que se lee y no lo
    que se guarda es lo que evita que NOVA "olvide" lo que dijo.
    """
    t = (texto or "").strip()
    if not t:
        return ""

    frases = [f for f in _FIN_DE_FRASE.split(t) if f.strip()]
    if len(frases) > max_frases:
        t = " ".join(frases[:max_frases]).strip()

    if len(t) <= max_caracteres:
        return t

    # Corte por palabra entera: leer media palabra suena a avería, no a
    # resumen. Si no hay ningún espacio razonable donde cortar (palabra
    # gigante, una URL), se corta a pelo antes que devolver el ladrillo.
    corte = t.rfind(" ", 0, max_caracteres)
    if corte < max_caracteres // 2:
        corte = max_caracteres
    recorte = t[:corte].rstrip(" ,;:-—")
    if recorte and recorte[-1] not in _TERMINADORES:
        recorte += "."
    return recorte
