"""Voz de NOVA: oír en dos etapas y hablar. Todo local y gratis."""

from .chime import play as play_chime
from .listener import VoiceListener
from .speaker import Speaker, limpiar_para_voz
from .transcriptor import PROMPT_NOVA, Transcriptor
from .wake import DetectorWake

__all__ = [
    "DetectorWake",
    "PROMPT_NOVA",
    "Speaker",
    "Transcriptor",
    "VoiceListener",
    "limpiar_para_voz",
    "play_chime",
]
