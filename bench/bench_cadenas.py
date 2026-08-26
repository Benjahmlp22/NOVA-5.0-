"""Órdenes encadenadas: "abre Discord y pon música".

    python bench/bench_cadenas.py

Mide lo único que importa aquí: si NOVA hace las DOS cosas que le pides,
o sólo la primera. No se comprueba el texto de la respuesta, se comprueba
qué herramientas llegó a ejecutar.

Las herramientas son de mentira — registran la llamada y devuelven una
frase — porque el banco no puede abrir Discord veinte veces para saber si
el modelo encadena.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent))

from nova.config import CONFIG  # noqa: E402
from nova.core.agent import Agent  # noqa: E402
from nova.core.conversation import build_system_prompt  # noqa: E402
from nova.llm.ollama import OllamaClient  # noqa: E402
from nova.tools.registry import Risk, Tool, ToolRegistry, ToolResult  # noqa: E402

# (orden, herramientas que deberían acabar ejecutándose)
CASOS: list[tuple[str, set[str]]] = [
    ("abre discord y abre chrome", {"app.open"}),
    ("abre spotify y dime que hora es", {"app.open"}),
    ("dime el estado del pc y que apps tengo abiertas", {"pc.status", "pc.running_apps"}),
    ("hazme una captura y dime que tengo delante", {"screen.capture", "pc.active_window"}),
    ("abre el bloc de notas y busca en internet el precio de la 4070",
     {"app.open", "web.search"}),
    ("dime la hora y luego abre discord", {"app.open"}),
    ("mira cuanta ram tengo y haz una captura", {"pc.status", "screen.capture"}),
    ("abre chrome, spotify y discord", {"app.open"}),
]

# Cuántas veces se espera que se llame a app.open cuando se piden varias
# apps: es el caso donde "encadenar" significa repetir la MISMA
# herramienta, que es el que más se le atraganta a un modelo pequeño.
REPETICIONES = {
    "abre discord y abre chrome": 2,
    "abre chrome, spotify y discord": 3,
}


def registro_espia() -> tuple[ToolRegistry, list[str]]:
    """Registro con las mismas herramientas, pero que no tocan el PC."""
    llamadas: list[str] = []
    reg = ToolRegistry("solo_peligroso")

    def falsa(nombre: str, descripcion: str, respuesta: str, esquema=None):
        def handler(**kw):
            llamadas.append(nombre)
            return ToolResult(ok=True, message=respuesta)

        reg.register(Tool(name=nombre, description=descripcion, handler=handler,
                          schema=esquema or {"type": "object", "properties": {}},
                          risk=Risk.SAFE))

    texto = {"type": "object",
             "properties": {"name": {"type": "string"}, "query": {"type": "string"}}}
    falsa("app.open", "Abre una aplicación o programa por su nombre",
          "Listo, la he abierto.", texto)
    falsa("pc.status", "Estado del PC ahora: CPU, RAM, procesos y batería",
          "CPU al 12%. 18 GB libres de 32 GB.")
    falsa("pc.running_apps", "Lista las aplicaciones abiertas",
          "Tienes abiertas: Chrome, Discord, Steam.")
    falsa("pc.active_window", "Qué aplicación tiene el usuario en primer plano",
          "Tienes delante Chrome.")
    falsa("screen.capture", "Hace una captura de pantalla", "Captura guardada.")
    falsa("web.search", "Busca algo en internet", "La 4070 cuesta unos 550 euros.", texto)
    return reg, llamadas


def main() -> int:
    llm = OllamaClient(CONFIG.ollama_url, CONFIG.model, keep_alive=CONFIG.keep_alive,
                       temperature=CONFIG.temperature, max_tokens=CONFIG.max_tokens)
    if not llm.available():
        print(f"✗ Ollama no responde en {CONFIG.ollama_url}")
        return 1

    print(f"Modelo: {CONFIG.model}\n")
    aciertos = 0
    for orden, esperadas in CASOS:
        reg, llamadas = registro_espia()
        agente = Agent(llm, reg, max_rounds=CONFIG.max_rounds)
        t0 = time.monotonic()
        respuesta = agente.run(build_system_prompt(), [], orden)
        dt = time.monotonic() - t0

        usadas = set(llamadas)
        falta = esperadas - usadas
        veces = REPETICIONES.get(orden)
        pocas = veces is not None and llamadas.count("app.open") < veces

        bien = not falta and not pocas
        aciertos += bien
        marca = "✓" if bien else "✗"
        detalle = ""
        if falta:
            detalle = f"  falta: {', '.join(sorted(falta))}"
        elif pocas:
            detalle = f"  sólo {llamadas.count('app.open')} de {veces} apps"
        print(f"  {marca} [{dt:5.2f}s] «{orden}»{detalle}")
        print(f"      llamó: {llamadas or '—'}")
        print(f"      dijo : {respuesta.text[:110]}")

    print(f"\nEncadena bien {aciertos}/{len(CASOS)}")
    llm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
