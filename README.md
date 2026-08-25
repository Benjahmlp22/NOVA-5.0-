# NOVA 4

Asistente de escritorio por voz. **Local, gratis y sin nube**: ni API
keys, ni suscripciones, ni datos saliendo del PC.

No tiene panel. Se abre y aparece un círculo pequeño en una esquina.
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
y descomprímelo en `NOVA4/models/vosk/`.

## Arrancar

```bash
pip install -r requirements.txt
python run.py
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
- **Arrastra el orbe** donde quieras.

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
NOVA_MODEL=qwen3.5:4b     # gemma4:e4b razona algo mejor pero pesa 9.6GB
NOVA_CONFIRM=solo_peligroso  # o "estricto" si quieres que pregunte más
NOVA_ORB_CORNER=bottom-left
NOVA_TTS=true
```

## Decisiones que importan

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
el prompt prohíba "¿necesitas algo más?", un 3B lo cuela igual. Un
filtro determinista sí lo garantiza (`nova/core/polish.py`).

**Los permisos los decide el registro, no el modelo.** Borrar, cerrar
procesos o subir el volumen pasan por confirmación según la política;
que el LLM "decida" saltársela no es una opción que exista en el código.

## Estructura

```
nova/
  app.py            orquestación y máquina de estados
  config.py         toda la configuración, con los porqués
  core/
    agent.py        bucle LLM ↔ herramientas
    conversation.py personalidad e historial
    awareness.py    hora, app activa, batería, clima
    polish.py       limpieza de tics del modelo
  llm/ollama.py     cliente del modelo local
  voice/
    listener.py     wake word y captura (Vosk)
    speaker.py      voz (SAPI5) con interrupción
    chime.py        sonido de activación
  tools/            lo que NOVA sabe hacer + permisos
  ui/               orbe, borde azul
tests/              71 tests, sin red ni micrófono
```

## Tests

```bash
python -m pytest tests -q
```
