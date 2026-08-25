"""Voz de NOVA: escuchar (Vosk) y hablar (SAPI5). Todo local y gratis."""

from .chime import play as play_chime
from .listener import VoiceListener
from .speaker import Speaker, limpiar_para_voz

__all__ = ["VoiceListener", "Speaker", "play_chime", "limpiar_para_voz"]
