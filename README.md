# NOVA 5 — agosto 2026

> Sucesora de NOVA4 (julio 2026); NOVA, NOVA3.0-2027 y `nova/` quedan archivadas.

Asistente de escritorio por voz. **Local, gratis y sin nube**: ni API
keys, ni suscripciones, ni datos saliendo del PC.

Dices **"NOVA"**, te escucha, y hace lo que le pides.

```
                ┌─────────────────────────────┐
   "NOVA"  ───▶ │  chime + borde azul + orbe  │
                └──────────────┬──────────────┘
                               ▼
                      tu orden hablada
                               ▼
              Ollama decide qué herramienta usar
                               ▼
              abre apps · captura · lee el sistema
                    crea archivos · recuerda
                               ▼
              responde por voz y sigue escuchando
```

## Estado

En obras. Esto es lo que hay hecho y lo que no, sin adornos:

| Frente | Estado |
|---|---|
| Repo con historial revisable | **hecho** — NOVA4 no tenía git, y era su mayor debilidad |
| Barrido de bugs de NOVA4 | **hecho** — ver "Lo que se arregló" |
| Logs con niveles y `--debug` | **hecho** |
| STT en dos etapas (Vosk + faster-whisper) | pendiente |
| `python -m nova.doctor` | pendiente |
| Panel abajo a la derecha | pendiente |
| Búsqueda en internet | pendiente |
| Presupuesto de RAM/VRAM medido | pendiente |

## Qué necesitas

| Pieza | Para qué | Coste |
|---|---|---|
| [Ollama](https://ollama.com) + `qwen3.5:4b` | el cerebro | gratis |
| Modelo de voz Vosk en español | oírte | gratis |
| Voz SAPI5 de Windows | hablarte | ya la tienes |

```bash
ollama pull qwen3.5:4b
```

El modelo de voz se busca solo en `models/` y en los proyectos vecinos;
si no lo tienes, descarga [vosk-model-es-0.42](https://alphacephei.com/vosk/models)
y descomprímelo en `models/vosk/`.

## Arrancar

```bash
pip install -r requirements.txt
python run.py
```

Con el detalle completo al fichero de log (`data/nova.log`), que es lo
que hace falta cuando el audio se porta raro:

```bash
python run.py --debug
```

Comprobar que el cerebro va bien, sin abrir ventanas ni micrófono:

```bash
python smoke.py
```

## Cómo se usa

- **"NOVA"** → suena el chime, el borde de la pantalla se ilumina, te escucha.
- **"NOVA, abre Discord"** → todo seguido también vale.
- Conversación continua: tras responder, sigue escuchando — no hace
  falta repetir "NOVA" en cada turno.
- **"hasta luego"**, **"eso es todo"**, **"adiós"** → cierra la conversación.
- Callarte 20 segundos → vuelve a dormir sola.
- **Clic derecho en el orbe** → silenciar voz o salir.
- Arrastra el orbe donde quieras.

Sabe la hora, qué aplicación tienes delante (y por tanto a qué juegas),
la batería y el tiempo que hace, sin que se lo preguntes.

## Qué puede hacer

Abrir y cerrar aplicaciones · buscar si algo está instalado · estado del
PC y especificaciones · qué tienes en primer plano · IP y conexión ·
capturas de pantalla · crear, leer y borrar archivos en su carpeta de
trabajo · recordar cosas de ti entre sesiones · volumen del sistema.

## Configuración

Todo en `.env` (copia `.env.example`). Lo que más se toca:

```ini
NOVA_MODEL=qwen3.5:4b
NOVA_KEEP_ALIVE=24h       # el modelo se queda en VRAM, no se recarga
NOVA_CONFIRM=solo_peligroso
NOVA_TTS=true
```

## Decisiones que importan

Las seis primeras vienen de NOVA4 y **no se tocan**. Cada una costó un
bug real.

**Un solo proceso.** La versión anterior separaba backend Python y
frontend Electron hablando por WebSocket. La mayoría de fallos de voz
salían de ese viaje: el navegador arrancaba el audio suspendido, el
bucle de recepción se bloqueaba mientras el modelo respondía, el token
de sesión caducaba. Aquí el micrófono y el reconocedor están a una
llamada de función: esa clase entera de bugs desaparece.

**`127.0.0.1`, nunca `localhost`.** En Windows, Python prueba IPv6 antes
de caer a IPv4: ~2.5 s perdidos en *cada* respuesta. Medido: 3.8 s vs
1.3 s para la misma petición.

**El resultado de una herramienta vuelve al modelo como texto en
español, no como JSON.** Con JSON, un modelo pequeño no reconocía la
forma como "ya está hecho" y repetía la llamada hasta agotar las rondas
(4 capturas seguidas, el 100% de las veces), además de contestar en
inglés.

**Las coletillas se quitan con código, no con el prompt.** Por mucho que
el prompt prohíba "¿necesitas algo más?", un modelo pequeño lo cuela
igual. Un filtro determinista sí lo garantiza (`nova/core/polish.py`).

**Los permisos los decide el registro, no el modelo.** Borrar, cerrar
procesos o subir el volumen pasan por confirmación según la política;
que el LLM "decida" saltársela no es una opción que exista en el código.

**Conversación continua.** Tras responder sigue escuchando. Solo una
despedida explícita o el timeout de silencio la duermen.

Y las que estrena NOVA5:

**La longitud de lo que dice también se corta con código.** Es la cuarta
regla aplicada a otra cosa. El prompt pide "1 o 2 frases" y el modelo
soltó 200 caracteres = **12.34 s hablando**, seguidos de otros 148 =
8.08 s. Veinte segundos con el micrófono mudo, y lo siguiente que
entendió fue el fragmento suelto "principio". Tope: 2 frases y 180
caracteres, y sólo en el camino del TTS — el historial guarda la
respuesta entera.

**Nada pesado se carga en el hilo de Qt.** Cargar el modelo de voz ahí
costaba 42 s durante los que la interfaz no repintaba y decir "NOVA" no
hacía nada. Ahora se carga en su hilo y el orbe lo anuncia con un estado
"preparando" propio: si NOVA todavía no oye, se tiene que ver.

**Lo que se le oculta al modelo se decide en la herramienta.** Con
`expose_to_llm=False`, junto a su definición. NOVA4 tenía una lista
aparte que nombraba tres herramientas inexistentes: parecía un control
de superficie y no lo era.

## Lo que se arregló de NOVA4

Todo con test que falla antes y pasa después.

- **42 s sorda al arrancar.** `Model(...)` de Vosk se llamaba en el hilo
  de Qt (medido en el log del 25/07: 23:09:36 → 23:10:18). Ahora en su
  hilo, con aviso por `on_ready` / `on_error`.
- **El filtro de coletillas se había quedado atrás.** Reproducido en el
  smoke del 26/08: 4 de 7 respuestas acabaron en coletilla y ninguna se
  filtró. Faltaban el clítico opcional ("en qué **te** puedo ayudar"),
  el "algo más" envuelto en cualquier verbo, y las que el modelo cuela
  **en medio** de la frase.
- **El orbe se quedaba clavado** en "pensando" para siempre con
  `NOVA_TTS=false`: nadie disparaba `on_end`, así que nadie lo movía.
- **La memoria nunca llegaba al prompt.** `build_system_prompt` acepta
  `memory_hint` desde NOVA4 y nadie se lo pasaba: NOVA sólo recordaba si
  el modelo acertaba a llamar a `memory.recall` por su cuenta.
- **La interrupción no interrumpía.** `Speaker._interrupt` se ponía, se
  limpiaba y no se consultaba nunca.
- **`LLM_HIDDEN` ocultaba tres herramientas que no existen.**
- **El log crecía sin techo** y no distinguía niveles.

## Estructura

```
nova/
  app.py            orquestación y máquina de estados
  config.py         toda la configuración, con los porqués
  core/
    agent.py        bucle LLM ↔ herramientas
    conversation.py personalidad e historial
    awareness.py    hora, app activa, batería, clima
    polish.py       limpieza de tics y longitud de la voz
  llm/ollama.py     cliente del modelo local
  voice/
    listener.py     wake word y captura (Vosk)
    speaker.py      voz (SAPI5) con interrupción
    chime.py        sonido de activación
  tools/            lo que NOVA sabe hacer + permisos
  ui/               orbe, borde azul
tests/              111 tests, sin red ni micrófono
```

## Tests

```bash
python -m pytest tests -q
ruff check .
```
