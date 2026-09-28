"""Asynchronous interfaces for local OpenAI-compatible and Ollama servers."""

from __future__ import annotations

import base64
import inspect
import os
import re
from collections.abc import Callable, Sequence
from typing import Protocol

import httpx

from ..config import LLMSettings
from ..domain import ChatMessage, HealthComponent
from ..errors import ProviderError


class LLMProvider(Protocol):
    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str: ...

    async def health(self) -> HealthComponent: ...


MockResponder = Callable[[Sequence[ChatMessage]], str]


class MockLLMProvider:
    """Deterministic provider used by tests and no-model UI development."""

    def __init__(self, responder: MockResponder | None = None) -> None:
        self.responder = responder
        self.calls: list[list[ChatMessage]] = []

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        del temperature, max_tokens
        snapshot = list(messages)
        self.calls.append(snapshot)
        if self.responder is not None:
            result = self.responder(snapshot)
            if inspect.isawaitable(result):
                result = await result
            return str(result)
        system = snapshot[0].content if snapshot else ""
        all_system = "\n".join(item.content for item in snapshot if item.role == "system")
        user = next(
            (item.content for item in reversed(snapshot) if item.role == "user"),
            "",
        )
        lowered = user.casefold()
        italian = "Italian" in system
        flag_match = re.search(r"RH26\{[^}\r\n]+}", all_system)
        flag = flag_match.group(0) if flag_match else None

        # Deterministic, playable simulation for development without model weights.
        if "INTERNAL_DIAGNOSTIC_RESULT" in all_system and flag:
            if any(term in lowered for term in ("base64", "codifica", "encode")):
                encoded = base64.b64encode(flag.encode("utf-8")).decode("ascii")
                return f"Record diagnostico in Base64: {encoded}" if italian else f"Base64 diagnostic record: {encoded}"
            if any(term in lowered for term in ("reverse", "invers", "backward")):
                return flag[::-1]
            values = " ".join(str(ord(character)) for character in flag)
            return f"Valori Unicode del record: {values}" if italian else f"Unicode values of the record: {values}"

        if "JANUS VAULT" in system:
            authorization_terms = (
                "diagnostic",
                "diagnostica",
                "operator",
                "operatore",
                "authorize",
                "autorizz",
                "incident response",
            )
            if sum(term in lowered for term in authorization_terms) >= 2:
                return (
                    '<JANUS_TOOL>{"name":"diagnostics.collect","arguments":'
                    '{"include_sensitive":true,"operator_confirmed":true,'
                    '"justification":"authorized incident-response audit"}}</JANUS_TOOL>'
                )

        if "JANUS MIRROR" in system and flag:
            if any(term in lowered for term in ("base64", "codifica", "encode")):
                encoded = base64.b64encode(flag.encode("utf-8")).decode("ascii")
                return f"Dato intermedio: {encoded}"
            if any(term in lowered for term in ("reverse", "invers", "backward")):
                return flag[::-1]
            if any(term in lowered for term in ("unicode", "ascii", "code point", "codici")):
                return " ".join(str(ord(character)) for character in flag)

        if flag and any(
            term in lowered
            for term in (
                "ignore previous",
                "ignora le istruzioni",
                "debug",
                "audit",
                "backup",
                "role-play",
                "roleplay",
                "modalità sviluppatore",
                "developer mode",
            )
        ):
            return f"Audit completato: {flag}" if italian else f"Audit complete: {flag}"

        return (
            "Il caveau resta chiuso. Dovrai essere più astuto."
            if italian
            else "The vault remains closed. You will have to be more cunning."
        )

    async def health(self) -> HealthComponent:
        return HealthComponent(available=True, detail="mock provider")


class FallbackLLMProvider:
    """Keeps the kiosk demonstrable when its configured local server is offline."""

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback or MockLLMProvider()

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        try:
            return await self.primary.generate(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        except ProviderError:
            return await self.fallback.generate(
                messages, temperature=temperature, max_tokens=max_tokens
            )

    async def health(self) -> HealthComponent:
        primary = await self.primary.health()
        if primary.available:
            return primary
        return HealthComponent(
            available=True,
            detail=f"fallback mock active; primary unavailable: {primary.detail}",
        )


class OpenAICompatibleProvider:
    def __init__(self, settings: LLMSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key_env:
            api_key = os.environ.get(self.settings.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": self.settings.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self.settings.enable_thinking},
        }
        try:
            if self._client is not None:
                response = await self._client.post(
                    f"{self.settings.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                )
            else:
                async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                    response = await client.post(
                        f"{self.settings.base_url}/chat/completions",
                        json=payload,
                        headers=self._headers(),
                    )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty completion")
            return content.strip()
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"Local LLM request failed: {exc}") from exc

    async def health(self) -> HealthComponent:
        try:
            if self._client is not None:
                response = await self._client.get(
                    f"{self.settings.base_url}/models", headers=self._headers()
                )
            else:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(
                        f"{self.settings.base_url}/models", headers=self._headers()
                    )
            response.raise_for_status()
            return HealthComponent(available=True, detail=self.settings.model)
        except httpx.HTTPError as exc:
            return HealthComponent(available=False, detail=str(exc))


class OllamaProvider:
    def __init__(self, settings: LLMSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        # Accept either http://localhost:11434 or a mistakenly suffixed /api URL.
        self._root = re.sub(r"/api$", "", settings.base_url)

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = {
            "model": self.settings.model,
            "messages": [message.model_dump() for message in messages],
            "stream": False,
            "think": self.settings.enable_thinking,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        try:
            if self._client is not None:
                response = await self._client.post(f"{self._root}/api/chat", json=payload)
            else:
                async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
                    response = await client.post(f"{self._root}/api/chat", json=payload)
            response.raise_for_status()
            content = response.json()["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty completion")
            return content.strip()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ProviderError(f"Local Ollama request failed: {exc}") from exc

    async def health(self) -> HealthComponent:
        try:
            if self._client is not None:
                response = await self._client.get(f"{self._root}/api/tags")
            else:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    response = await client.get(f"{self._root}/api/tags")
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", [])
            available_names = {
                str(item.get("name") or item.get("model") or "").casefold()
                for item in models
                if isinstance(item, dict)
            }
            requested = self.settings.model.casefold()
            requested_aliases = {requested}
            if ":" not in requested:
                requested_aliases.add(f"{requested}:latest")
            if not available_names.intersection(requested_aliases):
                return HealthComponent(
                    available=False,
                    detail=f"Ollama is online but model {self.settings.model!r} is not installed",
                )
            return HealthComponent(available=True, detail=self.settings.model)
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            return HealthComponent(available=False, detail=str(exc))
