"""Prueba de humo: cerebro completo sin interfaz.

    python smoke.py

Sirve para comprobar que Ollama responde, que las herramientas se
ejecutan y cuánto tarda cada turno — sin abrir ventanas ni micrófono.
"""

from __future__ import annotations

import logging
import time

from nova.config import CONFIG
from nova.core.agent import Agent
from nova.core.awareness import Awareness
from nova.core.conversation import Conversation, build_system_prompt
from nova.llm.ollama import OllamaClient
from nova.tools import build_registry

FRASES = [
    "hola",
    "que hora es",
    "que estoy haciendo ahora mismo",
    "que apps tengo abiertas",
    "hazme una captura de pantalla",
    "recuerda que mi color favorito es el verde",
    "cual es mi color favorito",
]


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    CONFIG.ensure_dirs()

    print(f"Modelo   : {CONFIG.model}")
    print(f"Ollama   : {CONFIG.ollama_url}")
    print(f"Voz      : {'sí' if CONFIG.voice_available else 'NO'} ({CONFIG.vosk_model.name})")
    print()

    llm = OllamaClient(
        CONFIG.ollama_url, CONFIG.model,
        keep_alive=CONFIG.keep_alive,
        temperature=CONFIG.temperature,
        max_tokens=CONFIG.max_tokens,
    )
    if not llm.available():
        print(f"✗ Ollama no responde en {CONFIG.ollama_url}")
        return 1

    tools = build_registry()
    print(f"Herramientas: {len(tools.names())} registradas, "
          f"{len(tools.llm_schemas())} ofrecidas al modelo\n")

    awareness = Awareness()
    conv = Conversation(CONFIG.history_turns)
    agent = Agent(llm, tools, max_rounds=CONFIG.max_rounds)

    for frase in FRASES:
        t0 = time.monotonic()
        reply = agent.run(build_system_prompt(awareness.snapshot()), conv.history(), frase)
        dt = time.monotonic() - t0
        conv.add_user(frase)
        conv.add_assistant(reply.text)

        herramientas = ", ".join(reply.tools_used) or "—"
        print(f"[{dt:5.2f}s] «{frase}»")
        print(f"          tools: {herramientas} | rondas: {reply.rounds}")
        print(f"          NOVA: {reply.text[:150]}")
        if reply.pending:
            print(f"          (pide confirmación: {reply.pending.summary})")
        print()

    llm.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
