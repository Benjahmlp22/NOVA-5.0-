"""Herramientas para cuando estás peleándote con código.

Ejemplo de plugin CON código. Todo lo que hace se puede leer aquí en
treinta segundos, que es exactamente lo que el panel te pide que hagas
antes de activarlo.

No importa nada peligroso a propósito: sin `subprocess`, sin `socket`,
sin `shutil`. La revisión de NOVA sale limpia y eso se ve en el panel.
"""

from __future__ import annotations

import re
from pathlib import Path

# Errores que en Python significan casi siempre lo mismo. Sale de verlos
# una y otra vez, no de la documentación.
_CAUSAS_TIPICAS = [
    (r"ModuleNotFoundError: No module named '([\w.]+)'",
     "Falta instalar {0}, o lo estás ejecutando desde otra carpeta."),
    (r"IndentationError",
     "Mezcla de espacios y tabulaciones, casi seguro."),
    (r"UnicodeEncodeError.*charmap",
     "La consola de Windows está en cp1252 y hay un carácter que no entra. "
     "Con reconfigure(encoding='utf-8') en la salida se arregla."),
    (r"AttributeError: 'NoneType' object has no attribute '(\w+)'",
     "Algo devolvió None donde esperabas un objeto, y se usó sin comprobar."),
    (r"KeyError: '?([\w.]+)'?",
     "La clave {0} no está en el diccionario. Suele ser una errata o un "
     "dato que llegó distinto de lo previsto."),
    (r"FileNotFoundError.*'(.+)'",
     "No existe {0}. Ojo a las rutas relativas: dependen de desde dónde ejecutes."),
    (r"RecursionError",
     "Una función se llama a sí misma sin caso de parada."),
    (r"ZeroDivisionError",
     "Una división entre cero. Normalmente una lista vacía de la que se "
     "calcula la media."),
]


def explicar_error(texto: str) -> dict:
    """Mira un traceback y dice la causa típica, si la reconoce."""
    contenido = (texto or "").strip()
    if not contenido:
        return {"ok": False, "message": "Pégame el error y te lo miro."}

    # La última línea de un traceback es la que dice qué pasó.
    lineas = [ln for ln in contenido.splitlines() if ln.strip()]
    ultima = lineas[-1] if lineas else contenido

    for patron, explicacion in _CAUSAS_TIPICAS:
        m = re.search(patron, contenido)
        if m:
            detalle = explicacion.format(*m.groups()) if m.groups() else explicacion
            return {"ok": True, "message": f"{ultima.strip()} — {detalle}"}

    # Sin causa conocida, al menos se le da lo que importa al modelo.
    archivo = re.findall(r'File "([^"]+)", line (\d+)', contenido)
    donde = f" Falla en {Path(archivo[-1][0]).name}, línea {archivo[-1][1]}." if archivo else ""
    return {"ok": True, "message": f"{ultima.strip()}{donde}"}


def contar_lineas(ruta: str) -> dict:
    """Cuántas líneas tiene un archivo de código, sin contar huecos."""
    try:
        archivo = Path(ruta).expanduser()
        texto = archivo.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "message": f"No pude abrir ese archivo: {exc}"}

    lineas = texto.splitlines()
    codigo = [ln for ln in lineas if ln.strip() and not ln.strip().startswith("#")]
    return {
        "ok": True,
        "message": f"{archivo.name} tiene {len(lineas)} líneas, "
                   f"{len(codigo)} de código y el resto huecos o comentarios.",
    }


# Lo que el plugin le ofrece a NOVA. El gestor lee esto: mismo formato
# que las herramientas de serie, para no inventar un segundo sistema.
HERRAMIENTAS = [
    {
        "name": "codigo.explicar_error",
        "description": "Explica un error o traceback de Python que el usuario tiene delante",
        "handler": explicar_error,
        "schema": {
            "type": "object",
            "properties": {"texto": {"type": "string"}},
            "required": ["texto"],
        },
        "responde_sola": True,
    },
    {
        "name": "codigo.contar_lineas",
        "description": "Cuántas líneas tiene un archivo de código",
        "handler": contar_lineas,
        "schema": {
            "type": "object",
            "properties": {"ruta": {"type": "string"}},
            "required": ["ruta"],
        },
        "responde_sola": True,
    },
]
