# Bitácora

Lo que se hizo, cuándo, y **por qué se hizo así y no de la otra forma**.

Esto no es un changelog. Un changelog dice *qué* cambió y eso ya está en
`git log`. Aquí está lo que costó averiguar: las medidas que decidieron
cada cosa y, sobre todo, **los caminos que parecían buenos y no lo
eran**. Si vienes a tocar este proyecto —persona o IA— lee esto antes de
"arreglar" algo que ya se intentó.

Formato de cada entrada: qué se creía, qué se midió, qué se decidió.

---

## Agosto 2026 — NOVA5 sale de NOVA4

Punto de partida: NOVA4 funcionaba pero era lenta y sorda a ratos. Se
copió tal cual (primer commit, "NOVA4 as-is") para poder comparar
siempre contra el original.

### Reconocimiento de voz: dos etapas

**Lo que se creía:** que el modelo grande de Vosk (2.3 GB) detectaría
mejor el nombre que el pequeño (58 MB). El usuario ya lo había cambiado
al grande por eso mismo.

**Lo medido** (14 frases: 8 con "nova", 6 trampas tipo "no va a
funcionar el mando"):

| modelo | despierta | falsas | arranque |
|---|---|---|---|
| vosk-model-es-0.42 (2.3 GB) | 4/8 | 4/6 | 47.2 s |
| vosk-model-small (58 MB) | 5/8 | 3/6 | 0.4 s |
| **small + gramática `["nova"]`** | **8/8** | 5/6 | **0.4 s** |

**Decidido:** el pequeño con gramática restringida. El grande era peor
en las dos columnas *y* no admite gramática ("Runtime graphs are not
supported by this model"). Sus 2.3 GB sólo compraban 47 segundos de
arranque sordo.

Que salte de más en la etapa 1 da igual: sólo abre la puerta, y la
etapa 2 (Whisper con `initial_prompt`) confirma antes de que suene nada.

En español, **"nova" y "no va" son la misma secuencia de fonemas**. No
hay forma acústica de separarlos; sólo el contexto lo hace.

### Transcripción: por qué `medium` y no el más grande

| modelo | WER | frases exactas | tiempo |
|---|---|---|---|
| Vosk es-0.42 | 17.9% | 10/20 | 0.76 s |
| whisper small | 16.1% | 10/20 | 0.30 s |
| whisper small + vocabulario | 9.3% | 11/20 | 0.32 s |
| whisper medium | 12.9% | 13/20 | 0.61 s |
| **whisper medium + vocabulario** | **3.5%** | **17/20** | 0.50 s |
| whisper large-v3-turbo | 24.1% | 8/20 | 0.61 s |

`turbo`, que es más grande, fue **el peor de todos**. En órdenes de
cinco palabras el tamaño no compra nada.

### El camino del audio (esto costó una tarde entera)

**Primer diagnóstico, EQUIVOCADO:** el corpus grabado salía inservible y
se culpó a las mejoras de audio de Windows.

**La causa real:** llamar a `InputStream.read()` en un bucle de Python.
Se pierden bloques entre lectura y lectura. La captura tiene que ser
**por callback**, siempre.

Otras dos que salieron de ahí:

- WASAPI en modo compartido sólo abre a su frecuencia nativa. Hay que
  capturar a esa y remuestrear uno mismo.
- El umbral de voz se calculaba con la media del ruido y salía **por
  encima de la propia voz del usuario** (0.0425 contra 0.015-0.026 que
  daba él hablando). Se cambió a la mediana con un tope entre 0.004 y
  0.012.

### El segfault del arranque

Importar PyQt5 **antes** de construir `WhisperModel` mata el proceso sin
mensaje. Por eso existe `nova/bootstrap.py`, que no importa PyQt5 nunca,
y `run.py` carga el transcriptor antes de tocar la interfaz.

Si alguna vez NOVA muere al arrancar sin decir nada, mira ahí primero.

### CUDA en Windows

CTranslate2 necesita **las dos cosas**: `os.add_dll_directory` *y* meter
la ruta en `PATH`. Con una sola no encuentra las DLL. Está en
`nova/voice/cuda.py`.

---

## 27 de agosto — la ronda larga

Todo lo de abajo salió del mismo día, tirando del hilo de lo que el
usuario reportaba usándola de verdad.

### La VRAM: por qué hay un "modo ligero"

**El síntoma:** con Star Citizen abierto, NOVA tardaba 4-7 segundos en
contestar "qué hora es". Ollama respondía con normalidad; sólo que desde
la CPU.

**Lo medido** (11.0 de 12.3 GB de VRAM ocupados):

| modelo | respuesta | del modelo, en la GPU |
|---|---|---|
| qwen3.5:4b | 4.36 - 7.44 s | 10 % |
| **qwen2.5:3b** | **1.11 - 1.19 s** | 48 % |

El pequeño no razona mejor: **cabe**. De 4 a 6 veces más rápido por esa
única razón.

**La señal fiable es `size_vram / size` de `/api/ps`.** Mirar la VRAM
libre con `nvidia-smi` NO sirve: dice cuánta queda, no si el modelo está
dentro.

### Las voces: 13 veces más rápidas, y por accidente

Se buscaban voces menos robóticas. Windows tiene **dos juegos de voces**
y no son el mismo: `pyttsx3` ve SAPI5 (dos en español, las dos de
mujer), y OneCore tiene cinco, incluidas Pablo y Raul, que son de
hombre.

Lo inesperado al medirlo:

| sintetizador | por frase |
|---|---|
| SAPI, motor nuevo cada vez | 142 ms |
| **OneCore, proceso vivo** | **11 ms** |

SAPI pagaba construir un motor entero por frase, que es el precio de
esquivar el reciclado roto de `pyttsx3`. Como NOVA habla frase a frase
mientras el modelo escribe, eran **130 ms de más en cada una**. No era
sólo "más voces": era el segundo trozo de latencia más gordo que
quedaba.

### Buscar imágenes: el número que decide la arquitectura

Hay 16.699 imágenes en el PC de referencia. Con eso, la decisión se toma
sola:

| cómo | por imagen | las 16.699 |
|---|---|---|
| un modelo de visión describiendo cada una | 1 - 3 s | **5 a 14 horas** |
| CLIP (vectores comparables) | 47 ms | **13 minutos** |

**Y el hallazgo que cambió el diseño: CLIP ordena bien pero NO SABE
DECIR "no la tengo".** Sus puntuaciones no son comparables entre
consultas:

| consulta | puntuación | ¿existe? |
|---|---|---|
| `an underwater photo of a coral reef` | 0.266 | **no** |
| `a screenshot of Minecraft` | 0.300 | sí |

El z-score tampoco separa (4.28 para el coral contra 3.44 para
Minecraft). El primer diseño tenía un umbral fijo de 0.24 y habría
contestado `BlastTexture.png` a "búscame una foto de un perro" con toda
la seguridad del mundo.

**Lo que sí funciona:** usar CLIP como se usa bien, comparando frases
sobre la misma imagen. Si `a photo of a dog` no le gana a `a photo of
something else`, no hay perro. Eso baja el perro a 0.075 de confianza y
deja Minecraft en 0.994. Sigue sin ser perfecto, así que no filtra en
silencio: NOVA dice cuándo no está segura.

### Los tres bugs que se oían raro

**"S", "EST" y trozos sueltos.** No eran palabras: era audio cortado.
Sintetizar cuesta 11 ms con la CPU tranquila y **1909 ms en el peor caso
con los 12 hilos al tope** (medido). Con un juego encima se agotaba el
plazo de 3 s, y entonces la respuesta atrasada se quedaba en la cola: la
petición SIGUIENTE la leía como suya y a partir de ahí todo iba corrido
un puesto. NOVA daba por escrita una frase que aún no lo estaba y
reproducía el WAV anterior a medio escribir.

Arreglado numerando cada petición (`nova/winrt.py`). **Es la misma clase
de fallo que ya había aparecido en el OCR** (dos líneas de respuesta y
una sola lectura); allí se arregló en el guion y no en el puente, así
que seguía vivo en las voces. Lección: arreglar el patrón, no la
instancia.

**Dormirse con un "sí/no" en el aire.** Decir "adiós" con una
confirmación pendiente la dejaba en la cola. La próxima vez que la
despertaras, un "sí" a *cualquier otra cosa* habría ejecutado esa acción
olvidada — y podía ser un borrado.

**Pedir permiso cancelaba el resto.** "Olvida lo anterior sobre mí y
recuerda que soy Messi" son dos acciones y sólo una pide permiso. La que
pedía abortaba la ronda entera y lo de Messi no se guardaba nunca, sin
avisar.

### El bug de fondo: `num_ctx`

**El síntoma:** "es bastante tonto en general". Y lo era:

```
«cierra Spotify»  ->  voz_cambiar(descripcion="ponte voz de hombre")
```

Sin ninguna relación con lo pedido, reproducible 6 de 6 veces en
procesos nuevos.

**La causa:** `num_ctx` nunca se había fijado, **ni en NOVA4**. Ollama
usa 2048 tokens si nadie dice lo contrario, y un turno con el catálogo
de 52 herramientas pesa 2050. El modelo llevaba quién sabe cuánto
respondiendo a **un prompt cortado a la mitad**.

Sólo dejó de caber ese día, cuando el catálogo creció de 27 a 52 y cruzó
el límite por casualidad.

| | aciertos (banco de 34 casos) |
|---|---|
| antes | 20/34, estable en 6 vueltas y dos temperaturas |
| con `num_ctx=8192` | **34/34** |

Cuesta 280 MB de VRAM. Nada.

**Aviso para quien venga:** antes de culpar al modelo o al tamaño del
catálogo, comprueba `prompt_eval_count` contra el `num_ctx` real. El
síntoma de un prompt truncado no se parece en nada a su causa.

---

## 1 de septiembre — plugins y reparto de recursos

### El reparto: un vigilante para toda la máquina

Antes sólo se miraba la VRAM, y sólo para cambiar de modelo. Faltaba
todo lo demás: podías estar jugando y NOVA seguir indexando 16.699
imágenes con ocho hilos.

Ahora `nova/recursos.py` mide CPU, RAM, VRAM y si hay un juego a
pantalla completa, y dice **holgado**, **justo** o **apretado**. Todo lo
caro pregunta antes de ponerse.

Los umbrales son medidos, no copiados:

| umbral | valor | de dónde sale |
|---|---|---|
| CPU «justo» | 70 % | con los 12 hilos al tope, sintetizar una frase pasa de 11 ms a **1909 ms** |
| VRAM «justo» | 85 % | el punto en que Ollama deja parte del modelo fuera de la tarjeta |
| juego delante | — | cuenta como apretado aunque los números den bien: los fotogramas son suyos |

Con el juego abierto, pedirle que repase las imágenes ahora contesta:
«Ahora no: tienes StarCitizen a pantalla completa… Dímelo cuando cierres
StarCitizen». Y si te pones a jugar a mitad del repaso, se para sola.

**Un fallo que salió al probarlo:** la primera medición decía «CPU al 0
por ciento» con la máquina al 90. `cpu_percent(interval=None)` mide
desde la llamada anterior, y sin una anterior devuelve 0.0. Cebar el
contador y preguntar acto seguido no basta, porque no ha pasado tiempo.
La primera vez usa una ventana de 150 ms.

### Plugins: lo que este sistema NO promete

Un plugin con código Python corre con los permisos del usuario. **Python
no tiene forma real de encerrar código ajeno.** No hay caja de arena, y
quien diga lo contrario está vendiendo humo.

Así que aquí no se promete seguridad, se hace algo más modesto: el
plugin declara lo que necesita, se le enseña al usuario en castellano
antes de activarlo, el código se lee entero dentro del panel, y un
revisor avisa de lo que huele mal.

El revisor analiza el **árbol sintáctico**, no el texto: buscando
palabras, un comentario que diga «nunca uses eval» daría un susto.

Probado contra código malicioso escrito a mano: caza `rmtree`, `eval`,
`subprocess`, `socket` y `shutil`. En la primera versión **se le coló**
`getattr(__builtins__, "e" + "val")`, que es el ofuscado de manual —
ahí `__builtins__` es un `Name` y no un `Attribute`, y sólo se miraban
los `Attribute`. Ahora se caza por dos vías. En el panel pone que esto
es un detector de descuidos y no un antivirus.

**La distinción que hace todo el trabajo:** un plugin de sólo datos
(personalidad, voz, frases) es imposible que haga daño y se activa con
un clic. Uno con código pide leerlo antes, y **por voz no se activa**:
decir un nombre de pasada no es consentimiento informado para ejecutar
código de otra persona.

Nada se ejecuta al arrancar. Importar un módulo YA ejecuta su cuerpo,
así que el código sólo se importa si el plugin está activo, pidió el
permiso, y lo activaste tú.

### Y una cosa que resultó no estar rota

Se reportó que NOVA «no ve la pantalla». Probado con seis formas de
pedirlo, cinco llaman a `pantalla.leer` y contestan bien («Tienes
delante Star Citizen, la misión JUNIOR RANK en el CARGO HALL»). Lo que
fallaba era el `num_ctx` de la entrada anterior: con el prompt cortado,
la elección de herramienta era un desastre.

**Antes de arreglar algo, comprueba que sigue roto.**

---

## 1-2 de septiembre — programar, y el precio de la nube

### NOVA se cerraba sola: no era un crash nativo

**Lo que se creía:** un fallo nativo. El Visor de sucesos tenía dos
firmas feas —`Qt5Core.dll` 0xc0000409 y `MSVCP140.dll` 0xc0000005— y la
sospecha apuntaba a un conflicto de driver de audio con el G435.

**Lo medido:** esas dos firmas eran del 26/08. De los **seis** cierres
del 01/09, Windows no registró **ni uno**. Un crash nativo siempre deja
evento; una muerte sin evento, no.

**Lo que era:** un `TypeError`. Al añadir la personalidad de los plugins
al prompt, `app.py` llamaba a `build_system_prompt()` con tres
argumentos y la función seguía aceptando dos. Dentro de un slot de Qt,
que es la única excepción de Python que se lleva el proceso entero por
delante: PyQt5 llama a `qFatal()` y aborta.

**Lo que quedó:** `nova/forense.py` (faulthandler + excepthook de
proceso y de hilo → `data/crash.log`) y un manejador de mensajes de Qt.
Dos días después, ese manejador cazó el crash siguiente —abrir el panel
de plugins construía un QWidget desde el hilo trabajador— con el aviso
literal en el log. De adivinar a leerlo.

### Las herramientas de código

Ver, buscar, escribir, editar, ejecutar y probar, acotadas a la carpeta
de proyectos y al Escritorio. Tres cosas que salieron probándolo:

- **El cronómetro existía y no servía.** `subprocess.run(timeout=...)`
  con un `sleep(30)` lanzado por shell tardaba **30 segundos** en
  volver: mata el `cmd.exe` y luego espera a que se cierren las
  tuberías, que las tiene el nieto. Ahora es `Popen` + `taskkill /T`:
  1.3 s medidos.
- **`max_tokens=350` hacía imposible escribir código**, y de la peor
  forma: qwen3.5 razona antes de responder y ese razonamiento consume
  presupuesto, así que se lo gastaba entero pensando y devolvía
  **vacío** (`done_reason=length`, `content=''`). Sin error ni traceback.
- **Editar, no reescribir.** Con sólo `escribir`, "cámbiale el color a
  la serpiente" obligaba a regenerar las 120 líneas de memoria.
  `codigo.editar` toca un trozo: el diff de esa orden fue **una línea**.

### El cerebro en la nube: los números

Escrito a ciegas y luego probado con clave de verdad, que enseñó cosas
que no se podían suponer:

- `llama-3.3-70b-versatile`, el modelo por defecto, **ya no existía**.
  Los proveedores retiran modelos cada pocos meses.
- Los modelos grandes rellenan **todos** los parámetros y ponen `null`
  en los que no aplican. Groq valida el esquema y devolvía 400,
  tumbando el turno entero.
- **El límite no es diario, son 8000 tokens POR MINUTO.** Un turno
  gastaba 3849, de los cuales 3683 eran de entrada: casi todo el
  catálogo de 68 herramientas. Dos turnos por minuto, y el tercero
  esperando 23 segundos.

**Lo que se hizo:** mandarle sólo las 18 herramientas que vienen a
cuento (medido: 18/18 aciertos con 18, 17/18 con 12) y recortar los
resultados largos. El turno bajó a ~1800 tokens, de 2 a 5 por minuto.

**Lo que se decidió:** dejarlo **apagado**. Cinco frases por minuto no
dan para vivir encendido, y para "qué hora es" el modelo local va
sobrado y sin límite. La nube se pide a mano justo cuando hace falta.

### Una mentira en el propio código

`remoto.py` redactaba errores que decían "Vuelvo a lo local" y **nadie
cambiaba el cerebro de vuelta**: el agente seguía apuntando a la nube y
repetía esa frase para siempre. Si un mensaje promete una acción, algo
tiene que ejecutarla.

---

## Cosas que se probaron y NO funcionaron

Guardadas para que nadie las repita.

- **El modelo grande de Vosk.** Peor que el pequeño en todo. Ver arriba.
- **whisper large-v3-turbo.** El peor WER de los seis probados (24.1%).
- **Un umbral fijo de parecido en CLIP.** Da falsos positivos con total
  seguridad. Hay que comparar frases sobre la misma imagen.
- **Restar luz al borde de pantalla pintando negro encima.** Sobre una
  ventana translúcida el negro no resta alfa: oscurece. Deja una neblina
  gris encima del juego. Se pinta por tramos.
- **Verde menta para "hablando".** Se quedaba a 40° de tono del azul de
  "escuchando" y a tamaño de barra pequeña se leían igual. Verde puro
  está a ~85°.
- **Culpar al tamaño del catálogo de herramientas.** Medido: 17/20 con
  27 herramientas y 17/20 con 48, con los mismos fallos. El problema
  estaba en `num_ctx`.
- **Buscar patrones peligrosos en el código como texto.** Un comentario
  que diga «no uses eval» daba un aviso. Hay que analizar el árbol
  sintáctico.
- **Fiarse del Visor de sucesos sin mirar la FECHA.** Dos firmas de
  crash nativo de una semana antes costaron una sesión entera buscando
  corrupción de memoria donde había un `TypeError`.
- **Recortar las descripciones de las herramientas para ahorrar
  tokens.** Ahorraba 200 de 4400. Lo que pesa es el NÚMERO de
  herramientas, no lo largas que sean sus descripciones.
- **Reintentar un 429 esperando lo que diga la cabecera.** Suena bien
  hasta que la cabecera pide 23 segundos. Se espera si son menos de
  cuatro; por encima, mejor contestar con el modelo de casa.
- **Medir latencia cronometrando desde fuera.** El ruido de la máquina
  es mayor que el efecto. Dos medidas del mismo cambio dieron +230 ms y
  +408 ms; el valor real era 0. Usa `prompt_eval_count` y
  `prompt_eval_duration`, que Ollama da hechos.

---

## Reglas que no se tocan sin preguntar

Heredadas de NOVA4 y confirmadas por todo lo de arriba:

1. **Un solo proceso.** Nada de Electron ni WebSocket.
2. **`127.0.0.1`, nunca `localhost`.** En Windows el resolver prueba
   IPv6 primero y eso añadía 2.5 s a cada llamada.
3. **Los resultados de las herramientas vuelven al modelo como frases en
   español**, no como JSON. Es lo que evita que reinterprete y conteste
   otra cosa.
4. **El relleno se quita con código, no con el prompt.** Y por extensión:
   la longitud, los permisos, y a quién se le enseña el catálogo. Un
   modelo pequeño no cumple una regla escrita; cúmplela tú por él.
5. **Los permisos los decide el registro, no el LLM.**
6. **Conversación continua.** Tras responder sigue escuchando.
7. **Local y gratis por defecto.** Ni suscripciones ni datos saliendo
   del PC. Los modelos se bajan una vez y después nada toca la red.

   **Matizada el 02/09**, y conviene leer el matiz entero antes de
   tocarlo. Existe un cerebro opcional en la nube (`nova/llm/remoto.py`,
   capa gratuita, sin tarjeta) porque un modelo de 4B no programa y con
   un juego delante ni cabe en la GPU. Lo que NO cambia:

   - sin clave en `data/groq.key` el módulo es inerte y no contacta con
     nadie;
   - con clave sigue apagado hasta que Benja diga «modo rápido» en voz
     alta, cada sesión, y no se guarda en disco;
   - «modo local» gana por encima de cualquier configuración.

   La regla original decía "100% local". Se cambió el número, no el
   principio: **que los datos salgan sigue siendo una decisión suya y
   nunca un efecto secundario.**
