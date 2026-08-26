"""Cliente de Ollama — LLM local, gratis, con function calling nativo.

Síncrono a propósito: NOVA lo llama desde un hilo trabajador y el hilo
principal es el de Qt.  Meter asyncio aquí solo añadiría un segundo
bucle de eventos que coordinar, sin ganar nada.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("nova.llm")


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    eval_count: int = 0


class OllamaError(RuntimeError):
    """Ollama no responde o devolvió algo inesperado."""


class OllamaClient:
    def __init__(
        self,
        url: str,
        model: str,
        *,
        keep_alive: str = "30m",
        temperature: float = 0.6,
        max_tokens: int = 350,
        timeout: float = 120.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.max_tokens = max_tokens
        # Un cliente reutilizado mantiene viva la conexión TCP; abrir
        # una nueva por mensaje añade handshake a cada respuesta.
        self._http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    # ── API ──────────────────────────────────────────────────────────

    def available(self) -> bool:
        try:
            self._http.get(f"{self.url}/api/version", timeout=3.0).raise_for_status()
            return True
        except Exception:
            return False

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_trozo: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Pide una respuesta. Con `on_trozo`, la va entregando según llega.

        Streaming no es un lujo aquí: esperar el punto final antes de
        abrir la boca metía hasta 2.66 s de silencio en el peor caso
        medido, y ése era el tramo más gordo de la latencia. Con los
        trozos, NOVA puede empezar a hablar con la primera frase mientras
        el modelo sigue escribiendo la segunda.
        """
        if on_trozo is not None:
            return self._chat_en_trozos(messages, tools, on_trozo)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            # Los modelos "thinking" (gemma4, qwen3.x) razonan en silencio
            # antes de responder y multiplican la latencia. Un asistente
            # de voz tiene que reaccionar, no meditar.
            "think": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = self._http.post(f"{self.url}/api/chat", json=payload)
            # Modelos sin soporte de "think" rechazan el campo: reintento
            # limpio en vez de fallar la conversación entera.
            if resp.status_code == 400 and "think" in resp.text.lower():
                payload.pop("think", None)
                resp = self._http.post(f"{self.url}/api/chat", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError as exc:
            raise OllamaError(
                f"No encuentro Ollama en {self.url}. ¿Está abierto?"
            ) from exc
        except Exception as exc:
            log.exception("fallo llamando a Ollama")
            raise OllamaError(f"Error del modelo: {exc}") from exc

        msg = data.get("message") or {}
        return LLMResponse(
            text=msg.get("content") or "",
            tool_calls=self._parse_tool_calls(msg),
            eval_count=int(data.get("eval_count") or 0),
        )

    def _chat_en_trozos(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_trozo: Callable[[str], None],
    ) -> LLMResponse:
        """Igual que `chat`, pero entregando el texto según se genera.

        Ollama manda un JSON por línea. Los que traen `tool_calls` no son
        texto para el usuario: esa ronda va a ejecutar herramientas y no
        se dice nada en alto hasta que haya respuesta de verdad.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
            "think": False,
            "options": {"temperature": self.temperature, "num_predict": self.max_tokens},
        }
        if tools:
            payload["tools"] = tools

        partes: list[str] = []
        llamadas: list[ToolCall] = []
        evaluados = 0
        try:
            with self._http.stream("POST", f"{self.url}/api/chat", json=payload) as resp:
                if resp.status_code == 400:
                    # Modelos sin soporte de "think" rechazan el campo.
                    resp.read()
                    payload.pop("think", None)
                    return self._chat_en_trozos(messages, tools, on_trozo)
                resp.raise_for_status()
                for linea in resp.iter_lines():
                    if not linea:
                        continue
                    try:
                        dato = json.loads(linea)
                    except ValueError:
                        continue
                    msg = dato.get("message") or {}
                    llamadas.extend(self._parse_tool_calls(msg))
                    trozo = msg.get("content") or ""
                    if trozo:
                        partes.append(trozo)
                        on_trozo(trozo)
                    if dato.get("done"):
                        evaluados = int(dato.get("eval_count") or 0)
        except httpx.ConnectError as exc:
            raise OllamaError(f"No encuentro Ollama en {self.url}. ¿Está abierto?") from exc
        except Exception as exc:
            log.exception("fallo llamando a Ollama en streaming")
            raise OllamaError(f"Error del modelo: {exc}") from exc

        return LLMResponse(text="".join(partes), tool_calls=llamadas, eval_count=evaluados)

    # ── Conversión ───────────────────────────────────────────────────

    @staticmethod
    def _parse_tool_calls(msg: dict[str, Any]) -> list[ToolCall]:
        out: list[ToolCall] = []
        for item in msg.get("tool_calls") or []:
            fn = (item or {}).get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (ValueError, TypeError):
                    args = {}
            out.append(
                ToolCall(
                    id=str(item.get("id") or uuid.uuid4().hex[:12]),
                    name=str(fn.get("name") or ""),
                    args=args if isinstance(args, dict) else {},
                )
            )
        return out
