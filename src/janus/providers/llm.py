"""Asynchronous interfaces for local OpenAI-compatible and Ollama servers, plus the
optional remote Hugging Face Inference Providers router."""

from __future__ import annotations

import asyncio
import base64
import inspect
import math
import os
import re
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Protocol

import httpx

from ..config import LLMSettings
from ..domain import ChatMessage, HealthComponent
from ..errors import CapacityError, ProviderError


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


class GatedLLMProvider:
    """Caps concurrent model calls and bounds the waiting queue across all sessions.

    Waiters are served FIFO by the semaphore. When the queue is full the caller
    gets CapacityError (HTTP 503) with a retry estimate instead of waiting forever.
    """

    def __init__(
        self,
        inner: LLMProvider,
        *,
        max_concurrent: int,
        max_queue: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.inner = inner
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._max_queue = max_queue
        self._clock = clock
        self._waiting = 0
        self._average_seconds = 5.0

    @property
    def waiting(self) -> int:
        return self._waiting

    def _retry_after(self) -> int:
        estimate = self._average_seconds * (self._waiting + 1) / self._max_concurrent
        return min(60, max(2, math.ceil(estimate)))

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        if self._semaphore.locked() and self._waiting >= self._max_queue:
            raise CapacityError(self._retry_after())
        self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            self._waiting -= 1
        started = self._clock()
        try:
            return await self.inner.generate(
                messages, temperature=temperature, max_tokens=max_tokens
            )
        finally:
            self._semaphore.release()
            elapsed = self._clock() - started
            self._average_seconds = 0.8 * self._average_seconds + 0.2 * elapsed

    async def health(self) -> HealthComponent:
        return await self.inner.health()


class OpenAICompatibleProvider:
    error_label = "Local LLM"

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

    def _payload(
        self, messages: Sequence[ChatMessage], *, temperature: float, max_tokens: int
    ) -> dict[str, object]:
        return {
            "model": self.settings.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self.settings.enable_thinking},
        }

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        payload = self._payload(messages, temperature=temperature, max_tokens=max_tokens)
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
            raise ProviderError(f"{self.error_label} request failed: {exc}") from exc

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


HF_WHOAMI_URL = "https://huggingface.co/api/whoami-v2"
HF_INFERENCE_PERMISSION = "inference.serverless.write"
HF_ROUTING_POLICIES = frozenset({"fastest", "cheapest", "preferred"})


class HuggingFaceProvider(OpenAICompatibleProvider):
    """Remote inference through the Hugging Face Inference Providers router.

    The model may pin an operator (``org/model:provider``) or a routing policy
    (``org/model:cheapest``); without a suffix the router chooses the operator.
    """

    error_label = "Hugging Face"

    @staticmethod
    def _env(name: str | None) -> str | None:
        if not name:
            return None
        return os.environ.get(name, "").strip() or None

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        bill_to = self._env(self.settings.bill_to_env)
        if bill_to:
            headers["X-HF-Bill-To"] = bill_to
        return headers

    def _payload(
        self, messages: Sequence[ChatMessage], *, temperature: float, max_tokens: int
    ) -> dict[str, object]:
        payload = super()._payload(messages, temperature=temperature, max_tokens=max_tokens)
        # A vLLM/llama.cpp extension that router operators are not required to accept.
        del payload["chat_template_kwargs"]
        return payload

    def _missing_token(self) -> str | None:
        if self._env(self.settings.api_key_env) is None:
            return f"Hugging Face is not configured: {self.settings.api_key_env} is not set"
        return None

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float,
        max_tokens: int,
    ) -> str:
        missing = self._missing_token()
        if missing:
            raise ProviderError(missing)
        return await super().generate(messages, temperature=temperature, max_tokens=max_tokens)

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                yield client

    def _billing_problem(self, identity: dict) -> str | None:
        bill_to = self._env(self.settings.bill_to_env)
        if bill_to is None:
            return None
        token = identity.get("auth", {}).get("accessToken", {})
        if token.get("role") == "fineGrained":
            allowed = any(
                scope.get("entity", {}).get("name") == bill_to
                and HF_INFERENCE_PERMISSION in scope.get("permissions", [])
                for scope in token.get("fineGrained", {}).get("scoped", [])
            )
        else:
            allowed = any(org.get("name") == bill_to for org in identity.get("orgs", []))
        if allowed:
            return None
        # Without this grant the router ignores X-HF-Bill-To and bills the user silently.
        return (
            f"Hugging Face token cannot bill {bill_to}: grant {HF_INFERENCE_PERMISSION} "
            "on that organization"
        )

    def _model_problem(self, catalog: dict) -> str | None:
        model_id, _, route = self.settings.model.partition(":")
        entry = next(
            (item for item in catalog.get("data", []) if item.get("id") == model_id), None
        )
        if entry is None:
            return f"{model_id} is not served by any Hugging Face inference provider"
        if route and route not in HF_ROUTING_POLICIES:
            providers = {item.get("provider") for item in entry.get("providers", [])}
            if route not in providers:
                return f"{model_id} is not served by the {route!r} inference provider"
        return None

    async def health(self) -> HealthComponent:
        missing = self._missing_token()
        if missing:
            return HealthComponent(available=False, detail=missing)
        try:
            async with self._session() as client:
                # The router lists models even for invalid tokens, so verify the token itself.
                identity = await client.get(
                    HF_WHOAMI_URL,
                    headers={"Authorization": self._headers()["Authorization"]},
                )
                if identity.status_code in {401, 403}:
                    return HealthComponent(
                        available=False, detail="Hugging Face rejected the configured token"
                    )
                identity.raise_for_status()
                problem = self._billing_problem(identity.json())
                if problem is None:
                    catalog = await client.get(
                        f"{self.settings.base_url}/models", headers=self._headers()
                    )
                    catalog.raise_for_status()
                    problem = self._model_problem(catalog.json())
        except (httpx.HTTPError, AttributeError, TypeError, ValueError) as exc:
            return HealthComponent(available=False, detail=f"Hugging Face is unreachable: {exc}")
        if problem:
            return HealthComponent(available=False, detail=problem)
        return HealthComponent(available=True, detail=f"{self.settings.model} via Hugging Face")


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
