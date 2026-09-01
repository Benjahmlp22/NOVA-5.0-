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
    # Modelo de repuesto para cuando un juego se queda con la VRAM.
    #
    # No es una corazonada: medido el 27/08 con Star Citizen abierto
    # (11.0 GB de 12.3 ocupados), preguntando "qué hora es" con el
    # catálogo de herramientas entero:
    #
    #   qwen3.5:4b   4.36-7.44 s   sólo el 10% del modelo en la GPU
    #   qwen2.5:3b   1.11-1.19 s   el 48% en la GPU
    #
    # El pequeño CABE en lo que sobra, y por eso va de 4 a 6 veces más
    # rápido. No es que razone mejor — es que el otro está corriendo en
    # CPU sin que nadie lo diga.
    model_ligero: str = field(default_factory=lambda: _env("NOVA_MODEL_LIGERO", "qwen2.5:3b"))
    # Por debajo de esta fracción del modelo residente en VRAM, se cambia
    # al ligero. 0.85 y no 1.0 porque Ollama deja siempre algo fuera.
    residencia_minima: float = field(
        default_factory=lambda: float(_env("NOVA_RESIDENCIA_MINIMA", "0.85"))
    )
    # ── Cerebro rápido, en la nube y APAGADO por defecto ─────────────
    #
    # Sin clave no existe: NOVA no lo mira nunca y todo va como siempre.
    # Con clave sigue haciendo falta pedirlo ("modo rápido"), porque
    # encenderlo significa que lo que dices sale del PC — y eso lo
    # decide Benja, no un umbral de VRAM. Ver `nova/llm/remoto.py`.
    remoto_url: str = field(
        default_factory=lambda: _env("NOVA_REMOTO_URL", "https://api.groq.com/openai/v1")
    )
    # Caduca: los proveedores retiran modelos cada pocos meses. Si NOVA
    # dice que no existe, se pone aquí el nombre nuevo.
    remoto_model: str = field(
        default_factory=lambda: _env("NOVA_REMOTO_MODEL", "llama-3.3-70b-versatile")
    )
    remoto_key: str = field(default_factory=lambda: _env("NOVA_GROQ_KEY", ""))
    remoto_key_file: Path = ROOT / "data" / "groq.key"
    # Cambiar solo a la nube cuando haya un juego delante. Apagado a
    # propósito: mandar tus datos fuera no puede ser un efecto
    # secundario de abrir un juego.
    remoto_auto: bool = field(default_factory=lambda: _env_bool("NOVA_REMOTO_AUTO", False))

    temperature: float = field(default_factory=lambda: float(_env("NOVA_TEMPERATURE", "0.6")))
    # Tope de tokens generados. Es un TECHO, no un objetivo: una
    # respuesta hablada normal usa 40-80 y no tarda más por tenerlo alto.
    #
    # Subido de 350 a 2048 el 01/09, y no por capricho: con 350 la
    # herramienta de escribir código era inútil. El contenido del archivo
    # son tokens generados, así que un juego en HTML se cortaba a la
    # cuarta línea y NOVA escribía un archivo roto creyendo que lo había
    # hecho bien. Lo que se dice en alto lo sigue acotando
    # `recortar_para_voz`, que es donde tiene que estar.
    max_tokens: int = field(default_factory=lambda: int(_env("NOVA_MAX_TOKENS", "2048")))
    # La ventana de contexto que se le pide a Ollama.
    #
    # BUG DE FONDO encontrado el 27/08: nunca se había fijado. Ollama usa
    # 2048 por defecto si nadie dice lo contrario, y un turno cualquiera
    # con el catálogo de 52 herramientas ya pesa 2050 — por ENCIMA del
    # límite. El modelo respondía a un prompt cortado a la mitad y el
    # síntoma no tenía pinta de esto: "cierra Spotify" llamaba a
    # voz_cambiar con "ponte voz de hombre", sin relación ninguna con lo
    # pedido. Confirmado forzando num_ctx=8192 en la misma petición: ahí
    # sí acertó (app_close, Spotify).
    #
    # 8192 cabía de sobra para hablar: sistema + catálogo + historial de
    # 12 turnos + una ronda de herramientas no llegaba a acercarse, y en
    # VRAM cuesta 280 MB medidos (3.06 GB a 2048 contra 3.34 GB a 8192).
    #
    # Subido a 16384 el 01/09 para poder leer código de verdad. Un
    # archivo de 20.000 caracteres son ~6.000 tokens: con 8192 no cabía
    # el archivo Y el resto del prompt a la vez, así que "léete esto y
    # dime qué falla" era imposible por construcción. El coste es otro
    # medio giga de VRAM; cuando no lo haya, el modo ligero ya se encarga.
    num_ctx: int = field(default_factory=lambda: int(_env("NOVA_NUM_CTX", "16384")))
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
    # que ella conteste.
    #
    # Estaba en 8 s, que es el hueco de UN turno, y la queja de Benja el
    # 01/09 fue justo ésa: «que no se calle la conversación, que me deje
    # seguir a menos que le diga adiós». Ocho segundos son los que tardas
    # en pensar la siguiente frase, así que a la segunda ya tenías que
    # volver a decir «nova».
    #
    # 35 s es una conversación de verdad. Lo que antes protegía este
    # número —que no procese como órdenes lo que se diga en la
    # habitación— ya lo hacen dos filtros que entonces no existían: el
    # `creible` de Whisper descarta lo que no es habla, y las frases de
    # menos de dos palabras se ignoran. Y sigue cerrándose al oír una
    # despedida, que es como se cierra una conversación de verdad.
    seguimiento_s: float = field(default_factory=lambda: float(_env("NOVA_SEGUIMIENTO", "35")))
    # Poder cortarla hablando por encima. Con altavoces en vez de cascos,
    # si NOVA se interrumpe a sí misma por su propio eco, ponlo a false.
    interrumpir: bool = field(default_factory=lambda: _env_bool("NOVA_INTERRUMPIR", True))
    # Segundos de silencio tras despertar antes de volver a dormir.
    # Subido de 20 a 120 por el mismo motivo que `seguimiento_s`: en una
    # conversación con pausas, dormirse a los veinte segundos obliga a
    # despertarla otra vez a mitad de charla. Se sigue durmiendo al oír
    # una despedida, y ahí es inmediato.
    awake_timeout_s: float = field(default_factory=lambda: float(_env("NOVA_AWAKE_TIMEOUT", "120")))
    # Cuánto aguanta despierta cuando ha preguntado algo y espera tu
    # respuesta. Un minuto: lo que tardas en mirar la pantalla, pensarlo
    # y contestar. Los 20 s normales se le quedaban cortos justo cuando
    # más importaba — pedía permiso y se dormía antes del "sí".
    espera_respuesta_s: float = field(
        default_factory=lambda: float(_env("NOVA_ESPERA_RESPUESTA", "60"))
    )
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

    # La carpeta donde vive todo lo que Benja programa. Es la ÚNICA que
    # las herramientas de código pueden mirar y en la que pueden
    # ejecutar: dejar que un modelo de 4B elija ruta a partir de lo que
    # ha entendido por un micrófono no es una opción.
    proyectos_dir: Path = field(
        default_factory=lambda: Path(
            _env("NOVA_PROYECTOS", str(Path.home() / "Desktop" / "proyectos"))
        )
    )
    # Cuánto se le deja correr a un script antes de matarlo. Uno que se
    # queda esperando input() no termina nunca, y NOVA se quedaría
    # colgada en "pensando" para siempre. Noventa segundos dan de sobra
    # para una suite de tests mediana.
    codigo_timeout: float = field(default_factory=lambda: float(_env("NOVA_CODIGO_TIMEOUT", "90")))

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
