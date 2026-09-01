# Cómo se trabaja aquí

Para personas y para IAs. Si eres una IA y sólo vas a leer un archivo,
que sea `docs/BITACORA.md`: ahí está lo que ya se intentó y no funcionó.

---

## Lo primero: medir antes de escribir

Es la regla que da forma a todo el proyecto. **Casi cada número del
código tiene un comentario al lado diciendo de dónde salió**, y muchos
contradicen lo que parecía obvio:

- el modelo de Vosk grande resultó ser *peor* que el pequeño,
- `whisper large-v3-turbo` dio el peor WER de seis,
- las voces nuevas salieron 13 veces más rápidas, cosa que nadie
  buscaba,
- el catálogo de herramientas no costaba nada de latencia, al revés de
  lo que decía el cronómetro.

Si vas a cambiar un default, trae la medida. Si vas a añadir algo, mide
lo que cuesta.

**Dos trampas que ya costaron tiempo:**

No cronometres desde fuera. El ruido de la máquina es mayor que casi
cualquier efecto. Dos medidas del mismo cambio dieron +230 ms y +408 ms
cuando el valor real era cero.

Repite antes de concluir. El modelo va a temperatura 0.6; una sola
vuelta no dice nada.

## Todo en español

Código, comentarios, docstrings, mensajes de NOVA, README y **mensajes
de commit**. Los nombres de las funciones también (`interpretar_cuando`,
`_a_porcentaje`, `es_pura_charla`).

La excepción son las descripciones que se le pasan a CLIP, que se
entrenó en inglés.

## Los comentarios explican el porqué, no el qué

Mal:

```python
# Convierte a porcentaje
nivel = _a_porcentaje(level)
```

Bien (lo que hay de verdad en `nova/tools/audio.py`):

```python
# Hace falta porque el modelo no respeta el esquema. Visto de verdad:
# pidiéndole "baja el volumen de Spotify" mandó `level="0.5"`, y el
# `int()` de antes reventaba con "invalid literal for int()". NOVA
# contestaba con el error de Python en alto.
```

## Commits

Uno por cambio con sentido, y el mensaje cuenta **el porqué**. Los del
historial son largos a propósito: explican el síntoma, la medida y la
decisión. Mira cualquiera de agosto de 2026 como modelo.

Si te sale un commit que toca cinco cosas distintas, sepáralo.

## Ramas

`main` siempre verde: tests en verde y `ruff` limpio.

Todo lo demás en una rama por tema, y se borra al fusionar:

```
voz/…        voces, TTS, wake word, transcripción
vision/…     pantalla, OCR, búsqueda de imágenes
archivos/…   carpetas, organizar, buscar
ui/…         panel, orbe, borde de pantalla
cerebro/…    agente, prompt, herramientas, memoria
fix/…        arreglos que no encajan en las de arriba
```

Ejemplo: `vision/describir-imagenes`, `fix/timeout-tts`.

## Tests

496 y **ninguno necesita micrófono, GPU ni red**. Si un test tuyo los
necesita, está mal escrito: inyecta lo que haga falta, como hacen los
demás.

Los que necesitan los modelos de CLIP (607 MB) se saltan solos con
`pytest.skip` si no están.

```bash
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m ruff check .
```

Lo que más valor tiene aquí son los tests de **lo que NO debe pasar**.
`tests/test_organizar.py` es casi entero de eso: que no borre, que no se
salga de la carpeta, que no pise archivos, que no toque accesos
directos. Mueve 611 archivos del usuario de una vez; cada test existe
porque el fallo correspondiente sería irreversible.

## Dónde tocar cada cosa

```
nova/
  app.py          orquestación y máquina de estados
  config.py       TODA la configuración, con los porqués medidos
  bootstrap.py    arranque sin PyQt5 (ver el segfault en la bitácora)
  winrt.py        puente a PowerShell para lo que sólo da WinRT
  doctor.py       diagnóstico de audio cuando algo no suena
  core/
    agent.py      bucle LLM ↔ herramientas
    charla.py     cuándo NO hay que enseñarle el catálogo
    conversation.py  personalidad e historial
    awareness.py  hora, app activa, batería, clima
    polish.py     quitar coletillas y recortar para la voz
  llm/ollama.py   cliente del modelo local
  voice/          wake word, captura, transcripción, voz
  vista/          OCR de pantalla y búsqueda de imágenes (CLIP)
  tools/          lo que NOVA sabe hacer, y los permisos
  ui/             panel, orbe, borde de pantalla
```

### Añadir una herramienta

Todo lo que NOVA sabe hacer vive en `nova/tools/` y se declara igual:

```python
reg.register(Tool(
    name="carpeta.resumen",
    description="Cuántos archivos hay en una carpeta suya, cuánto ocupan y de qué tipo",
    handler=resumen,
    schema={"type": "object", "properties": {"nombre": {"type": "string"}}},
    risk=Risk.SAFE,
    responde_sola=True,
))
```

Cuatro cosas que decidir:

**`risk`.** `SAFE` no pregunta nunca; `MEDIUM` según la política;
`DANGEROUS` siempre. Lo decide el registro, **nunca el modelo**.

**`responde_sola`.** Ponlo si el mensaje que devuelve YA es la respuesta
completa para el usuario. Entonces sale tal cual y se ahorra una vuelta
al modelo. **No** lo pongas si devuelve material en bruto que hay que
resumir (`web.search`, `pantalla.leer`).

**`resumir`.** Sólo si es `DANGEROUS` y la frase genérica no avisa de
nada. "¿Confirmas que quiero ordenar descargas?" no dice gran cosa;
"¿confirmas que quiero mover 611 archivos a 8 carpetas?" sí.

**El `message`.** Es lo que oye el usuario Y lo que lee el modelo, así
que escríbelo como una frase que se pueda decir en alto. Los tamaños en
"1.8 gigas", no en bytes.

Después: registrarla en `nova/tools/__init__.py` y añadir casos a
`bench/bench_herramientas.py` — incluido alguno de "aquí NO se llama a
esta".

## Antes de dar algo por terminado

1. `ruff check .` limpio
2. `pytest tests -q` en verde
3. `bench/bench_herramientas.py` si tocaste herramientas o el prompt
4. **Arrancar NOVA y probarlo hablando.** Los tests no oyen.
