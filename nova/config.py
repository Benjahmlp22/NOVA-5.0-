"""Configuración de NOVA — un solo sitio, valores ya afinados.

Cada default que lleva comentario está medido en la máquina real, no
copiado de un tutorial.  Si algo va lento o raro, es aquí donde se mira
primero.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(key: str, default: str) -> str:
    return os.getenv(key, default).strip()


def _env_bool(key: str, default: bool) -> bool:
    return _env(key, str(default)).lower() in ("1", "true", "yes", "on", "si", "sí")


def _es_modelo(p: Path) -> bool:
    return (p / "am").exists() or (p / "conf").exists()


def _buscar(candidatos: list[Path]) -> Path:
    for c in candidatos:
        if _es_modelo(c):
            return c
        # ¿Es una carpeta contenedora con el modelo dentro?
        if c.is_dir():
            for child in sorted(c.iterdir()):
                if child.is_dir() and _es_modelo(child):
                    return child
    return candidatos[0]


def _find_wake_model() -> Path:
    """Modelo de Vosk para la ETAPA 1: detectar «NOVA» y nada más.

    Se prefiere el SMALL (58 MB) y no es por ahorrar: está medido el
    26/08 sobre 14 frases (8 con "nova", 6 trampas del tipo "no va a
    funcionar el mando").

        vosk-model-es-0.42 (2.3 GB)   despierta 4/8   falsas 4/6   47.2 s
        vosk-model-small   (58 MB)    despierta 5/8   falsas 3/6    0.4 s
        small + gramática ["nova"]    despierta 8/8   falsas 5/6    0.4 s

    El grande es PEOR que el pequeño en las dos columnas, y además no
    admite gramática restringida ("Runtime graphs are not supported by
    this model"), que es lo que sube el recall a 8/8. Sus 2.3 GB sólo
    compraban 47 segundos de arranque sordo.

    Que salte de más aquí da igual: esta etapa sólo abre la puerta, y la
    etapa 2 confirma antes de que suene nada.
    """
    explicit = os.getenv("NOVA_WAKE_MODEL")
    if explicit:
        return Path(explicit)
    return _buscar([
        ROOT / "models" / "vosk-small",
        ROOT.parent / "NOVA3.0-2027" / "data" / "models" / "vosk" / "vosk-model-small-es-0.42",
        ROOT.parent / "NOVA" / "vosk-model-small-es-0.42",
        ROOT / "models" / "vosk",
    ])


def _find_vosk_model() -> Path:
    """Modelo de Vosk grande, sólo como RESERVA de la etapa 2.

    Si no hay CUDA o faster-whisper falla, NOVA transcribe con esto antes
    que quedarse sorda. Peor (17.9% de WER contra 3.5%), pero viva.
    """
    explicit = os.getenv("NOVA_VOSK_MODEL")
    if explicit:
        return Path(explicit)
    return _buscar([
        ROOT / "models" / "vosk",
        ROOT.parent / "NOVA3.0-2027" / "data" / "models" / "vosk" / "vosk-model-es-0.42",
        ROOT.parent / "NOVA" / "vosk-model-small-es-0.42",
    ])


@dataclass(frozen=True)
class Config:
    # ── Modelo de lenguaje (Ollama, local y gratis) ──────────────────
    #
    # 127.0.0.1 y NUNCA "localhost": en Windows el resolver de Python
    # prueba IPv6 (::1) antes de caer a IPv4, y eso añadía ~2.5 s a CADA
    # llamada. Medido: 3.8 s con localhost vs 1.3 s con la IP. Era, con
    # diferencia, la causa nº1 de "NOVA va lenta".
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://127.0.0.1:11434"))
    # qwen3.5:4b responde en ~0.6-1 s en caliente, llama tools de forma
    # nativa y fiable, y razona mejor que qwen2.5:3b (menos respuestas
    # "tontas"). gemma4:e4b razona algo más pero pesa 9.6GB: cualquier
    # recarga en frío tarda 30-40s en un PC ajustado — para voz, la
    # latencia manda más que ganar un poco de inteligencia.
    model: str = field(default_factory=lambda: _env("NOVA_MODEL", "qwen3.5:4b"))
    # El modelo se queda en VRAM: recargarlo desde disco cuesta caro
    # (medido: 7-60 s según cuánto tarde el disco/antivirus en leer ~2 GB
    # la primera vez) frente a ~0.6 s ya cargado. Con un PC que no anda
    # sobrado, ese coste se paga UNA vez al día, no varias veces por hora.
    # 24h de VRAM ociosa (~2 GB de una tarjeta de 12 GB) sale gratis.
    keep_alive: str = field(default_factory=lambda: _env("NOVA_KEEP_ALIVE", "24h"))
    temperature: float = field(default_factory=lambda: float(_env("NOVA_TEMPERATURE", "0.6")))
    # Tope de tokens generados: acota la latencia peor caso sin cortar
    # frases a medias (con 200 se truncaban enumeraciones a mitad).
    max_tokens: int = field(default_factory=lambda: int(_env("NOVA_MAX_TOKENS", "350")))
    # Rondas del bucle de herramientas antes de forzar un cierre.
    max_rounds: int = field(default_factory=lambda: int(_env("NOVA_MAX_ROUNDS", "4")))
    request_timeout: float = field(default_factory=lambda: float(_env("NOVA_TIMEOUT", "120")))

    # ── Voz ──────────────────────────────────────────────────────────
    wake_word: str = field(default_factory=lambda: _env("NOVA_WAKE_WORD", "nova").lower())
    wake_model: Path = field(default_factory=_find_wake_model)
    vosk_model: Path = field(default_factory=_find_vosk_model)

    # ── Etapa 2: transcripción de la orden ───────────────────────────
    #
    # Medido el 26/08 sobre 20 órdenes reales grabadas hablando:
    #
    #   Vosk es-0.42                17.9%  10/20  0.76 s
    #   whisper small               16.1%  10/20  0.30 s
    #   whisper small + vocabulario  9.3%  11/20  0.32 s
    #   whisper medium              12.9%  13/20  0.61 s
    #   whisper medium + vocabulario 3.5%  17/20  0.50 s   ← este
    #   whisper large-v3-turbo      24.1%   8/20  0.61 s
    #
    # medium gana por 5x sobre Vosk por 560 MiB de VRAM y 0.2 s. Y turbo,
    # que es más grande, es el peor de todos: en órdenes de cinco palabras
    # el tamaño no compra nada.
    whisper_model: str = field(default_factory=lambda: _env("NOVA_WHISPER_MODEL", "medium"))
    whisper_compute: str = field(default_factory=lambda: _env("NOVA_WHISPER_COMPUTE", "int8_float16"))
    whisper_device: str = field(default_factory=lambda: _env("NOVA_WHISPER_DEVICE", "auto"))

    # ── Captura ──────────────────────────────────────────────────────
    # Segundos de audio que se guardan SIEMPRE hacia atrás. Sin esto, el
    # principio de la orden se pierde: cuando NOVA se entera de que la
    # están llamando, esa parte del audio ya pasó.
    preroll_s: float = field(default_factory=lambda: float(_env("NOVA_PREROLL", "1.0")))
    # Silencio que cierra una frase. 0.7 s es el punto donde deja de
    # cortar a mitad de frase (una coma da ~0.4 s) sin que la espera se
    # note. Cuenta entera en la latencia de punta a punta.
    silencio_fin_s: float = field(default_factory=lambda: float(_env("NOVA_SILENCIO_FIN", "0.7")))
    # Tope duro de un enunciado: si el umbral no vuelve a bajar (ruido
    # continuo, ventilador), no se puede grabar para siempre.
    max_enunciado_s: float = field(default_factory=lambda: float(_env("NOVA_MAX_ENUNCIADO", "12")))
    mic_exclusive: bool = field(default_factory=lambda: _env_bool("NOVA_MIC_EXCLUSIVO", False))
    # Cuánto se le puede seguir hablando sin repetir el nombre después de
    # que ella conteste. Ocho segundos es el hueco de un turno normal.
    # Con los veinte del timeout de sueño, NOVA procesaba como órdenes
    # todo lo que se dijera en la habitación durante ese rato.
    seguimiento_s: float = field(default_factory=lambda: float(_env("NOVA_SEGUIMIENTO", "8")))
    # Segundos de silencio tras despertar antes de volver a dormir.
    awake_timeout_s: float = field(default_factory=lambda: float(_env("NOVA_AWAKE_TIMEOUT", "20")))
    tts_enabled: bool = field(default_factory=lambda: _env_bool("NOVA_TTS", True))
    tts_rate: int = field(default_factory=lambda: int(_env("NOVA_TTS_RATE", "195")))
    chime_enabled: bool = field(default_factory=lambda: _env_bool("NOVA_CHIME", True))
    mic_device: int | None = field(
        default_factory=lambda: (
            int(os.getenv("NOVA_MIC_DEVICE")) if os.getenv("NOVA_MIC_DEVICE") else None
        )
    )

    # ── Comportamiento ───────────────────────────────────────────────
    # Turnos de conversación que se recuerdan (12 = contexto suficiente
    # para "¿qué dije antes?" sin inflar el prompt en cada llamada).
    history_turns: int = field(default_factory=lambda: int(_env("NOVA_HISTORY", "12")))
    # "solo_peligroso": abrir apps, crear archivos, capturas → directo.
    #   Borrar, cerrar procesos, ejecutar comandos → piden confirmación.
    # "estricto": todo lo que no sea de solo lectura pregunta.
    confirm_policy: str = field(default_factory=lambda: _env("NOVA_CONFIRM", "solo_peligroso"))

    # ── Interfaz ─────────────────────────────────────────────────────
    # Abajo a la DERECHA. NOVA4 la ponía a la izquierda, donde se pisa
    # con la barra de tareas de quien la tiene ahí y con el menú inicio.
    # Sólo vale la primera vez: a partir de ahí manda dónde la dejaste
    # (`data/ui.json`).
    orb_corner: str = field(default_factory=lambda: _env("NOVA_ORB_CORNER", "bottom-right"))
    glow_enabled: bool = field(default_factory=lambda: _env_bool("NOVA_GLOW", True))

    # ── Rutas ────────────────────────────────────────────────────────
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    workspace: Path = ROOT / "workspace"
    log_file: Path = ROOT / "data" / "nova.log"
    memory_file: Path = ROOT / "data" / "memory.json"
    screenshots: Path = ROOT / "data" / "screenshots"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.workspace, self.screenshots):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def voice_available(self) -> bool:
        return _es_modelo(self.wake_model) or _es_modelo(self.vosk_model)

    @property
    def wake_model_available(self) -> bool:
        return _es_modelo(self.wake_model)


CONFIG = Config()
