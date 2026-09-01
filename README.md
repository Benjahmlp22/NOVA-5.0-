# NOVA 5 — agosto 2026

[![tests](https://github.com/Benjahmlp22/NOVA-5.0-/actions/workflows/tests.yml/badge.svg)](https://github.com/Benjahmlp22/NOVA-5.0-/actions/workflows/tests.yml)
[![licencia: MIT](https://img.shields.io/badge/licencia-MIT-blue.svg)](LICENSE)

> Sucesora de NOVA4 (julio 2026); NOVA, NOVA3.0-2027 y `nova/` quedan archivadas.

Asistente de escritorio por voz. **Local, gratis y sin nube**: ni API
keys, ni suscripciones, ni datos saliendo del PC.

Sólo Windows: usa SAPI y las voces OneCore, el OCR de WinRT y pywin32
para las ventanas y el mezclador de audio.

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

## Si vienes a tocar el código

Tres archivos, en este orden:

| | |
|---|---|
| **[docs/BITACORA.md](docs/BITACORA.md)** | Lo que se midió y por qué se decidió cada cosa. Incluye **lo que se probó y NO funcionó**, para que nadie lo repita. |
| **[docs/PENDIENTE.md](docs/PENDIENTE.md)** | Lo que falta, y lo que se dejó fuera a propósito con su motivo. |
| **[CONTRIBUTING.md](CONTRIBUTING.md)** | Cómo se trabaja aquí: medir antes de escribir, todo en español, dónde tocar cada cosa. |

## Estado

En obras. Esto es lo que hay hecho y lo que no, sin adornos:

| Frente | Estado |
|---|---|
| Repo con historial revisable | **hecho** — NOVA4 no tenía git, y era su mayor debilidad |
| Barrido de bugs de NOVA4 | **hecho** — ver "Lo que se arregló" |
| Logs con niveles y `--debug` | **hecho** |
| faster-whisper en la RTX 3060 | **verificado** — 3.5 % WER con voz real, 0.50 s/frase |
| STT en dos etapas dentro de la app | **hecho** |
| `python -m nova.doctor` | **hecho** |
| Banco de pruebas WER + grabador de corpus | **hecho** |
| Pre-roll, VAD y unmute sin perder el principio | **hecho** |
| Panel abajo a la derecha | **hecho** |
| Búsqueda en internet | **hecho** — entra en la página si el resumen no trae el dato |
| Hablar mientras el modelo escribe | **hecho** |
| Cortarla hablando por encima | **hecho** |
| Órdenes encadenadas | **hecho** — 8/8 en tres pasadas |
| Presupuesto de RAM/VRAM medido | **hecho** |
| Latencia punta a punta < 1,5 s | **NO**, pero de 3-5.6 s a 2-5.2 s |

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

Tarda unos 8 segundos en estar escuchando: casi todo es cargar Whisper en
la GPU. **Arráncala siempre con `run.py`** y no importando `nova.app` a
mano — el orden de carga importa y está explicado en `nova/bootstrap.py`.

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

**Programas y ventanas.** Abrir y cerrar aplicaciones · buscar si algo
está instalado · traer al frente una ventana ya abierta · minimizarlas
todas · cerrar la de delante.

**El PC.** Estado y especificaciones · qué tienes en primer plano · IP y
conexión · capturas de pantalla · volumen del sistema y **de una app
concreta**.

**Tus cosas.** Buscar y abrir archivos en Escritorio, Descargas,
Documentos e Imágenes · **abrir una carpeta**, ver cuánto ocupa, qué ha
llegado hoy y qué pesa más · **ordenar Descargas y el Escritorio** por
tipo (y deshacerlo) · crear, leer y borrar en su carpeta de trabajo ·
**apuntar lo que le dictes** · leer y resumir **lo que tengas copiado** ·
recordar cosas de ti entre sesiones · recordatorios, alarmas y
temporizadores.

**Ver.** Leer el **texto que hay en pantalla** cuando no se puede copiar
· buscar una imagen tuya **por lo que se ve en ella**, no por su nombre.

**Ella misma.** Cambiarse la voz y la velocidad si se lo pides.

**Internet.** Buscar sin API keys y abrirte un enlace.

## Configuración

Todo en `.env` (copia `.env.example`). Lo que más se toca:

```ini
NOVA_MODEL=qwen3.5:4b
NOVA_MODEL_LIGERO=qwen2.5:3b  # al que se cambia si un juego llena la VRAM
NOVA_KEEP_ALIVE=24h           # el modelo se queda en VRAM, no se recarga
NOVA_CONFIRM=solo_peligroso
NOVA_ESPERA_RESPUESTA=60      # aguanta despierta si ha preguntado algo
NOVA_TTS=true
```

La voz elegida no va aquí: se cambia hablando y se guarda sola en
`data/voz.json`.

## Reconocimiento de voz: qué se eligió y con qué números

Medido el 26/08/2026 en esta máquina (RTX 3060 12 GB, Ryzen 5 5600G)
sobre **20 órdenes reales grabadas hablando** (`bench/grabar.py`), con el
mismo audio y el mismo remuestreo para todos.

| motor | WER | exactas | latencia/frase | VRAM |
|---|---|---|---|---|
| Vosk `es-0.42` | 17.9 % | 10/20 | 0.76 s | — (CPU) |
| faster-whisper `small` | 16.1 % | 10/20 | 0.30 s | 365 MiB |
| faster-whisper `small` + vocabulario | 9.3 % | 11/20 | 0.32 s | 365 MiB |
| faster-whisper `medium` | 12.9 % | 13/20 | 0.63 s | 924 MiB |
| **faster-whisper `medium` + vocabulario** | **3.5 %** | **17/20** | 0.50 s | 924 MiB |
| faster-whisper `large-v3-turbo` | 24.1 % | 8/20 | 0.61 s | 1024 MiB |
| faster-whisper `large-v3-turbo` + vocabulario | 19.1 % | 9/20 | 0.56 s | 1024 MiB |

**Gana `medium` con vocabulario**: 3.5 % de WER es 5× menos error que Vosk
y 4.6× menos que `small` a secas, por 0.2 s más de latencia y 560 MiB de
VRAM que en una tarjeta de 12 GB sobran. Para poner ese 3.5 % en escala:
es mejor de lo que daba Vosk sobre voz sintética perfecta (6.1 %).

**El "+ vocabulario" es la mitad del resultado y sale gratis.** Es un
`initial_prompt` con las palabras que NOVA oye todos los días — Discord,
Spotify, bloc de notas, RTX 4070, vatios — que el decodificador ve como
contexto previo. Sin él, Whisper escribía "blog de notas", "calor
favorito" y "cuánta **de morir a** RAM". Baja el WER de 16.1 % a 9.3 % en
`small` y de 12.9 % a 3.5 % en `medium`, **sin coste de latencia**. No hay
ninguna frase del corpus dentro del prompt.

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

## El panel

Abajo a la derecha, arrastrable por toda la pantalla, y **recuerda dónde
lo dejaste** entre sesiones (`data/ui.json`). Se colapsa al orbe de 62 px
con el guion de la esquina, y vuelve con un clic en el orbe — el orbe de
NOVA4 no se tiró: es el estado minimizado.

Dentro, en orden de importancia:

**La onda va con la amplitud real del audio.** Nada de `sin()`
decorativo. Escuchando, el nivel viene del micrófono; hablando, del audio
que está sonando. Eso último obligó a cambiar cómo habla NOVA: SAPI no
expone su búfer, así que ahora se le pide el audio (`save_to_file`) y lo
reproduce NOVA con sounddevice, midiendo el nivel bloque a bloque.

Salió mejor de lo esperado, porque sintetizar va ~9× más rápido que el
tiempo real:

| | antes de la primera sílaba |
|---|---|
| `runAndWait()` de SAPI (NOVA4) | ~1.40 s |
| sintetizar + reproducir (NOVA5) | **0.67 s** |

Y de paso interrumpir dejó de ser una súplica: se corta el flujo de
audio, que es inmediato, en vez de pedirle a SAPI que pare.

**Subtítulos.** Lo que NOVA entendió y lo que respondió. Con
reconocimiento de voz, ver la transcripción es la forma más rápida de
saber si te entendió mal, antes de que haga algo raro.

**Barra de actividad.** Las últimas cuatro acciones, con color por tipo:
abrir en verde, cerrar en rojo, buscar en azul, archivos en violeta,
memoria en amarillo. De reojo, en una barra pequeña, el color es lo único
que se lee — y abrir Discord no puede parecer lo mismo que cerrarlo.

**Un color por estado, y hablar tiene el suyo.** La distancia se mide en
TONO y no en RGB: el primer verde menta que probé se quedaba a 40° del
azul de escuchar y en pantalla no se distinguía. El verde de ahora está
a 81°.

| estado | color | |
|---|---|---|
| preparando | `#607694` | azul apagado: va a estar, aún no está |
| dormida | `#96989e` | gris |
| te escucho | `#4da3ff` | azul |
| pensando | `#f0f0f2` | blanco |
| hablando | `#4cd964` | verde |
| error | `#e8613c` | ámbar rojizo |

Sigue siendo PyQt5, sin marco, siempre encima, fuera del Alt+Tab
(`Qt.Tool`) y con `WA_ShowWithoutActivating`: **no roba el foco**.
Comprobado con una partida a pantalla completa delante — el panel se ve
encima y el juego no se entera.

## No llamar a herramientas por los buenos días

Un modelo pequeño con un catálogo delante quiere usarlo. Medido con el
modelo real y las herramientas cargadas:

| le dices | llamaba a |
|---|---|
| «adiós» | `memory.forget` — **que borra cosas** |
| «hola» | `pc.status`, `pc.active_window`, `pc.running_apps` |
| «qué tal el día» | `pc.status` |

Y no es por tener muchas: con las 27 de antes pasaba igual (17/20 con 27
y 17/20 con 48, los mismos fallos).

El prompt ya lo prohibía. No bastó, como no bastaba con el relleno ni
con los permisos, así que va por código (`nova/core/charla.py`): si lo
que has dicho es charla y nada más, al modelo se le pregunta **sin
herramientas** y le resulta imposible llamar a ninguna.

Conservador a propósito. Tomar una orden por charla dejaría a NOVA sin
hacer lo que le pediste, que es mucho peor que ofrecer herramientas de
más: sólo cuenta si la frase ENTERA, quitados los adornos, es una de las
conocidas. «hola» sí; «hola, abre Discord» no.

**Y cuando la herramienta ya trae la respuesta, esa ES la respuesta.**
Preguntando «cuál es el archivo más grande de descargas», NOVA elegía
bien, recibía «Lo que más ocupa: setup.exe, 1.8 gigas…» y contestaba «si
quieres que te diga qué ocupa más, dímelo». Tenía la respuesta delante y
no la daba. Las 19 herramientas que informan llevan `responde_sola` y su
mensaje sale tal cual — sin darle al modelo otra oportunidad de
estropearlo, y con una vuelta al modelo menos.

Sólo las que informan. `web.search` y `pantalla.leer` devuelven material
en bruto que sí hay que resumir.

Se mide con `bench/bench_herramientas.py`, donde un tercio de los casos
son «aquí no llames a nada».

**Y detrás de todo esto había un bug de fondo, no de hoy: `num_ctx`
nunca se había fijado.** Ollama usa 2048 tokens de contexto si nadie
dice lo contrario, y el catálogo de 52 herramientas por sí solo pesa
2050 — por encima del límite en cuanto se suma un mensaje. El modelo
respondía a un prompt cortado a la mitad, y el síntoma no lo parecía en
absoluto:

```
«cierra Spotify»  ->  voz_cambiar(descripcion="ponte voz de hombre")
```

Sin relación con lo pedido, reproducible 6 de 6 veces. Confirmado
forzando `num_ctx=8192` en la misma petición: ahí sí acertó. Con el
arreglo, el banco pasa de 20/34 (estable en 6 vueltas, dos
temperaturas) a **34/34**. En VRAM cuesta 280 MB — nada, sobra en 12 GB.

`NOVA_NUM_CTX=8192` en el `.env`.

## Escuchar sin responder a todo

Un asistente que responde a lo que oiga es peor que uno sordo. Cinco
filtros, cada uno de un fallo visto en uso real:

**No se oye a sí misma.** El búfer de pre-roll se tira al dejar de
hablar: lo que hay en ese segundo es su voz, y entraba como orden.

**No acepta órdenes mientras está ocupada.** Dos frases seguidas se
encolaban y contestaba una detrás de otra.

**Lo que no es habla se descarta.** Whisper devuelve SIEMPRE alguna
frase, también con música o el audio de un juego. Se mira su
`no_speech_prob` (> 0.60 fuera) y su `avg_logprob` (< -1.0 fuera). Una
palabra suelta tampoco cuenta como orden, salvo las afirmaciones que
responden a un "¿confirmas que...?".

**La conversación continua dura 8 s, no 20.** Tras responder puedes
seguir hablándole sin nombrarla; pasado ese hueco sigue despierta pero
hay que volver a llamarla. Con los 20 s del timeout de sueño, en una
habitación con la tele puesta procesaba como órdenes todo lo que se
dijera.

**"Cállate" la calla.** Junto con "duérmete", "dormite", "silencio",
"para ya" y "déjame". Que no hiciera caso justo a eso era lo peor.

**Pero si ha preguntado ELLA, espera.** Los 20 s no cuentan mientras
haya una respuesta pendiente: pedía permiso, te parabas a pensarlo, se
dormía, y el "sí" llegaba a una NOVA que ya no sabía de qué le hablaban.
Contestar a una pregunta que te acaba de hacer no puede exigir volver a
nombrarla. Con tope de un minuto (`NOVA_ESPERA_RESPUESTA`): una pregunta
sin contestar tampoco puede dejar el micro abierto para siempre.

Y se la puede **cortar hablando por encima**. Mientras habla el micro no
se procesa pero sí se vigila, con dos condiciones a la vez: el micro 2.5×
por encima del umbral de voz, y NOVA en una pausa de la suya — si suena
fuerte a la vez que ella, con altavoces eso es su propio eco. Más 150 ms
de voz sostenida, que separa "no, espera" de un golpe en la mesa.
`NOVA_INTERRUMPIR=false` lo apaga.

## Latencia de punta a punta

Objetivo: menos de 1,5 s desde que dejas de hablar hasta la primera
sílaba de NOVA. **Sigue sin cumplirse, pero ya está cerca en el caso
bueno.** Medido con todo montado:

| tramo | antes | ahora |
|---|---|---|
| detectar que has terminado (silencio) | 0.70 s | 0.70 s |
| etapa 2: Whisper `medium` en la 3060 | 0.50 s | 0.50 s |
| cerebro hasta la PRIMERA frase | 1.11 – 2.66 s | **0.16 – 3.33 s** |
| sintetizar la voz y empezar a sonar | 1.40 s | **0.54 s** |
| **total** | ≈ 3 – 5.6 s | **≈ 1.9 – 5.1 s** |

Tres cambios se comieron la diferencia:

**Hablar mientras el modelo escribe.** Ollama va en streaming y las
frases se dicen según se cierran, sin esperar al punto final. En una
respuesta directa la primera frase está lista en 0.16 s donde antes la
respuesta entera tardaba 1.11 s.

**Pedirle el audio a Windows en vez de dejarle hablar.** Sintetizar va
~9× más rápido que el tiempo real, así que la primera sílaba baja de
1.40 s a 0.67 s. Ver la sección del panel.

**Y las voces OneCore, que además de sonar mejor van 13× más rápido.**
Medido por el camino real de NOVA, la misma frase:

| sintetizador | por frase |
|---|---|
| SAPI, motor nuevo cada vez | 142 ms |
| OneCore, proceso vivo | **11 ms** |

SAPI pagaba construir un motor entero por frase, que es el precio de
esquivar el reciclado roto de `pyttsx3`. Como NOVA habla frase a frase
mientras el modelo escribe, esos 130 ms se ahorraban en cada una. Ver
«Las voces».

Lo que queda, y por qué no está hecho:

**El silencio (0.70 s).** Es un `NOVA_SILENCIO_FIN` en el `.env`.
Bajarlo es gratis en trabajo y caro en calidad: por debajo de ~0.5 s
corta a mitad de frase, porque una coma ya da 0.4 s de pausa.

**Las órdenes con herramienta (hasta 3.3 s).** Ahí el suelo lo pone la
herramienta, no el modelo: buscar en internet son 2 s de red que no se
pueden acelerar desde aquí.

## Ver: la pantalla y tus imágenes

Dos cosas distintas que no se hacen igual.

**Leer texto en pantalla** usa el OCR que ya trae Windows (WinRT). No
hay nada que descargar y son 0.18 s por lectura a 1920x1080. Sirve para
lo que NO se puede copiar: un error en un diálogo, el menú de un juego,
un PDF escaneado. Si el texto sí se puede copiar, `portapapeles.leer` es
mejor y más barato.

Por defecto lee sólo la **ventana de delante**. La pantalla entera trae
la barra de tareas, el navegador de detrás y los nombres de tus
carpetas, y el modelo se pierde entre todo eso.

**Buscar una imagen por lo que se ve** es otro problema. «Búscame
aquella imagen de League of Legends» tiene que funcionar aunque el
archivo se llame `descarga (7).png`, que es como se llaman de verdad.

Aquí hay 16.699 imágenes. El cálculo decide la arquitectura solo:

| cómo | por imagen | las 16.699 |
|---|---|---|
| un modelo de visión describiendo cada una | 1 – 3 s | **5 a 14 horas** |
| CLIP (vectores comparables) | 47 ms | **13 minutos** |

Así que CLIP, con `onnxruntime` en CPU — que ya estaba instalado, y no
hace falta torch (2.5 GB) para *ejecutar* un modelo ya entrenado. Los
dos codificadores son 607 MB que se bajan una vez de un repositorio
público; después esto no toca la red nunca más.

Detalles que salieron de medir:

**Los hilos de onnxruntime.** Su «auto» se queda a menos de la mitad de
lo que da la máquina:

| hilos | lote | por imagen |
|---|---|---|
| auto | 8 | 72 ms |
| auto | 32 | 80 ms |
| 6 | 32 | 61 ms |
| **12** | **32** | **31 ms** |

**Se indexa en segundo plano** y NOVA te sigue atendiendo. La segunda
vez sólo mira lo nuevo: 0.1 s si no ha cambiado nada.

**El escritorio no entra por defecto.** Aquí tiene 10.288 imágenes que
son recursos de proyectos: dos tercios del tiempo a cambio de ruido.

**CLIP ordena bien, pero no sabe decir «no la tengo».** Esto salió de
medirlo con las 3.793 imágenes ya indexadas, y cambió el diseño. Sus
puntuaciones no son comparables entre consultas distintas:

| consulta | puntuación | ¿existe? |
|---|---|---|
| `an underwater photo of a coral reef` | 0.266 | **no** |
| `a screenshot of Minecraft` | 0.300 | sí |

El z-score tampoco separa (4.28 para el coral contra 3.44 para
Minecraft). El primer diseño tenía un umbral fijo de 0.24 y habría
contestado `BlastTexture.png` a «búscame una foto de un perro» con toda
la seguridad del mundo.

Lo que sí funciona es usar CLIP como se usa bien: **comparando frases
sobre la misma imagen**. Si `a photo of a dog` no le gana a `a photo of
something else`, es que no hay ningún perro. Eso baja el perro a 0.075
de confianza y deja Minecraft en 0.994.

Tampoco es perfecto —`a screenshot of Excel` sigue en 0.944 sin que haya
ninguna— así que **no se usa para filtrar en silencio**, sino para que
NOVA diga si está segura. Con confianza alta afirma; con confianza baja
dice «no estoy segura, pero lo que más se parece es…». Y enseña las
candidatas igual: buscar una foto es mirar candidatas.

**La descripción va en inglés.** CLIP se entrenó así y en español
acierta bastante menos. No hay que traducir nada a mano: el modelo que
decide llamar a la herramienta ya lo hace al rellenar el argumento.

## Las voces

Windows tiene **dos juegos de voces y no son el mismo**. `pyttsx3` ve
SAPI5, que en español son dos y las dos de mujer (Helena y Sabina
«Desktop», de la época de Windows 7). OneCore tiene cinco:

| voz | | |
|---|---|---|
| Pablo | hombre | España |
| Laura | mujer | España |
| Helena | mujer | España |
| Sabina | mujer | México |
| Raul | hombre | México |

A OneCore sólo se llega por WinRT. Desde Python haría falta instalar
`winsdk`; desde PowerShell ya está en el sistema, así que no se instala
nada.

Lo que hacía inviable la idea era el arranque: `powershell.exe` cuesta
**179 ms medidos**, y NOVA sintetiza frase a frase mientras el modelo
escribe. Un proceso por frase se habría cargado el trabajo de que
empiece a hablar pronto. Con el proceso **vivo** leyendo de stdin, el
arranque se paga una vez (0.43 s) y cada frase sale por 11 ms.

El texto le llega en base64. No es adorno: por stdin los acentos se
corrompen según la página de códigos de la consola, y «cañón» llegaba
convertido en otra cosa.

**Y cada petición lleva su número.** Tampoco es adorno, y esto costó un
fallo en uso real: NOVA dejaba de responder un momento y luego decía
«S», «EST» y trozos sueltos.

Sintetizar cuesta 11 ms con la CPU tranquila, pero **1909 ms en el peor
caso con los doce hilos al tope** — medido. Con un juego encima se
agotaba el plazo de tres segundos que había. Y entonces la respuesta
atrasada se quedaba en la cola: la petición siguiente la leía como
suya, daba por escrita una frase que aún no lo estaba, y NOVA
reproducía el WAV anterior **a medio escribir**. Un plazo agotado y
todo iba corrido un puesto para siempre.

Con el número, una respuesta que llega tarde se descarta. Y el plazo
sube a seis segundos, que ahora sale gratis: agotarlo sólo hace caer a
SAPI, no descolocar nada.

Si algo falla —no hay PowerShell, WinRT no responde, el proceso se
muere— NOVA sigue con SAPI. Mejor metálica que muda.

Por defecto sigue siendo **Helena**, la misma de antes. Windows lista a
Pablo primero, y heredar de ahí le habría cambiado el sexo a la voz de
NOVA sin que nadie lo pidiera.

Para cambiarla no hay que saberse los nombres: *«ponte voz de hombre»*,
*«con acento mexicano»*, *«habla más despacio»*. Si ninguna encaja lo
dice, en vez de cambiar a una al azar.

Cuál suena mejor no lo decide ninguna medida, lo decide el oído:

```
.venv\Scripts\python.exe bench\muestras_voz.py
```

deja en `muestras_voz/` la misma frase con las seis, la vieja incluida.

## Presupuesto de recursos

Con la configuración por defecto, medido con `nvidia-smi` y `psutil`:

| pieza | VRAM | notas |
|---|---|---|
| Ollama `qwen3.5:4b` | 3.1 GB | residente con `NOVA_KEEP_ALIVE=24h` |
| faster-whisper `small` | 365 MiB | `int8_float16` |
| Vosk `es-0.42` | — | ~2.3 GB en RAM, no toca la GPU |
| CLIP (buscar imágenes) | — | 607 MB en disco, corre en CPU |
| escritorio de Windows | ~2.8 GB | navegador, juegos, etc. |

Sobra sitio en una tarjeta de 12 GB **mientras no juegues**. En cuanto un
juego pide la tarjeta entera, el driver expulsa al modelo y Ollama sigue
respondiendo desde la CPU sin decir nada. Medido el 27/08 con Star
Citizen abierto (11.0 de 12.3 GB ocupados), preguntando la hora:

| modelo | respuesta | del modelo, en la GPU |
|---|---|---|
| `qwen3.5:4b` | 4.36 - 7.44 s | 10 % |
| `qwen2.5:3b` | **1.11 - 1.19 s** | 48 % |

El pequeño no razona mejor: es que **cabe** en lo que sobra, y por eso va
de 4 a 6 veces más rápido. Así que NOVA se cambia sola.

Cada 5 segundos mira `/api/ps` (`size_vram / size`, la única señal fiable
— la VRAM libre de `nvidia-smi` dice cuánta hay, no si el modelo está
dentro). Por debajo del 85 % se pasa a `NOVA_MODEL_LIGERO` y descarga el
grande; cuando cierras el juego, vuelve. Nunca a mitad de una respuesta,
y el panel pone «· modo ligero» mientras dura, para que no te preguntes
por qué hoy va distinta.

Si `NOVA_MODEL_LIGERO` no está descargado, NOVA lo dice en el log y se
queda con el grande: mejor lenta que muda.

El cuello de botella de NOVA sin juegos no es la memoria: es el camino
del audio y el arranque en frío.

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

Lo que sí se corrigió: **pedir permiso para una cosa ya no cancela las
demás**. «Olvida lo anterior sobre mí y recuerda que soy Messi» son dos
acciones y sólo una pide permiso; la que pedía abortaba la ronda entera
y lo de Messi no se guardaba nunca, sin avisar. Ahora se ejecuta todo lo
que no necesita permiso, lo demás se encola, y la pregunta va al final
contando antes lo hecho. Un «no» tumba sólo esa acción. Y si contestas
otra cosa, tu frase **se atiende** en vez de tirarse a la basura con un
«vale, lo dejo».

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
    charla.py       cuándo NO hay nada que hacer, sólo que contestar
    conversation.py personalidad e historial
    awareness.py    hora, app activa, batería, clima
    polish.py       limpieza de tics y longitud de la voz
  llm/ollama.py     cliente del modelo local
  winrt.py          puente a PowerShell para lo que sólo da WinRT
  vista/
    ocr.py          leer el texto de la pantalla (OCR de Windows)
    clip.py         imágenes y frases como vectores comparables
    album.py        el índice de tus imágenes
  voice/
    listener.py     wake word y captura (Vosk)
    speaker.py      voz, con interrupción
    onecore.py      las voces buenas de Windows (WinRT vía PowerShell)
    onecore.ps1     el proceso que se queda vivo sintetizando
    chime.py        sonido de activación
  tools/            lo que NOVA sabe hacer + permisos
  ui/               orbe, borde de pantalla
tests/              496 tests, sin red ni micrófono
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
