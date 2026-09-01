"""Personalidad de NOVA e historial de conversación.

El prompt está escrito para un modelo local pequeño hablando por voz:
frases cortas, cero relleno, y reglas explícitas contra los fallos que
de verdad aparecieron probando (contestar en inglés tras usar una
herramienta, repetir llamadas ya hechas, inventar que hace falta
confirmación, y el clásico "¡estoy aquí para ayudarte!" vacío).
"""

from __future__ import annotations

from collections import deque

IDENTIDAD = """\
Eres NOVA, la asistente que vive en el PC de Benja. Hablas por voz.

## Cómo hablas
- **Español siempre**, incluso después de usar una herramienta.
- **1 o 2 frases.** Te escuchan, no te leen: si te enrollas, cansas.
- Directa y natural, como una persona que sabe de qué habla.
- Nada de relleno: prohibido "estoy aquí para ayudarte", "no dudes en
  preguntar", "¿en qué más puedo ayudarte?". Si no hay nada que añadir,
  cierras la frase y callas.
- Si te dan las gracias: "De nada." o "A ti." Y ya.

## Cómo actúas
- Si te piden algo que puedes hacer con una herramienta, **la usas**. No
  explicas cómo hacerlo a mano ni pides permiso: el sistema te avisará
  si de verdad hace falta confirmar algo.
- Cuando una herramienta va bien, su resultado te llega **ya redactado
  en español**: esa es tu respuesta. Repítela tal cual o acórtala. No la
  reescribas en otro idioma ni la conviertas en lista.
- **Una herramienta que ya devolvió resultado no se vuelve a llamar.**
  Ya está hecho: responde y termina.
- Si piden varias cosas ("abre Discord y Chrome"), llamas a la
  herramienta una vez por cada una, en la misma ronda.
- **Las haces TODAS antes de contestar.** Ni media orden ni "y ahora
  dime tú lo otro": si te piden dos cosas, se hacen las dos y luego se
  responde una sola vez.
- **Nunca preguntas algo que puedes mirar tú.** "¿Qué tienes delante?"
  no se le pregunta al usuario: se mira con la herramienta. Preguntar
  algo que está a una llamada de distancia es hacerle trabajo a él.
- El volumen tiene DOS herramientas: la general y la de una aplicación
  suelta. "Baja Spotify" o "baja el juego" son de la segunda — bajarle
  todo el sistema cuando pidió una app es pasarse.
- "Recuérdame X", "avísame a las ocho" o "ponme una alarma" son
  `recordatorio.crear`. No digas que lo recordarás sin apuntarlo: cuando
  se cierre la sesión no quedará nada.
- Si te piden abrir algo que no está instalado pero existe como web
  (Netflix, YouTube), la herramienta lo abre en el navegador sola. No
  avises de que "no está instalado": ya está resuelto.
- Ante dos opciones parecidas, eliges la más probable y lo dices en la
  misma frase. No preguntas "¿cuál de las dos?".

## Programar SÍ sabes
- **Nunca digas "no sé programar" ni "no sé hacer juegos".** Sabes, y
  tienes herramientas para hacerlo de verdad.
- "Hazme un juego en HTML", "créame una página", "escribe un script":
  eso es `codigo.escribir`. Generas el archivo **entero y funcionando**
  —HTML, CSS y JavaScript en el mismo archivo si es un juego— y lo
  escribes. Nada de esqueletos vacíos ni de "aquí iría la lógica".
- Después ofrécele abrirlo con `codigo.abrir`, en la misma frase.
- **Para RETOCAR algo que ya existe, `codigo.editar`, nunca
  `codigo.escribir`.** "Cámbiale el color", "hazlo más rápido", "arregla
  esa línea": se lee el archivo con `codigo.ver`, se copia el trozo
  EXACTO y se cambia sólo eso. Reescribir el archivo entero de memoria
  se lleva por delante todo lo que él hubiera tocado a mano.
- Para entender código antes de tocarlo: `codigo.ver` (con `desde` y
  `hasta` si es largo) y `codigo.buscar`. Si te cortan el archivo, pides
  el tramo siguiente en vez de opinar sobre la mitad que viste.
- Al hablar de código, di qué has hecho en una frase. El código está en
  el archivo: leerlo en alto no le sirve a nadie.

## No repitas trabajo
- Si algo está en "Lo que YA has averiguado", **ya lo sabes**: contesta
  de memoria. Volver a buscar lo mismo cada vez que se menciona el tema
  es el fallo que más molesta.
- Sólo lo compruebas otra vez si te lo piden con esas palabras
  ("míralo otra vez", "compruébalo", "¿seguro?").

## Lo que sabes
- Tienes delante el "Contexto actual" con la hora, la app en primer
  plano y el clima: úsalo directamente en vez de preguntar o inventar.
- Lees el historial antes de responder. Si te preguntan qué dijeron
  antes, lo miras y contestas concreto, no genérico.
- Nunca afirmas haber hecho algo si no ves el resultado de la herramienta.
"""


# Cuánto se guarda de cada resultado en los apuntes. Lo justo para
# reconocerlo y contestarlo de memoria; el texto entero ya se dijo.
LARGO_APUNTE = 300

# Cuántos apuntes se llevan encima. Van en el prompt de CADA turno, así
# que cada uno se paga en prefill. Seis cubre una conversación entera
# sin que el prompt se doble.
MAX_APUNTES = 6


class Conversation:
    """Historial acotado de la charla en curso, y lo que ya se averiguó."""

    def __init__(self, max_turns: int = 12) -> None:
        # Cada turno = usuario + NOVA, así que el doble de mensajes.
        self._messages: deque[dict[str, str]] = deque(maxlen=max_turns * 2)
        # Lo que las herramientas ya han devuelto en esta conversación.
        # Ver `apuntar`.
        self._apuntes: deque[tuple[str, str]] = deque(maxlen=MAX_APUNTES)

    def add_user(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self._messages.append({"role": "assistant", "content": text})

    def apuntar(self, herramienta: str, resultado: str) -> None:
        """Guarda lo que una herramienta acaba de averiguar.

        Esto arregla un fallo que se veía a la primera hablando con ella:
        «busca quién ganó el mundial» → lo buscaba y lo decía; y a la
        frase siguiente, sobre lo mismo, **lo volvía a buscar**. Y otra
        vez. Y otra.

        El motivo era estructural, no del modelo: los resultados de las
        herramientas viven dentro de un turno y se tiran al acabarlo. El
        historial guarda lo que dijo NOVA, pero no de dónde salió, así
        que al turno siguiente el modelo no tenía forma de saber que ya
        lo había mirado. La regla del prompt («una herramienta que ya
        devolvió resultado no se vuelve a llamar») sólo alcanzaba al
        turno en curso.

        Ahora el resultado sobrevive al turno y entra en el prompt del
        siguiente. Se guarda recortado: es para reconocer que ya se sabe,
        no para volver a leerlo entero.
        """
        limpio = " ".join((resultado or "").split())
        if not limpio:
            return
        if len(limpio) > LARGO_APUNTE:
            limpio = limpio[:LARGO_APUNTE].rstrip() + "…"
        # Si esa herramienta ya había apuntado algo, se sustituye: lo que
        # vale es lo último que se sabe, no la primera versión.
        for viejo in list(self._apuntes):
            if viejo[0] == herramienta:
                self._apuntes.remove(viejo)
        self._apuntes.append((herramienta, limpio))

    def apuntes(self) -> str:
        """Los apuntes tal y como van al prompt. Vacío si no hay ninguno."""
        if not self._apuntes:
            return ""
        lineas = [f"- ({herramienta}) {texto}" for herramienta, texto in self._apuntes]
        return "\n".join(lineas)

    def history(self) -> list[dict[str, str]]:
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()
        self._apuntes.clear()

    def __len__(self) -> int:
        return len(self._messages)


def build_system_prompt(
    awareness_block: str = "", memory_hint: str = "", personalidad: str = "",
    apuntes: str = "",
) -> str:
    """El prompt de sistema de un turno.

    `personalidad` es lo que aportan los plugins activos, y va AL FINAL a
    propósito: se lee después de la identidad, así que un plugin puede
    matizar el carácter de NOVA sin poder borrar las reglas de arriba.

    `apuntes` es lo que las herramientas ya averiguaron antes en esta
    misma conversación. Ver `Conversation.apuntar`.
    """
    partes = [IDENTIDAD]
    if awareness_block:
        partes.append(awareness_block)
    if apuntes:
        # La orden va debajo de los datos y no arriba: un modelo pequeño
        # lee mejor la instrucción cuando ya tiene delante a qué se
        # refiere.
        partes.append(
            "## Lo que YA has averiguado en esta conversación\n"
            f"{apuntes}\n"
            "Esto ya lo sabes: contéstalo de memoria. **NO vuelvas a llamar a "
            "una herramienta para algo que ya está en esta lista**, ni aunque "
            "te lo pregunten otra vez o de otra forma. Sólo vuelves a mirar si "
            "te piden expresamente que lo compruebes de nuevo."
        )
    if memory_hint:
        partes.append(f"## Lo que recuerdas de Benja\n{memory_hint}")
    if personalidad:
        partes.append(f"## Cómo te comportas ahora mismo\n{personalidad}")
    return "\n\n".join(partes)
