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
| faster-whisper en la RTX 3060 | **verificado** — 1.6 % WER, 0.28 s/frase, 365 MiB |
| STT en dos etapas dentro de la app | pendiente |
| `python -m nova.doctor` | **hecho** |
| Banco de pruebas WER + grabador de corpus | **hecho** |
| Panel abajo a la derecha | pendiente |
| Búsqueda en internet | pendiente |
| Presupuesto de RAM/VRAM medido | **hecho** |

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

## Reconocimiento de voz: qué se eligió y con qué números

Medido el 26/08/2026 en esta máquina (RTX 3060 12 GB, Ryzen 5 5600G)
sobre **20 órdenes reales grabadas hablando** (`bench/grabar.py`), con el
mismo audio y el mismo remuestreo para todos.

| motor | WER | exactas | latencia/frase | VRAM |
|---|---|---|---|---|
| Vosk `es-0.42` | 17.9 % | 10/20 | 0.76 s | — (CPU) |
| faster-whisper `small` | 16.1 % | 10/20 | 0.30 s | 365 MiB |
| faster-whisper `small` + vocabulario | 10.2 % | 13/20 | 0.31 s | 365 MiB |
| faster-whisper `medium` | 12.9 % | 13/20 | 0.63 s | 924 MiB |
| **faster-whisper `medium` + vocabulario** | **6.5 %** | **16/20** | 0.63 s | 924 MiB |
| faster-whisper `large-v3-turbo` | 24.1 % | 8/20 | 0.61 s | 1024 MiB |
| faster-whisper `large-v3-turbo` + vocabulario | 19.1 % | 9/20 | 0.56 s | 1024 MiB |

**Gana `medium` con vocabulario**: 2.7× menos error que Vosk y 2.5× menos
que `small` a secas, por 0.3 s más de latencia y 560 MiB de VRAM que en
una tarjeta de 12 GB sobran.

**El "+ vocabulario" es la mitad del resultado y sale gratis.** Es un
`initial_prompt` con las palabras que NOVA oye todos los días — Discord,
Spotify, bloc de notas, RTX 4070, vatios — que el decodificador ve como
contexto previo. Sin él, Whisper escribía "blog de notas", "calor
favorito" y "cuánta **de morir a** RAM". Baja el WER de 16.1 % a 10.2 %
en `small` y de 12.9 % a 6.5 % en `medium`, **sin coste de latencia
medible**. No hay ninguna frase del corpus dentro del prompt.

`large-v3-turbo` es el peor de los tres, con y sin vocabulario. Más
grande no es más listo cuando las frases son órdenes de cinco palabras.

### Por qué NO hay que fiarse del audio sintético

La primera versión de esta tabla se midió con la voz SAPI5 de Windows,
porque el micrófono no estaba disponible. Daba esto:

| motor | WER sintético | WER voz real |
|---|---|---|
| Vosk `es-0.42` | 6.1 % | 17.9 % |
| faster-whisper `small` | 1.6 % | 16.1 % |
| faster-whisper `medium` | 1.6 % | 12.9 % |

Con voz sintética `small` empataba con `medium` y la conclusión era "no
subas de small". Con voz real, `medium` gana claramente. El audio
sintético sirvió para lo que tenía que servir —comprobar que
faster-whisper funciona en la 3060 sin necesitar micrófono— y **su
conclusión sobre qué modelo usar era falsa**.

## Presupuesto de recursos

Con la configuración por defecto, medido con `nvidia-smi` y `psutil`:

| pieza | VRAM | notas |
|---|---|---|
| Ollama `qwen3.5:4b` | 3.1 GB | residente con `NOVA_KEEP_ALIVE=24h` |
| faster-whisper `small` | 365 MiB | `int8_float16` |
| Vosk `es-0.42` | — | ~2.3 GB en RAM, no toca la GPU |
| escritorio de Windows | ~2.8 GB | navegador, juegos, etc. |

Sobra sitio en una tarjeta de 12 GB. El cuello de botella de NOVA no es
la memoria: es el camino del audio y el arranque en frío.

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

- **La interfaz se congelaba al arrancar.** `Model(...)` de Vosk se
  llamaba en el hilo de Qt. Ahora `start()` vuelve en **0 ms** y la carga
  ocurre en el hilo de voz.

  Lo que ese arreglo **no** resuelve: NOVA sigue sin oír hasta que el
  modelo termina de cargar. Medido el 26/08 en esta máquina:

  | Modelo de voz | Tamaño | Carga | Listo para oír |
  |---|---|---|---|
  | `vosk-model-es-0.42` | 2.3 GB | 47.2 s | 47.8 s |
  | `vosk-model-small-es-0.42` | 58 MB | 0.4 s | **0.98 s** |

  Son 48× de diferencia. El grande se usa porque el small detectaba peor
  el wake word — probado en su día, no supuesto. La decisión queda
  pendiente de la Fase 2, donde se mide la tasa de acierto de los dos
  sobre frases reales; hasta entonces manda la detección y se paga el
  arranque. Al menos ahora se ve: el orbe está en "preparando", no en
  "dormida".
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
tests/              143 tests, sin red ni micrófono
```

## Tests

```bash
python -m pytest tests -q
ruff check .
```

## Medir el audio

```bash
python -m nova.doctor              # ¿es el micro o es el reconocedor?
python bench/grabar.py --device 28 # graba las 20 frases hablando
python bench/bench_stt.py --sufijo benja   # WER y latencia de los dos motores
```

`nova.doctor` es lo primero que hay que ejecutar cuando NOVA no entienda:
separa "micro apagado", "dispositivo equivocado", "nivel muy bajo" y
"reconocedor malo", que dan exactamente el mismo síntoma.
