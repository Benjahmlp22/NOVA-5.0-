"""El álbum: todas tus imágenes convertidas en números, para buscarlas.

Buscar "una imagen de League of Legends" no puede mirar los nombres de
archivo: nadie llama a sus capturas por lo que sale en ellas.  Con CLIP
cada imagen es un vector, la frase también, y buscar es ordenar por
parecido.

Indexar cuesta caro **una vez** (unos 9 minutos las 16.699 de este PC) y
buscar después es instantáneo: un producto de matrices de 16.699x512,
que son milisegundos.

Se guarda en `data/album/`: los vectores en un `.npy` (34 MB para
16.699) y las fichas en un JSON. La segunda vez sólo se miran las
imágenes nuevas o cambiadas, así que actualizar son segundos.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import CONFIG
from . import clip

log = logging.getLogger("nova.vista.album")

EXTENSIONES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".avif"})

# Carpetas que nunca aportan nada y sí mucho ruido y mucho tiempo.
_SALTAR = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__", "AppData",
    "$RECYCLE.BIN", "System Volume Information", ".cache", "dist", "build",
    "site-packages", ".next", "target",
})

# Por debajo de esto es un icono, un botón o un trozo de interfaz, no una
# imagen que alguien quiera buscar. Filtrarlo por bytes es mucho más
# barato que abrirla para mirar el tamaño real.
MINIMO_BYTES = 12_000

# Lote de 32. Los hilos NO son fijos: los decide el vigilante de
# recursos según cómo esté el PC en ese momento, porque esto corre en
# segundo plano mientras NOVA tiene que seguir hablando.
LOTE = 32
HILOS = clip.HILOS          # el tope, cuando el PC va sobrado

# Frases de contraste para saber CUÁNTA confianza merece un resultado.
#
# CLIP no sirve para puntuar una imagen sola: sus números no son
# comparables entre consultas distintas. Medido sobre estas 3.793
# imágenes, "an underwater photo of a coral reef" (que no hay) sacó 0.266
# y "a screenshot of Minecraft" (que sí hay) sacó 0.300. Ni el valor
# absoluto ni el z-score separan una cosa de la otra.
#
# Lo que sí funciona es usarlo como se usa bien: COMPARANDO frases sobre
# la misma imagen. Si "a photo of a dog" no le gana a "a photo of
# something else", es que no hay ningún perro. Eso baja "a photo of a
# dog" de 0.259 a una confianza de 0.075, y deja Minecraft en 0.994.
#
# No es perfecto —"a screenshot of Excel" sigue sacando 0.944 sin que
# haya ninguna— y por eso esto NO se usa para filtrar en silencio, sino
# para que NOVA diga si está segura o no. Ver `Coincidencia.segura`.
_CONTRASTES = (
    "a photo of something else",
    "a random image",
    "a texture",
    "a user interface",
    "a blank picture",
)

# La temperatura con la que se entrenó CLIP. No es un número ajustable.
TEMPERATURA = 100.0

# Por encima de esto, NOVA lo dice sin peros. Por debajo avisa de que no
# está segura, pero lo enseña igual: buscar una foto es mirar candidatas.
CONFIANZA_SEGURA = 0.60


@dataclass(frozen=True)
class Coincidencia:
    ruta: Path
    parecido: float
    confianza: float

    @property
    def segura(self) -> bool:
        return self.confianza >= CONFIANZA_SEGURA


@dataclass
class Progreso:
    hechas: int = 0
    total: int = 0
    terminado: bool = False
    error: str = ""

    @property
    def porcentaje(self) -> int:
        return round(100 * self.hechas / self.total) if self.total else 0


def _hilos_ahora() -> int:
    """Cuántos hilos tocan ahora mismo, según lo cargado que esté el PC.

    Se pregunta al empezar y no en cada lote: cambiar el tamaño de la
    piscina a media faena no se puede, y de todas formas el `parar` de
    abajo ya corta el indexado entero si la cosa se pone fea.
    """
    try:
        from ..recursos import Vigilante

        return min(HILOS, Vigilante().hilos_para_lo_pesado())
    except Exception:  # noqa: BLE001
        return HILOS


def _raiz_album() -> Path:
    """Dónde vive el índice.

    Indirección a propósito: CONFIG es inmutable (que es lo correcto), y
    ésta es la función por la que los tests apuntan a una carpeta
    temporal. Mismo truco que en memory.py y notas.py.
    """
    return CONFIG.data_dir / "album"


def carpetas_por_defecto() -> list[Path]:
    """Dónde están las imágenes que la gente busca.

    El escritorio NO entra por defecto: en este PC tiene 10.288 imágenes
    que son recursos de proyectos (sprites, texturas, tiles). Costarían
    dos tercios del tiempo de indexado y sólo añaden ruido a "búscame
    aquella foto".
    """
    casa = Path.home()
    nombres = ("Downloads", "Descargas", "Pictures", "Imágenes", "Documents", "Documentos")
    return [c for n in nombres if (c := casa / n).is_dir()]


def _recorrer(carpetas: list[Path]) -> Iterator[Path]:
    for raiz in carpetas:
        for actual, subcarpetas, archivos in os.walk(raiz):
            subcarpetas[:] = [d for d in subcarpetas
                              if d not in _SALTAR and not d.startswith(".")]
            for nombre in archivos:
                if Path(nombre).suffix.lower() in EXTENSIONES:
                    yield Path(actual) / nombre


def _ficha(ruta: Path) -> tuple[str, float, int] | None:
    """Ruta, fecha y tamaño: con eso se sabe si hay que volver a mirarla."""
    try:
        st = ruta.stat()
    except OSError:
        return None
    if st.st_size < MINIMO_BYTES:
        return None
    return str(ruta), st.st_mtime, st.st_size


class Album:
    def __init__(self, carpetas: list[Path] | None = None) -> None:
        self.carpetas = carpetas or carpetas_por_defecto()
        self._rutas: list[str] = []
        self._marcas: list[list] = []      # [mtime, size] por imagen
        self._vectores: np.ndarray | None = None
        self._codificador = clip.CodificadorCLIP()
        self.progreso = Progreso()
        self._cargar()

    # ── Disco ────────────────────────────────────────────────────────

    @property
    def _carpeta(self) -> Path:
        return _raiz_album()

    def _cargar(self) -> None:
        fichas = self._carpeta / "fichas.json"
        vectores = self._carpeta / "vectores.npy"
        if not (fichas.exists() and vectores.exists()):
            return
        try:
            datos = json.loads(fichas.read_text(encoding="utf-8"))
            self._rutas = datos["rutas"]
            self._marcas = datos["marcas"]
            self._vectores = np.load(vectores)
            if len(self._rutas) != len(self._vectores):
                raise ValueError("el índice no cuadra")
        except Exception:  # noqa: BLE001
            log.warning("índice de imágenes ilegible, empiezo de cero", exc_info=True)
            self._rutas, self._marcas, self._vectores = [], [], None

    def _guardar(self) -> None:
        self._carpeta.mkdir(parents=True, exist_ok=True)
        np.save(self._carpeta / "vectores.npy", self._vectores)
        (self._carpeta / "fichas.json").write_text(
            json.dumps({"rutas": self._rutas, "marcas": self._marcas,
                        "cuando": time.strftime("%Y-%m-%d %H:%M")},
                       ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Estado ───────────────────────────────────────────────────────

    @property
    def cuantas(self) -> int:
        return len(self._rutas)

    @property
    def listo(self) -> bool:
        return self._vectores is not None and len(self._rutas) > 0

    # ── Indexar ──────────────────────────────────────────────────────

    def pendientes(self) -> list[Path]:
        """Las que faltan por mirar: nuevas, o cambiadas desde la última vez."""
        conocidas = {r: tuple(m) for r, m in zip(self._rutas, self._marcas, strict=True)}
        faltan = []
        for ruta in _recorrer(self.carpetas):
            ficha = _ficha(ruta)
            if ficha is None:
                continue
            texto, mtime, tam = ficha
            anterior = conocidas.get(texto)
            if anterior is None or abs(anterior[0] - mtime) > 1 or anterior[1] != tam:
                faltan.append(ruta)
        return faltan

    def indexar(self, avisar: Callable[[Progreso], None] | None = None,
                parar: Callable[[], bool] | None = None) -> int:
        """Mira las que faltan. Bloquea: se llama desde un hilo aparte."""
        faltan = self.pendientes()
        self.progreso = Progreso(total=len(faltan))
        if not faltan:
            self.progreso.terminado = True
            return 0

        nuevos_vectores: list[np.ndarray] = []
        nuevas_rutas: list[str] = []
        nuevas_marcas: list[list] = []

        # Decodificar imágenes es tan caro como pasarlas por el modelo
        # (20 ms contra 31), así que se hacen a la vez: mientras el
        # modelo trabaja con un lote, los hilos preparan el siguiente.
        hilos = _hilos_ahora()
        log.info("indexando %d imágenes con %d hilo(s)", len(faltan), hilos)
        with ThreadPoolExecutor(max_workers=hilos) as piscina:
            for i in range(0, len(faltan), LOTE):
                if parar is not None and parar():
                    log.info("indexado cancelado a las %d imágenes", self.progreso.hechas)
                    break
                trozo = faltan[i:i + LOTE]
                preparadas = list(piscina.map(clip.preparar, trozo))
                utiles = [(r, t) for r, t in zip(trozo, preparadas, strict=True) if t is not None]
                if utiles:
                    try:
                        vectores = self._codificador.de_imagenes([t for _, t in utiles])
                    except Exception:  # noqa: BLE001
                        log.warning("falló un lote de imágenes", exc_info=True)
                        self.progreso.hechas += len(trozo)
                        continue
                    for (ruta, _), vector in zip(utiles, vectores, strict=True):
                        ficha = _ficha(ruta)
                        if ficha is None:
                            continue
                        nuevas_rutas.append(ficha[0])
                        nuevas_marcas.append([ficha[1], ficha[2]])
                        nuevos_vectores.append(vector)
                self.progreso.hechas += len(trozo)
                if avisar is not None:
                    avisar(self.progreso)

        if nuevos_vectores:
            self._fundir(nuevas_rutas, nuevas_marcas, np.vstack(nuevos_vectores))
            self._guardar()
        self.progreso.terminado = True
        return len(nuevos_vectores)

    def _fundir(self, rutas: list[str], marcas: list[list], vectores: np.ndarray) -> None:
        """Mete lo nuevo, reemplazando lo que ya estuviera con esa ruta."""
        indice = {r: i for i, r in enumerate(self._rutas)}
        for j, ruta in enumerate(rutas):
            if (i := indice.get(ruta)) is not None and self._vectores is not None:
                self._vectores[i] = vectores[j]
                self._marcas[i] = marcas[j]
            else:
                indice[ruta] = len(self._rutas)
                self._rutas.append(ruta)
                self._marcas.append(marcas[j])
                self._vectores = (vectores[j:j + 1] if self._vectores is None
                                  else np.vstack([self._vectores, vectores[j:j + 1]]))

    def olvidar_las_que_ya_no_estan(self) -> int:
        """Quita del índice las imágenes que hayas borrado o movido."""
        if not self.listo:
            return 0
        siguen = [i for i, r in enumerate(self._rutas) if Path(r).exists()]
        quitadas = len(self._rutas) - len(siguen)
        if quitadas:
            self._rutas = [self._rutas[i] for i in siguen]
            self._marcas = [self._marcas[i] for i in siguen]
            self._vectores = self._vectores[siguen]
            self._guardar()
        return quitadas

    # ── Buscar ───────────────────────────────────────────────────────

    def buscar(self, descripcion: str, cuantas: int = 5) -> list[Coincidencia]:
        """Las que más se parecen. En inglés: CLIP se entrenó así.

        Siempre devuelve algo si hay álbum, porque CLIP no sabe decir "no
        la tengo" (ver `_CONTRASTES`). Lo que sí trae cada resultado es
        cuánta confianza merece, para que NOVA no afirme lo que no sabe.
        """
        if not self.listo or self._vectores is None:
            return []
        consulta = self._codificador.de_texto(descripcion)
        parecidos = self._vectores @ consulta
        mejores = np.argsort(-parecidos)[:cuantas]

        contra = np.stack([self._codificador.de_texto(c) for c in _CONTRASTES])
        salida = []
        for i in mejores:
            puntos = np.array([parecidos[i], *(self._vectores[i] @ contra.T)]) * TEMPERATURA
            exp = np.exp(puntos - puntos.max())
            salida.append(Coincidencia(Path(self._rutas[i]), float(parecidos[i]),
                                       float(exp[0] / exp.sum())))
        return salida
