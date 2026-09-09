# Lo que falta

Tres listas: lo que hay que hacer, lo que **se dejó fuera a propósito**
(con el motivo, para que nadie lo "arregle" sin saberlo), y las ideas
que están ahí sin decidir.

---

## Lo primero de todo

### ~~NOVA se cierra sola~~ — resuelto el 01/09

Se cerraba en TODAS las órdenes, siempre justo después de entenderlas.
Se dio por hecho que era un crash nativo, porque el Visor de sucesos
tenía dos firmas feas (`Qt5Core.dll` 0xc0000409 y `MSVCP140.dll`
0xc0000005). **No lo era**: esas dos son del 26/08, y de los seis
cierres del 01/09 Windows no registró ni uno. Una muerte sin evento no
es un crash nativo.

Era un `TypeError`: al añadir la personalidad de los plugins al prompt,
`nova/app.py` empezó a llamar a `build_system_prompt()` con tres
argumentos y la función seguía aceptando dos. Dentro de un slot de Qt,
que es la única excepción de Python que se lleva el proceso entero por
delante — PyQt5 llama a `qFatal()`. Nada que ver con el G435.

Lo que queda de aquello, y conviene no quitar:

- `nova/forense.py`: `faulthandler` + `sys.excepthook` + `threading.excepthook`
  escribiendo a `data/crash.log`. Es lo que hizo visible el traceback.
- El manejador de mensajes de Qt en `nova/app.py`, por lo mismo.
- `_Worker.procesar` y `.confirmar` ya no dejan escapar excepciones: un
  turno roto contesta y libera `_ocupada` en vez de matar el proceso y
  dejar a NOVA diciendo "todavía estoy con lo anterior" para siempre.

### Ojo: los valores que mandan están en `.env`, no en `config.py`

Perdido un rato el 01/09 por esto. `config.py` lleva los defaults **con
sus porqués medidos**, pero `load_dotenv()` hace que `.env` gane. Cuatro
cambios de configuración (tokens, contexto, seguimiento, timeout de
sueño) quedaron inertes en la app real mientras los tests pasaban, y no
se vio hasta probarla hablando.

Al tocar un default hay que tocar los tres sitios: `config.py`, `.env` y
`.env.example`.

### ~~Conseguir la clave del cerebro rápido~~ — hecha el 01/09

Está en `data/groq.key` y probada contra la API de verdad. Tres cosas
que sólo se supieron al tener clave:

- `llama-3.3-70b-versatile`, el modelo que traía por defecto, **ya no
  existe**. Ahora es `openai/gpt-oss-120b`: 0.62 s por respuesta con el
  catálogo entero, contra los 1-2 s del qwen local en caliente (4-7 s
  con un juego abierto).
- Los modelos grandes rellenan TODOS los parámetros y ponen `null` en
  los que no aplican. Groq valida el esquema y devolvía 400. Se relajan
  los opcionales al salir (`relajar_esquemas`) y el registro descarta
  los nulos antes de llamar al handler.
- `qwen/qwen3.8-27b` **agotó la cuota gratuita en la segunda llamada**.
  La capa gratis tiene tope diario de verdad; conviene saberlo antes de
  contar con ella para algo.

Si NOVA dice que el modelo no existe, es que lo han retirado otra vez:
`NOVA_REMOTO_MODEL` con el nombre nuevo.

### ~~"qué estás viendo en mi pantalla" tumbaba el modo rápido~~ — 02/09

En directo, con `NOVA_REMOTO_SIEMPRE=true` ya puesto, se agotaba el
límite a la SEGUNDA frase — no era `elegir_herramientas` (eso ya
funcionaba), era `pantalla.leer`: devuelve hasta 4000 caracteres de OCR
en bruto a propósito, para que el modelo lo resuma, y eso entraba entero
en la segunda ronda de la API junto con el catálogo otra vez. Medido:
3215 tokens sólo esa ronda, sobre un presupuesto de 8000 por minuto.

`acotar_resultados` recorta a 600 caracteres cualquier resultado de
herramienta que vaya camino a la nube — sólo camino a la nube: Ollama no
cobra por token y ahí se le sigue mandando entero. Medido con la misma
frase: 3215 → 2067 tokens, contestando igual de bien. Si otra
herramienta empieza a devolver mucho texto (`web.search`,
`carpeta.resumen`), el recorte ya está puesto sin tocarla una por una.

### La clave, y cómo está puesta hoy

Vive en `data/groq.key` (fuera del repositorio) y está probada contra la
API de verdad. `NOVA_REMOTO_SIEMPRE=true` en el `.env` de Benjahmlp22: arranca
YA en modo rápido, sin pedirlo cada sesión — decisión suya del 02/09.
Decir «modo local» sigue ganando sobre eso.

Para quien clone el repo sin esa preferencia: sin clave el modo rápido
está inerte del todo, y con clave sigue apagado hasta pedir «modo
rápido» — `NOVA_REMOTO_SIEMPRE` es `false` por defecto en el código.

Si algún día NOVA dice que el modelo no existe, es que el proveedor lo
ha retirado: `NOVA_REMOTO_MODEL` con el nombre nuevo y listo.

### Probarla en vivo, de punta a punta

Buena parte de esto ya se hizo el 01 y el 02 de septiembre, hablándole
de verdad y con un juego abierto. Lo verificado en vivo:

- el modo ligero cambiando de modelo con un juego abierto,
- escribir un juego en HTML y jugarlo,
- editar ese juego sin reescribirlo,
- correr los tests de un proyecto de verdad,
- el vigilante negándose a compilar con un juego a pantalla completa,
- «modo rápido» / «modo local», y la caída sola a local al agotar cuota.

**Lo que sigue sin verse correr de verdad:**

- `organizar.hacerlo` contra los 627 archivos reales de Descargas (sólo
  se ha probado en seco y con carpetas de mentira). Es lo que más
  incertidumbre queda: mueve archivos de Benjahmlp22.
- la búsqueda de imágenes con una consulta que importe,
- los botones de mudo y sordo.

### Calibrar el umbral de confianza de las imágenes

`CONFIANZA_SEGURA = 0.60` en `nova/vista/album.py` **es un número puesto
a ojo**, no medido contra lo que una persona consideraría un acierto.
Puede estar permisivo o estricto de más.

Para calibrarlo: busca diez cosas que sepas que tienes y diez que sepas
que no, apunta la confianza de cada una (sale en `data` del resultado) y
mira dónde está la frontera de verdad.

---

## Ideas sin decidir

Ninguna está empezada. Están aquí porque salieron en conversación y
parecían valer la pena.

| idea | por qué | dificultad |
|---|---|---|
| Tienda de plugins | hoy se instalan copiando la carpeta en `data/plugins`. Un catálogo en un repo público bastaría: cero claves, cero cuentas | media |
| Renombrar archivos por voz | "renombra la factura a factura-luz" no existe hoy | baja |
| Modo reunión | callarse sola cuando detecta Discord/Teams con el micro en uso | media |
| Historial consultable en el panel | ahora sólo se ve el último turno | media |
| Describir imágenes, no sólo buscarlas | necesita un modelo de visión en Ollama (~3 GB) y 1-3 s por imagen | media |
| Control del reproductor | play/pausa por teclas multimedia; "qué suena" leyendo el título de ventana | baja |
| Medir `gemma4:e4b` | está instalado y nunca se ha comparado con `qwen3.5:4b` | baja |
| Más grabaciones trampa del wake word | el 8/8 está medido sobre 14 frases, que son pocas | baja |

---

## Dejado fuera A PROPÓSITO

No son olvidos. Si vas a cambiarlos, sabe primero por qué están así.

**Borrar archivos fuera de `workspace/`.** `organizar.py` mueve, nunca
borra: ni un `unlink` en todo el módulo. Un asistente de voz que borra
por lo que cree haber oído es una máquina de perder cosas.

**Rutas libres en las herramientas de carpetas.** Sólo Descargas,
Escritorio, Documentos, Imágenes, Música y Vídeos. "Abre
`C:\Windows\System32`" no es una orden que deba existir.

**Abrir ejecutables encontrados por búsqueda.** `archivo.abrir` se niega
con `.exe`, `.msi`, `.bat` y compañía. Un instalador recién caído en
Descargas es exactamente lo que no debe lanzarse porque una frase sonó
parecida. Para programas está `app.open`, que va contra un índice de lo
instalado.

**El escritorio en el índice de imágenes.** En el PC de referencia tiene
10.288 imágenes que son recursos de proyectos (sprites, texturas). Dos
tercios del tiempo de indexado a cambio de ruido.

**Una carpeta "Otros" al ordenar.** Lo que no se reconoce se queda
quieto. Un cajón de sastre mueve el problema, no lo resuelve.

**`torch`.** Son 2.5 GB para *ejecutar* un modelo ya entrenado.
`onnxruntime` lo hace en 31 ms por imagen. Si alguien propone traerlo,
que traiga también la medida de qué mejora.

**Prometer que los plugins son seguros.** No lo son y no pueden serlo:
Python no encierra código ajeno. Lo que hay es declaración de permisos,
el código a la vista y un revisor de descuidos. Si alguien añade una
«caja de arena», que traiga primero la prueba de que aguanta.

**Firebase para la tienda de plugins.** Choca con la regla número 7
(«ni API keys, ni datos saliendo del PC»). Un catálogo en un repo
público hace lo mismo sin cuentas ni claves.

**Traducir las consultas de imagen con el LLM.** No hace falta: el
modelo que decide llamar a `imagen.buscar` ya rellena el argumento en
inglés, que es lo que CLIP necesita.

---

## Del repositorio

- **Proteger `main`** cuando el repo pase a público: en privado hace
  falta GitHub Pro. Settings → Branches, exigiendo el check `probar`.
- **Antes de hacerlo público**, repasa que el README y la bitácora
  llevan datos del PC de referencia (627 archivos en Descargas, 16.699
  imágenes, el hardware) como justificación de las decisiones. Son
  útiles técnicamente, pero son tuyos.

## Cómo medir lo que toques

Todo lo del proyecto está decidido con números, y hay bancos de pruebas
para rehacerlos:

```bash
.venv\Scripts\python.exe bench\bench_herramientas.py --detalle
.venv\Scripts\python.exe bench\bench_stt.py
.venv\Scripts\python.exe bench\bench_wake.py
.venv\Scripts\python.exe bench\bench_cadenas.py
.venv\Scripts\python.exe bench\muestras_voz.py
```

Dos avisos que costaron tiempo:

**No cronometres desde fuera.** El ruido de la máquina es mayor que casi
cualquier efecto que quieras medir. Dos medidas del mismo cambio dieron
+230 ms y +408 ms cuando el valor real era 0. Usa lo que Ollama ya te
da: `prompt_eval_count` y `prompt_eval_duration`.

**Repite antes de concluir.** El modelo va a temperatura 0.6. Una sola
vuelta no dice nada. El banco de herramientas se ejecutó 6 veces con dos
temperaturas antes de aceptar que 20/34 era real y no ruido — y menos
mal, porque lo era.
