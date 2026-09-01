# Lo que falta

Tres listas: lo que hay que hacer, lo que **se dejó fuera a propósito**
(con el motivo, para que nadie lo "arregle" sin saberlo), y las ideas
que están ahí sin decidir.

---

## Lo primero de todo

### Probarla en vivo, de punta a punta

**Nada de lo hecho el 27 de agosto se ha visto correr con micrófono real
y jugando a la vez.** Está verificado con 496 tests y con scripts
sueltos que llaman a las piezas por separado, pero la aplicación entera,
con voz de verdad, no se ha arrancado en toda esa sesión.

Concretamente, sin verificación real:

- el modo ligero cambiando de modelo con un juego abierto,
- los botones de mudo y sordo,
- `organizar.hacerlo` contra los 627 archivos reales de Descargas (sólo
  se ha probado en seco y con carpetas de mentira),
- el arreglo del audio cortado,
- la búsqueda de imágenes con una consulta que importe.

Es lo más barato de hacer y lo que más incertidumbre quita. Antes de
construir nada encima, arráncala.

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
