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
        num_ctx: int = 8192,
        timeout: float = 120.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.num_ctx = num_ctx
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

    def residencia(self) -> float:
        """Qué fracción del modelo está de verdad en la GPU, de 0 a 1.

        Ollama dice en `/api/ps` cuánto ocupa el modelo (`size`) y cuánto
        de eso está en VRAM (`size_vram`). Cuando un juego se queda con
        la tarjeta, el driver expulsa al modelo y esa fracción se hunde:
        medido con Star Citizen abierto, 330 MB de 3.2 GB — el 10%. El
        resto corre en CPU y las respuestas pasan de 1-4 s a 10-48 s.

        Es la única señal fiable de "voy lenta por falta de VRAM".
        Mirar la VRAM libre con nvidia-smi no vale: dice cuánta hay, no
        si el modelo está dentro.

        Devuelve 1.0 si no se puede saber: ante la duda, no alarmar.
        """
        try:
            datos = self._http.get(f"{self.url}/api/ps", timeout=3.0).json()
        except Exception:  # noqa: BLE001
            return 1.0
        for m in datos.get("models") or []:
            if m.get("model") != self.model and m.get("name") != self.model:
                continue
            total = float(m.get("size") or 0)
            en_vram = float(m.get("size_vram") or 0)
            if total <= 0:
                return 1.0
            return max(0.0, min(1.0, en_vram / total))
        # No está cargado todavía: no es que vaya lento, es que no ha
        # empezado.
        return 1.0

    def tiene_modelo(self, nombre: str) -> bool:
        """¿Está ese modelo descargado?

        Se pregunta ANTES de cambiarse a él: cambiar a uno que no existe
        deja a NOVA muda, y el fallo aparecería a mitad de una respuesta
        en vez de en el arranque.
        """
        try:
            datos = self._http.get(f"{self.url}/api/tags", timeout=5.0).json()
        except Exception:  # noqa: BLE001
            return False
        return any(
            (m.get("model") or m.get("name") or "") == nombre
            for m in datos.get("models") or []
        )

    def usar_modelo(self, nombre: str) -> None:
        """Cambia de modelo y suelta el anterior de la memoria.

        Soltarlo importa: con `keep_alive` largo los dos se quedarían
        residentes, y el motivo de cambiar es justo que no cabe uno.
        """
        if nombre == self.model:
            return
        anterior = self.model
        self.model = nombre
        log.info("cambio de modelo: %s → %s", anterior, nombre)
        try:
            # keep_alive 0 = descárgalo ya.
            self._http.post(
                f"{self.url}/api/generate",
                json={"model": anterior, "keep_alive": 0},
                timeout=10.0,
            )
        except Exception:  # noqa: BLE001
            log.debug("no pude descargar %s", anterior, exc_info=True)

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
                "num_ctx": self.num_ctx,
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
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
                "num_ctx": self.num_ctx,
            },
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
