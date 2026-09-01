"""Mirar, ejecutar y probar código: NOVA como compañera de programación.

Hasta ahora NOVA sabía abrir apps y leer carpetas, pero de los proyectos
de Benja no sabía nada. «Prueba Nodika» o «¿por qué falla el script?» no
tenían respuesta posible: no podía abrir un archivo, ni correr un test,
ni ver un error.

Tres decisiones que no son de estilo:

**Sólo dentro de `Desktop\\proyectos`.**  Ejecutar un comando arbitrario
en cualquier sitio del disco a partir de lo que ha entendido un modelo de
4B por un micrófono es exactamente el tipo de cosa que no debe poder
pasar.  La raíz se fija en la configuración y no se sale de ahí.

**Antes de ejecutar nada, se le pregunta al vigilante de recursos.**  Un
`pytest` de 500 tests o un `npm run build` se comen la máquina, y si
estás jugando eso son fotogramas tuyos.  Con un juego a pantalla
completa NOVA dice que no y te ofrece hacerlo luego — que es lo que
harías tú.  Se puede forzar con `igualmente`, porque a veces sí quieres.

**Nada corre sin cronómetro.**  Un script que se queda esperando entrada
por teclado no devuelve nunca, y NOVA se quedaría colgada para siempre
con el orbe en "pensando".  Al vencer el plazo se mata el proceso y se
dice cuánto aguantó.

Lo que sale por pantalla se recorta antes de dárselo al modelo: un
traceback de Python son 40 líneas de las que importan tres, y meterlas
enteras en el contexto de un modelo local es gastarse el prefill en
ruido.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path

from ..config import CONFIG
from ..recursos import Vigilante
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.codigo")

_vigilante = Vigilante()

# Hasta qué profundidad se buscan proyectos bajo la raíz. Con tres
# niveles salían 117 y se quedaban fuera los que están más hondos —la
# propia NOVA vive en `03_IA_y_Asistentes/ai-assistants/2026 V2 IA/...`,
# que es el cuarto—. Con cuatro salen 182 y el recorrido pasa de 0.20 s
# a 0.36 s: se paga a gusto por no tener proyectos invisibles.
PROFUNDIDAD = 4

# Por lo que se reconoce una carpeta como "un proyecto" y no como un
# cajón. Sin esto, `01_Juegos_Web` entero contaría como uno.
_MARCAS = (
    "package.json", "pyproject.toml", "requirements.txt", "index.html",
    "Cargo.toml", "go.mod", ".git", "main.py", "run.py",
)

# Carpetas por las que no se baja NUNCA. Recorrer `node_modules` para
# encontrar un proyecto es tardar minutos en algo que tarda milisegundos.
_NO_ENTRAR = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", "target", ".idea", ".vscode", "site-packages",
})

# Cuánto texto se le pasa al modelo. Un traceback entero son 40 líneas de
# las que importan tres, y el contexto de un modelo local se paga caro.
LINEAS_SALIDA = 25
CARACTERES_ARCHIVO = 6000


# ── Encontrar el proyecto ────────────────────────────────────────────

def _normalizar(texto: str) -> str:
    t = (texto or "").strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "", t)


def proyectos() -> dict[str, Path]:
    """Las carpetas bajo la raíz que parecen un proyecto, por nombre.

    Se recorre cada vez y no se cachea a propósito: Benja crea proyectos
    a diario, y una caché haría que el de esta mañana no existiera para
    NOVA hasta reiniciarla. Medido: 182 proyectos en 0.36 s, saltándose
    `node_modules` — cachear eso sería optimizar lo que no duele.
    """
    raiz = CONFIG.proyectos_dir
    if not raiz.is_dir():
        return {}

    encontrados: dict[str, Path] = {}

    def bajar(carpeta: Path, nivel: int) -> None:
        if nivel > PROFUNDIDAD:
            return
        try:
            hijos = list(carpeta.iterdir())
        except OSError:
            return
        nombres = {h.name for h in hijos}
        if nivel > 0 and any(m in nombres for m in _MARCAS):
            encontrados.setdefault(_normalizar(carpeta.name), carpeta)
            # No se baja más: dentro de un proyecto, las subcarpetas no
            # son proyectos distintos.
            return
        for h in hijos:
            if h.is_dir() and h.name not in _NO_ENTRAR and not h.name.startswith("."):
                bajar(h, nivel + 1)

    bajar(raiz, 0)
    return encontrados


def _resolver(nombre: str) -> Path | None:
    """La carpeta que Benja quiere decir. Tolerante: lo dijo hablando."""
    clave = _normalizar(nombre)
    if not clave:
        return None
    todos = proyectos()
    if clave in todos:
        return todos[clave]
    # Por trozos: "nodika" encuentra "nodika-motor", y "battle dither"
    # encuentra "battle-dither" porque el normalizado quita el guion.
    candidatos = [ruta for k, ruta in todos.items() if clave in k or k in clave]
    if len(candidatos) == 1:
        return candidatos[0]
    if candidatos:
        # Varios: gana el de nombre más parecido en longitud.
        return min(candidatos, key=lambda p: abs(len(_normalizar(p.name)) - len(clave)))
    return None


def _no_lo_encuentro(nombre: str) -> ToolResult:
    cuantos = len(proyectos())
    return ToolResult(
        ok=False,
        message=f"No encuentro ningún proyecto que se llame «{nombre}». "
                f"Tengo {cuantos} en {CONFIG.proyectos_dir.name}.",
    )


def _dentro(ruta: Path) -> bool:
    """¿Está de verdad bajo la raíz de proyectos?

    Se comprueba resuelto: `proyectos/x/../../../Windows` no es un
    proyecto por mucho que empiece por la raíz.
    """
    try:
        ruta.resolve().relative_to(CONFIG.proyectos_dir.resolve())
        return True
    except (ValueError, OSError):
        return False


# ── Mirar ────────────────────────────────────────────────────────────

def listar(nombre: str = "") -> ToolResult:
    """Qué proyectos hay, o qué archivos tiene uno."""
    if not nombre:
        todos = sorted(p.name for p in proyectos().values())
        if not todos:
            return ToolResult(
                ok=False,
                message=f"No veo ningún proyecto en {CONFIG.proyectos_dir}.",
            )
        muestra = ", ".join(todos[:12])
        cola = f" y {len(todos) - 12} más" if len(todos) > 12 else ""
        return ToolResult(
            ok=True,
            message=f"Tienes {len(todos)} proyectos. Algunos: {muestra}{cola}.",
            data={"proyectos": todos},
        )

    carpeta = _resolver(nombre)
    if carpeta is None:
        return _no_lo_encuentro(nombre)
    try:
        archivos = sorted(
            f.name for f in carpeta.iterdir()
            if f.is_file() or (f.is_dir() and f.name not in _NO_ENTRAR)
        )
    except OSError as exc:
        return ToolResult(ok=False, message=f"No pude mirar {carpeta.name}: {exc}")
    return ToolResult(
        ok=True,
        message=f"{carpeta.name} tiene: {', '.join(archivos[:20])}.",
        data={"proyecto": carpeta.name, "ruta": str(carpeta), "archivos": archivos},
    )


def ver(proyecto: str, archivo: str) -> ToolResult:
    """El contenido de un archivo, para poder hablar de él."""
    carpeta = _resolver(proyecto)
    if carpeta is None:
        return _no_lo_encuentro(proyecto)

    destino = carpeta / archivo
    if not destino.is_file():
        # Puede haberlo dicho sin la ruta: "el main" en vez de "src/main.py".
        coincidencias = [
            f for f in carpeta.rglob("*")
            if f.is_file()
            and _NO_ENTRAR.isdisjoint(f.parts)
            and _normalizar(archivo) in _normalizar(f.name)
        ]
        if not coincidencias:
            # Con la lista delante el modelo acierta a la siguiente. Sin
            # ella se inventa otro nombre: probándolo en vivo pidió
            # «main.tsx» en un proyecto que no tiene ni un TypeScript.
            hay = sorted(
                f.name for f in carpeta.rglob("*")
                if f.is_file() and _NO_ENTRAR.isdisjoint(f.parts)
            )[:15]
            return ToolResult(
                ok=False,
                message=f"No encuentro «{archivo}» en {carpeta.name}. "
                        + (f"Ahí dentro hay: {', '.join(hay)}." if hay else "Está vacío."),
                data={"archivos": hay},
            )
        destino = min(coincidencias, key=lambda f: len(f.name))

    if not _dentro(destino):
        return ToolResult(ok=False, message="Esa ruta se sale de tus proyectos.")
    try:
        texto = destino.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(ok=False, message=f"No pude leerlo: {exc}")

    lineas = texto.count("\n") + 1
    recortado = texto[:CARACTERES_ARCHIVO]
    if len(texto) > CARACTERES_ARCHIVO:
        recortado += "\n... (recortado)"
    return ToolResult(
        ok=True,
        message=f"{destino.name}, {lineas} líneas:\n\n{recortado}",
        data={"ruta": str(destino), "lineas": lineas},
    )


def buscar(proyecto: str, texto: str) -> ToolResult:
    """Dónde está algo dentro de un proyecto: «dónde defino el jugador»."""
    carpeta = _resolver(proyecto)
    if carpeta is None:
        return _no_lo_encuentro(proyecto)
    if not texto.strip():
        return ToolResult(ok=False, message="¿Qué quieres que busque?")

    aguja = texto.lower()
    hallazgos: list[str] = []
    for f in carpeta.rglob("*"):
        if len(hallazgos) >= 12:
            break
        if not f.is_file() or not _NO_ENTRAR.isdisjoint(f.parts):
            continue
        if f.suffix.lower() not in {".py", ".js", ".ts", ".jsx", ".tsx", ".html",
                                    ".css", ".json", ".md", ".lua", ".cs", ".rs"}:
            continue
        try:
            for n, linea in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if aguja in linea.lower():
                    hallazgos.append(f"{f.relative_to(carpeta)}:{n}: {linea.strip()[:120]}")
                    break
        except OSError:
            continue

    if not hallazgos:
        return ToolResult(
            ok=True, message=f"No encuentro «{texto}» en {carpeta.name}.", data={}
        )
    # Una frase, no una lista. `responde_sola` hace que ESTO sea lo que
    # NOVA dice en alto, y al pulirlo para voz se queda con la primera
    # línea: probándolo en vivo contestó «pixijs aparece en 7 archivos:»
    # y ahí se cortó, con los dos puntos colgando y sin decir ni uno.
    nombres = [h.split(":")[0] for h in hallazgos]
    primeros = ", ".join(nombres[:3])
    cola = f", y en {len(nombres) - 3} más" if len(nombres) > 3 else ""
    # Singular y plural, porque esto se dice en alto y «sale en 1
    # archivos» suena a máquina.
    cuenta = "1 archivo" if len(nombres) == 1 else f"{len(nombres)} archivos"
    return ToolResult(
        ok=True,
        message=f"«{texto}» sale en {cuenta} de {carpeta.name}: {primeros}{cola}.",
        data={"hallazgos": hallazgos},
    )


# ── Ejecutar ─────────────────────────────────────────────────────────

def _hay_sitio(igualmente: bool) -> ToolResult | None:
    """El vigilante manda, salvo que Benja insista.

    Devuelve el "no" ya redactado, o None si se puede seguir. Vive suelto
    porque lo usan `ejecutar` y `probar` igual, y porque así se puede
    probar sin lanzar ningún proceso.
    """
    if igualmente:
        return None
    estado = _vigilante.estado()
    if estado.modo() != "apretado":
        return None
    if estado.hay_juego:
        return ToolResult(
            ok=False,
            message=f"Tienes {estado.juego} a pantalla completa y esto se come la "
                    "máquina. Te lo dejo para cuando salgas, o dime que lo haga igualmente.",
            data={"motivo": "juego", "juego": estado.juego},
        )
    return ToolResult(
        ok=False,
        message=f"El PC está al límite ahora mismo (CPU al {estado.cpu:.0f} por ciento). "
                "Si quieres lo hago igualmente, pero va a ir lento.",
        data={"motivo": "recursos"},
    )


def _recortar(salida: str) -> str:
    """Las últimas líneas con contenido: el error está al final."""
    lineas = [ln.rstrip() for ln in (salida or "").splitlines() if ln.strip()]
    if len(lineas) <= LINEAS_SALIDA:
        return "\n".join(lineas)
    return "... (recortado)\n" + "\n".join(lineas[-LINEAS_SALIDA:])


# Lo que en la salida de pytest es el error de verdad, y no la maquinaria
# de importación que hay alrededor.
_ES_ERROR = re.compile(
    r"^(E\s|FAILED|ERROR\b|\w*(Error|Exception|Warning):|assert\b)|"
    r"(Error|Exception):\s"
)


def _por_que_falla(salida: str) -> str:
    """Sólo las líneas que explican el fallo, para poder DECIRLO.

    Probándolo en vivo NOVA leyó en alto un traceback entero:
    «return _bootstrap._gcd_import, import tools, from memory import...».
    De esas veinte líneas la única que dice algo es el `ImportError` del
    final; el resto es el andamiaje de Python.

    Si no se reconoce nada, se cae al recorte de siempre: peor es no
    decir nada.
    """
    utiles = [
        ln.strip() for ln in (salida or "").splitlines()
        if ln.strip() and _ES_ERROR.search(ln.strip())
    ]
    if not utiles:
        return _recortar(salida)
    # Sin repetir: pytest saca la misma línea en el detalle y en el
    # resumen final.
    vistas: list[str] = []
    for ln in utiles:
        if ln not in vistas:
            vistas.append(ln)
    return "\n".join(vistas[:6])


def _matar_arbol(proc: subprocess.Popen) -> None:
    """Mata el proceso Y a sus hijos.

    Hace falta el árbol entero, no sólo el proceso. Con `shell=True` lo
    que se lanza es `cmd.exe`, y matar el cmd deja al python o al node de
    dentro corriendo tan tranquilos. `taskkill /T` sí baja por los hijos;
    es de Windows, así que si algún día esto corre en otro sitio, el
    `proc.kill()` de después hace lo que puede.
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        log.debug("taskkill falló; mato sólo el proceso", exc_info=True)
    try:
        proc.kill()
    except OSError:
        pass


def _correr(comando: str | list[str], carpeta: Path, plazo: float) -> tuple[int, str, float]:
    """Lanza y espera, con cronómetro. Devuelve (código, salida, segundos).

    Con `Popen` y no con `subprocess.run(timeout=...)`, y no es lo mismo.
    Medido: un `sleep(30)` lanzado por shell con un plazo de 1 segundo
    tardaba **30 segundos** en devolver. `run()` levanta la excepción a
    tiempo, sí, pero mata sólo al `cmd.exe` y luego se queda esperando a
    que se cierren las tuberías — y las tuberías las tiene el nieto, que
    sigue vivo. El cronómetro estaba y no servía de nada: NOVA se habría
    quedado en "pensando" lo que durase el script.
    """
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            comando,
            cwd=str(carpeta),
            shell=isinstance(comando, str),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            # Un script que pide input() se quedaría esperando para
            # siempre; sin stdin recibe EOF y termina.
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except OSError as exc:
        return 1, f"No pude lanzarlo: {exc}", time.monotonic() - t0

    try:
        fuera, error = proc.communicate(timeout=plazo)
    except subprocess.TimeoutExpired:
        _matar_arbol(proc)
        try:
            # Ya está muerto: esto sólo recoge las tuberías para no dejar
            # descriptores abiertos.
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("el proceso no se ha muerto ni con taskkill")
        return -1, "", time.monotonic() - t0

    salida = (fuera or "") + (("\n" + error) if error else "")
    return proc.returncode, salida, time.monotonic() - t0


def ejecutar(proyecto: str, archivo: str = "", comando: str = "",
             igualmente: bool = False) -> ToolResult:
    """Corre un script del proyecto (o un comando) y cuenta qué pasó."""
    carpeta = _resolver(proyecto)
    if carpeta is None:
        return _no_lo_encuentro(proyecto)
    if not comando.strip() and not archivo.strip():
        # Antes que los recursos: si la orden no dice QUÉ ejecutar no hay
        # nada que autorizar ni que medir. Probándolo en vivo el modelo
        # llamó a esto sin argumentos y NOVA pidió permiso para «ejecutar
        # algo», que es lo peor que puede preguntar.
        return ToolResult(
            ok=False,
            message="¿Qué quieres que ejecute? Dime qué archivo, o «prueba» "
                    "si lo que quieres es correr los tests.",
        )
    if (negativa := _hay_sitio(igualmente)) is not None:
        return negativa

    if comando.strip():
        orden: str | list[str] = comando.strip()
        etiqueta = comando.strip()
    elif archivo.strip():
        destino = carpeta / archivo.strip()
        if not destino.is_file() or not _dentro(destino):
            return ToolResult(
                ok=False, message=f"No encuentro «{archivo}» dentro de {carpeta.name}."
            )
        orden = _como_lanzar(destino)
        etiqueta = destino.name
    else:  # pragma: no cover - ya descartado arriba
        return ToolResult(ok=False, message="¿Qué quieres que ejecute?")

    codigo, salida, segundos = _correr(orden, carpeta, CONFIG.codigo_timeout)

    if codigo == -1:
        return ToolResult(
            ok=False,
            message=f"{etiqueta} seguía corriendo a los {CONFIG.codigo_timeout:.0f} "
                    "segundos y lo he cortado. O se ha colgado, o esperaba que "
                    "escribieras algo.",
            data={"timeout": True},
        )
    recorte = _recortar(salida)
    if codigo == 0:
        return ToolResult(
            ok=True,
            message=f"{etiqueta} ha terminado bien en {segundos:.1f} segundos."
                    + (f"\n\n{recorte}" if recorte else ""),
            data={"codigo": 0, "segundos": segundos, "salida": recorte},
        )
    return ToolResult(
        ok=False,
        message=f"{etiqueta} ha fallado (código {codigo}):\n\n{_por_que_falla(salida)}",
        data={"codigo": codigo, "segundos": segundos, "salida": recorte},
    )


def _como_lanzar(destino: Path) -> list[str]:
    """Con qué se corre cada cosa. El venv del proyecto manda si lo hay."""
    sufijo = destino.suffix.lower()
    if sufijo == ".py":
        return [str(_python_de(destino.parent)), str(destino)]
    if sufijo in {".js", ".mjs", ".cjs"}:
        return ["node", str(destino)]
    if sufijo in {".ps1"}:
        return ["powershell", "-NoProfile", "-File", str(destino)]
    if sufijo in {".bat", ".cmd"}:
        return [str(destino)]
    return [str(destino)]


def _python_de(carpeta: Path) -> Path:
    """El intérprete del proyecto, si tiene venv propio.

    Correr los tests de un proyecto con el Python global es correrlos sin
    sus dependencias: el fallo que sale entonces no es el suyo, es que
    falta todo. Se busca hacia arriba porque el script puede estar en
    `src/` o en `tests/`.
    """
    for nivel in [carpeta, *carpeta.parents]:
        for nombre in (".venv", "venv", "env"):
            candidato = nivel / nombre / "Scripts" / "python.exe"
            if candidato.is_file():
                return candidato
        if (nivel / ".git").is_dir() or (nivel / "pyproject.toml").is_file():
            break
    import sys

    return Path(sys.executable)


def probar(proyecto: str, igualmente: bool = False) -> ToolResult:
    """Corre los tests del proyecto y dice cuántos pasan."""
    carpeta = _resolver(proyecto)
    if carpeta is None:
        return _no_lo_encuentro(proyecto)
    if (negativa := _hay_sitio(igualmente)) is not None:
        return negativa

    orden = _como_probar(carpeta)
    if orden is None:
        return ToolResult(
            ok=False,
            message=f"{carpeta.name} no tiene tests que yo sepa reconocer: "
                    "ni pytest, ni un «npm test» en el package.json.",
        )

    codigo, salida, segundos = _correr(orden, carpeta, CONFIG.codigo_timeout)
    if codigo == -1:
        return ToolResult(
            ok=False,
            message=f"Los tests de {carpeta.name} seguían corriendo a los "
                    f"{CONFIG.codigo_timeout:.0f} segundos y los he cortado.",
            data={"timeout": True},
        )

    resumen = _resumir_tests(salida)
    if codigo == 0:
        return ToolResult(
            ok=True,
            message=f"Los tests de {carpeta.name} pasan: {resumen} "
                    f"En {segundos:.0f} segundos.",
            data={"codigo": 0, "resumen": resumen},
        )
    return ToolResult(
        ok=False,
        message=f"Los tests de {carpeta.name} fallan: {resumen}\n\n{_por_que_falla(salida)}",
        # La salida entera queda aquí por si hace falta mirarla; lo que va
        # al modelo —y de ahí a la voz— son sólo las líneas que explican.
        data={"codigo": codigo, "resumen": resumen, "salida": _recortar(salida)},
    )


def _como_probar(carpeta: Path) -> list[str] | str | None:
    """Cómo se prueba este proyecto, mirando lo que tiene dentro."""
    if (carpeta / "pyproject.toml").is_file() or (carpeta / "pytest.ini").is_file() \
            or (carpeta / "tests").is_dir():
        return [str(_python_de(carpeta)), "-m", "pytest", "-q"]
    paquete = carpeta / "package.json"
    if paquete.is_file():
        try:
            import json

            datos = json.loads(paquete.read_text(encoding="utf-8", errors="replace"))
            if (datos.get("scripts") or {}).get("test"):
                return "npm test"
        except (OSError, ValueError):
            pass
    return None


def _resumir_tests(salida: str) -> str:
    """La línea de pytest que dice cuántos pasan, dicha en español.

    Se busca de abajo arriba porque pytest la escribe al final y porque
    una salida larga puede contener la palabra "passed" en cualquier
    sitio (en el nombre de un test, sin ir más lejos).
    """
    for linea in reversed((salida or "").splitlines()):
        if not re.search(r"\d+\s+(passed|failed|error)", linea):
            continue
        limpia = re.sub(r"[=\s]+$", "", re.sub(r"^[=\s]+", "", linea))
        pasan = re.search(r"(\d+) passed", limpia)
        fallan = re.search(r"(\d+) (?:failed|error)", limpia)
        trozos = []
        if fallan:
            trozos.append(f"{fallan.group(1)} fallando")
        if pasan:
            trozos.append(f"{pasan.group(1)} en verde")
        if trozos:
            return ", ".join(trozos) + "."
        return limpia
    return "no he sabido leer el resultado."


# ── Registro ─────────────────────────────────────────────────────────

def _resumir_ejecutar(args: dict) -> str:
    """Lo que Benja oye antes de decir que sí. Tiene que ser exacto."""
    que = args.get("comando") or args.get("archivo") or ""
    proyecto = args.get("proyecto", "ese proyecto")
    return f"ejecutar «{que}» en {proyecto}" if que else f"ejecutar algo en {proyecto}"


def _resumir_probar(args: dict) -> str:
    return f"correr los tests de {args.get('proyecto', '?')}"


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="codigo.proyectos",
        description="Qué proyectos tiene, o qué archivos tiene uno suyo",
        handler=listar,
        schema={"type": "object", "properties": {"nombre": {"type": "string"}}},
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="codigo.ver",
        description="Lee un archivo de código de un proyecto suyo para poder hablar de él",
        handler=ver,
        schema={
            "type": "object",
            "properties": {
                "proyecto": {"type": "string"},
                "archivo": {"type": "string"},
            },
            "required": ["proyecto", "archivo"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="codigo.buscar",
        description="Dónde aparece algo dentro de un proyecto: «dónde defino el jugador»",
        handler=buscar,
        schema={
            "type": "object",
            "properties": {
                "proyecto": {"type": "string"},
                "texto": {"type": "string"},
            },
            "required": ["proyecto", "texto"],
        },
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="codigo.ejecutar",
        description=(
            "Corre UN ARCHIVO o un comando concreto de un proyecto suyo y cuenta si "
            "ha fallado y por qué. Hay que decir cuál: para «prueba X» va codigo.probar. "
            "«igualmente» si insiste con el PC cargado"
        ),
        handler=ejecutar,
        schema={
            "type": "object",
            "properties": {
                "proyecto": {"type": "string"},
                "archivo": {"type": "string"},
                "comando": {"type": "string"},
                "igualmente": {"type": "boolean"},
            },
            "required": ["proyecto"],
        },
        risk=Risk.DANGEROUS,
        resumir=_resumir_ejecutar,
    ))
    reg.register(Tool(
        name="codigo.probar",
        description=(
            "Prueba un proyecto suyo: corre sus tests y dice cuántos pasan y cuántos "
            "fallan. Es la de «prueba X», «funciona X», «están verdes los tests de X»"
        ),
        handler=probar,
        schema={
            "type": "object",
            "properties": {
                "proyecto": {"type": "string"},
                "igualmente": {"type": "boolean"},
            },
            "required": ["proyecto"],
        },
        risk=Risk.DANGEROUS,
        resumir=_resumir_probar,
    ))
