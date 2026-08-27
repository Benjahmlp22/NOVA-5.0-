"""¿Acierta NOVA la herramienta? ¿Y se calla cuando no hace falta ninguna?

El catálogo pasó de 27 a 48 herramientas en una tarde y hubo que
comprobar qué costaba eso. El prefill no cuesta nada (medido: 639 tokens
más, 0 ms), pero **elegir** entre 48 opciones sí.

    .venv\\Scripts\\python.exe bench\\bench_herramientas.py
    .venv\\Scripts\\python.exe bench\\bench_herramientas.py --modelo qwen2.5:3b

Cada caso dice qué herramienta se espera, o `None` para "aquí no se
llama a nada". Los segundos importan menos que los aciertos: una
respuesta rápida y equivocada no sirve.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nova.config import CONFIG  # noqa: E402
from nova.core.agent import Agent  # noqa: E402
from nova.core.awareness import Awareness  # noqa: E402
from nova.core.conversation import build_system_prompt  # noqa: E402
from nova.llm.ollama import OllamaClient  # noqa: E402
from nova.tools import build_registry  # noqa: E402

# (lo que se dice, qué herramienta toca o None)
#
# La mitad de los casos son "no llames a nada". Eso no es relleno: el
# fallo que se vio en uso real fue decir "adiós" y que el modelo llamara
# a memory.forget, que borra cosas.
CASOS: list[tuple[str, str | None]] = [
    # Charla: aquí no se toca nada
    ("hola", None),
    ("gracias", None),
    ("adiós", None),
    ("vale", None),
    ("cómo estás", None),
    ("cuánto es dos más dos", None),
    ("quién eres", None),
    ("no, nada", None),
    ("cuéntame un chiste", None),
    ("qué tal el día", None),
    # Órdenes claras
    ("abre Discord", "app.open"),
    ("cierra Spotify", "app.close"),
    ("baja el volumen de Spotify", "app.volume"),
    ("sube el volumen del sistema al setenta", "pc.volume"),
    ("cómo va el PC", "pc.status"),
    ("hazme una captura de pantalla", "screen.capture"),
    ("busca en internet el precio de la 5070", "web.search"),
    ("recuérdame sacar la basura en diez minutos", "recordatorio.crear"),
    ("qué recordatorios tengo", "recordatorio.listar"),
    ("recuerda que mi color favorito es el verde", "memory.remember"),
    ("apunta que tengo que llamar al dentista", "nota.apuntar"),
    ("qué pone en la pantalla", "pantalla.leer"),
    ("resume lo que tengo copiado", "portapapeles.leer"),
    ("ponte voz de hombre", "voz.cambiar"),
    ("habla más despacio", "voz.velocidad"),
    ("qué ventanas tengo abiertas", "ventana.listar"),
    ("cambia a Chrome", "ventana.cambiar"),
    ("busca una imagen de un perro", "imagen.buscar"),
    # Enseñar antes lo que va a mover es lo correcto: son 611 archivos.
    ("ordena mis descargas", "organizar.revisar"),
    ("abre la carpeta de descargas", "carpeta.abrir"),
    ("cuánto ocupan mis descargas", "carpeta.resumen"),
    ("cuál es el archivo más grande de descargas", "carpeta.mas_grandes"),
    ("qué he bajado hoy", "carpeta.recientes"),
    ("dónde está el pdf de la factura", "archivo.buscar"),
]


def probar(llm: OllamaClient, reg, prompt: str) -> tuple[int, int, float, list]:  # noqa: ANN001
    """Por el AGENTE entero, no por el modelo pelado.

    Es lo único que mide lo que pasa de verdad: por el camino hay un
    atajo determinista para la charla y otro para "recuerda que...", y
    los dos existen justamente porque el modelo no acierta solo.

    Se apuntan las herramientas que INTENTA, no las que salen bien.
    `tools_used` sólo lista las que devolvieron ok, y "baja el volumen de
    Spotify" con Spotify cerrado elige bien y falla: eso es un acierto de
    NOVA, no un fallo.
    """
    aciertos = falsos = 0
    fallos = []
    t0 = time.perf_counter()
    for frase, esperada in CASOS:
        intentadas: list[str] = []

        def apuntar(etapa, herramienta, dato, _lista=intentadas):  # noqa: ANN001, ARG001
            if etapa == "tool" and herramienta:
                _lista.append(herramienta)

        agente = Agent(llm, reg, on_status=apuntar)
        r = agente.run(prompt, [], frase)
        #  incluye lo que resolvió el atajo determinista de
        # "recuerda que..." (memory.remember), que responde SIN pasar
        # por on_status a propósito: es barato y no merece jugárselo a
        # que el modelo acierte. Sin esto, el banco lo marcaba como
        # "nada" aunque NOVA sí lo hubiera hecho.
        llamadas = intentadas + list(r.tools_used) + [p.tool for p in r.pendientes]
        if esperada is None:
            if llamadas:
                falsos += 1
                fallos.append(f"  «{frase}» NO debía llamar a nada -> {llamadas}")
            else:
                aciertos += 1
        elif esperada in llamadas:
            aciertos += 1
        else:
            fallos.append(f"  «{frase}» esperaba {esperada} -> {llamadas or 'nada'}")
    return aciertos, falsos, time.perf_counter() - t0, fallos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modelo", default=CONFIG.model)
    ap.add_argument("--detalle", action="store_true", help="enseña cada fallo")
    args = ap.parse_args()

    reg = build_registry("solo_peligroso")
    llm = OllamaClient(CONFIG.ollama_url, args.modelo, keep_alive=CONFIG.keep_alive)
    # Con el contexto real. Sin él, "qué hora es" fallaba por culpa del
    # banco: la hora está en el contexto, no en ninguna herramienta.
    prompt = build_system_prompt(Awareness().snapshot(), "")

    print(f"modelo: {args.modelo}   casos: {len(CASOS)}   "
          f"({sum(1 for _, e in CASOS if e is None)} son 'no llames a nada')\n")

    aciertos, falsos, segundos, fallos = probar(llm, reg, prompt)
    print(f"catálogo de {len(reg.llm_schemas())} herramientas")
    print(f"   aciertos {aciertos}/{len(CASOS)}   "
          f"llamadas de más {falsos}   {segundos:.0f}s")
    if args.detalle and fallos:
        print("\n".join(fallos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
