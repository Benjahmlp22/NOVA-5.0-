"""El gestor: encuentra los plugins, los enciende y los apaga.

Dos carpetas:

    plugins/          los que vienen con NOVA
    data/plugins/     los que instala el usuario

Y un archivo, `data/plugins.json`, que dice cuáles están activos. Nada
más: no hay base de datos ni servidor. Copiar una carpeta en
`data/plugins/` es instalar un plugin.

**Nada se ejecuta al arrancar.** Leer un manifiesto es leer un JSON;
cargar el código sólo pasa cuando el plugin está activo Y trae código Y
el usuario lo activó a sabiendas. Por defecto, un plugin recién
encontrado está apagado.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import CONFIG, ROOT
from .manifiesto import Plugin, leer
from .revision import Revision, revisar

log = logging.getLogger("nova.plugins.gestor")


def carpeta_incluidos() -> Path:
    """Los que vienen con NOVA. Se leen, nunca se escriben."""
    return ROOT / "plugins"


def carpeta_usuario() -> Path:
    """Los que instala la gente. Aquí sí se escribe."""
    return CONFIG.data_dir / "plugins"


def _archivo_estado() -> Path:
    return CONFIG.data_dir / "plugins.json"


class Gestor:
    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}
        self._activos: set[str] = set()
        self._cargar_estado()
        self.buscar()

    # ── Encontrar ────────────────────────────────────────────────────

    def buscar(self) -> list[Plugin]:
        """Relee las dos carpetas. No ejecuta nada de nadie."""
        encontrados: dict[str, Plugin] = {}

        for raiz in (carpeta_incluidos(), carpeta_usuario()):
            if not raiz.is_dir():
                continue
            for carpeta in sorted(raiz.iterdir()):
                if not carpeta.is_dir():
                    continue
                plugin = leer(carpeta)
                if plugin is None:
                    continue
                if plugin.id in encontrados:
                    # El del usuario gana al incluido: así se puede
                    # reemplazar uno de serie sin tocar la instalación.
                    log.info("el plugin %s del usuario reemplaza al incluido", plugin.id)
                encontrados[plugin.id] = plugin

        self._plugins = encontrados

        # Un plugin activo que ya no está, o que se ha roto, deja de
        # estar activo. Si no, el estado miente.
        perdidos = {i for i in self._activos
                    if i not in encontrados or not encontrados[i].valido}
        if perdidos:
            log.info("desactivo plugins que ya no valen: %s", ", ".join(perdidos))
            self._activos -= perdidos
            self._guardar_estado()

        return list(encontrados.values())

    # ── Consultar ────────────────────────────────────────────────────

    def todos(self) -> list[Plugin]:
        return sorted(self._plugins.values(), key=lambda p: p.nombre.lower())

    def obtener(self, id_o_nombre: str) -> Plugin | None:
        """Por identificador o por nombre, como lo diría una persona."""
        clave = (id_o_nombre or "").strip().lower()
        if not clave:
            return None
        if clave in self._plugins:
            return self._plugins[clave]
        for plugin in self._plugins.values():
            if clave == plugin.nombre.lower() or clave in plugin.nombre.lower():
                return plugin
        return None

    def esta_activo(self, id_: str) -> bool:
        return id_ in self._activos

    def activos(self) -> list[Plugin]:
        return [p for p in self.todos() if p.id in self._activos]

    def revisar_codigo(self, plugin: Plugin) -> Revision:
        """Lee su código sin ejecutarlo. Vacío si no trae código."""
        if not plugin.tiene_codigo or plugin.carpeta is None:
            return Revision()
        return revisar(plugin.carpeta / "plugin.py")

    # ── Encender y apagar ────────────────────────────────────────────

    def activar(self, id_: str) -> tuple[bool, str]:
        plugin = self._plugins.get(id_)
        if plugin is None:
            return False, f"No tengo ningún plugin llamado «{id_}»."
        if not plugin.valido:
            return False, f"Ese plugin está mal hecho: {plugin.errores[0]}."
        if plugin.id in self._activos:
            return True, f"{plugin.nombre} ya estaba activo."

        self._activos.add(plugin.id)
        self._guardar_estado()
        return True, f"{plugin.nombre} activado."

    def desactivar(self, id_: str) -> tuple[bool, str]:
        plugin = self._plugins.get(id_)
        nombre = plugin.nombre if plugin else id_
        if id_ not in self._activos:
            return True, f"{nombre} ya estaba apagado."
        self._activos.discard(id_)
        self._guardar_estado()
        return True, f"{nombre} desactivado."

    # ── Lo que aportan los activos ───────────────────────────────────

    def personalidad(self) -> str:
        """Lo que los plugins activos añaden al carácter de NOVA.

        Se concatena en el orden en que están: si dos se contradicen,
        gana el último, igual que en una conversación.
        """
        trozos = [p.personalidad.strip() for p in self.activos() if p.personalidad.strip()]
        return "\n\n".join(trozos)

    def voz_preferida(self) -> str:
        """La voz que pide el último plugin activo que pida alguna."""
        for plugin in reversed(self.activos()):
            if plugin.voz:
                return plugin.voz
        return ""

    def frases(self, clave: str) -> list[str]:
        """Las frases que los activos aportan para un momento dado."""
        salida: list[str] = []
        for plugin in self.activos():
            valores = plugin.frases.get(clave)
            if isinstance(valores, list):
                salida.extend(str(v) for v in valores if str(v).strip())
        return salida

    # ── Estado en disco ──────────────────────────────────────────────

    def _cargar_estado(self) -> None:
        try:
            datos = json.loads(_archivo_estado().read_text(encoding="utf-8"))
            self._activos = set(datos.get("activos", []))
        except (OSError, ValueError):
            self._activos = set()

    def _guardar_estado(self) -> None:
        try:
            _archivo_estado().parent.mkdir(parents=True, exist_ok=True)
            _archivo_estado().write_text(
                json.dumps({"activos": sorted(self._activos)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("no pude guardar qué plugins están activos: %s", exc)
