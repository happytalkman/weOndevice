"""OpenJarvis-compatible inference engines: Local GGUF + OpenRouter Free Hybrid Auto-Fallback."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import psutil
from openjarvis.core.registry import EngineRegistry
from openjarvis.core.types import Message
from openjarvis.engine._base import messages_to_dicts
from openjarvis.engine._stubs import InferenceEngine


DEFAULT_GGUF_PATH = Path(
    os.environ.get(
        "WEONDEVICE_MODEL_PATH",
        "models/qwen2.5-0.5b-instruct-q4_k_m.gguf",
    )
)
CONFIG_PATH = Path(os.environ.get("WEONDEVICE_HOME", ".weondevice")) / "config.json"
DEFAULT_OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
LOW_RAM_THRESHOLD_GB = 2.0


def load_engine_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_engine_config(data: Dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = load_engine_config()
    existing.update({k: v for k, v in data.items() if v is not None})
    CONFIG_PATH.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


@EngineRegistry.register("weondevice_llamacpp")
class InProcessLlamaCppEngine(InferenceEngine):
    """Runs quantized GGUF models directly in-process on CPU/GPU via llama-cpp-python."""

    engine_id = "weondevice_llamacpp"
    is_cloud = False

    def __init__(
        self,
        model_path: Optional[str | Path] = None,
        n_ctx: int = 1024,
        n_threads: Optional[int] = None,
    ) -> None:
        self.model_path = Path(model_path) if model_path else DEFAULT_GGUF_PATH
        self.n_ctx = n_ctx
        self.n_threads = n_threads or max(1, (os.cpu_count() or 2))
        self._llm: Any = None

    def _ensure_loaded(self) -> Any:
        if self._llm is None:
            if not self.model_path.exists():
                raise FileNotFoundError(
                    f"Local GGUF model not found at {self.model_path}. "
                    "Set WEONDEVICE_MODEL_PATH or run model download."
                )
            from llama_cpp import Llama  # type: ignore[import-not-found]

            self._llm = Llama(
                model_path=str(self.model_path),
                n_ctx=self.n_ctx,
                n_threads=self.n_threads,
                verbose=False,
            )
        return self._llm

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        llm = self._ensure_loaded()
        formatted = messages_to_dicts(messages)
        resp = llm.create_chat_completion(
            messages=formatted,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = resp["choices"][0]["message"]
        usage = resp.get("usage", {})
        return {
            "content": choice.get("content") or "",
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "model": self.model_path.name,
            "engine": self.engine_id,
        }

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        llm = self._ensure_loaded()
        formatted = messages_to_dicts(messages)
        stream_iter = llm.create_chat_completion(
            messages=formatted,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        for chunk in stream_iter:
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            text = delta.get("content")
            if text:
                yield text

    def list_models(self) -> List[str]:
        return [self.model_path.name] if self.model_path.exists() else []

    def health(self) -> bool:
        return self.model_path.exists()

    def close(self) -> None:
        self._llm = None


@EngineRegistry.register("openrouter_free")
class OpenRouterFreeEngine(InferenceEngine):
    """OpenRouter free-tier LLM engine ($0.00) for low-spec / low-RAM laptops."""

    engine_id = "openrouter_free"
    is_cloud = True

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self._explicit_key = api_key
        self._explicit_model = model

    def _get_key(self) -> str:
        if self._explicit_key:
            return self._explicit_key
        cfg = load_engine_config()
        return (os.environ.get("OPENROUTER_API_KEY") or cfg.get("openrouter_api_key") or "").strip()

    def _get_model(self) -> str:
        if self._explicit_model:
            return self._explicit_model
        cfg = load_engine_config()
        return (cfg.get("openrouter_model") or DEFAULT_OPENROUTER_MODEL).strip()

    def health(self) -> bool:
        return bool(self._get_key())

    def list_models(self) -> List[str]:
        return [self._get_model()]

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        api_key = self._get_key()
        if not api_key:
            raise RuntimeError(
                "OpenRouter API 키가 설정되지 않았습니다. 화면 상단 'OpenRouter 무료 설정'에서 무료 API 키(sk-or-v1-...)를 입력해 주세요."
            )
        target_model = model or self._get_model()
        payload = {
            "model": target_model,
            "messages": messages_to_dicts(messages),
            "temperature": temperature,
            "max_tokens": max(max_tokens, 1500),
            "reasoning": {"exclude": True},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/happytalkman/weOndevice",
            "X-Title": "Jarvis-RG On-Device AI",
        }
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]["message"]
        content = (choice.get("content") or "").strip()
        if not content:
            content = (choice.get("reasoning") or choice.get("reasoning_content") or "").strip()
        usage = data.get("usage", {})
        return {
            "content": content,
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "model": data.get("model", target_model),
            "engine": self.engine_id,
        }

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 512,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        res = self.generate(messages, model=model, temperature=temperature, max_tokens=max_tokens)
        yield res["content"]


class HybridOnDeviceEngine:
    """Automatically selects Local GGUF or OpenRouter Free based on RAM & user preference."""

    def __init__(self, model_path: Optional[str | Path] = None) -> None:
        self.local_engine = InProcessLlamaCppEngine(model_path=model_path)
        self.openrouter_engine = OpenRouterFreeEngine()

    @property
    def model_path(self) -> Path:
        return self.local_engine.model_path

    def active_mode_info(self) -> Dict[str, Any]:
        cfg = load_engine_config()
        mode = cfg.get("mode", "auto")
        ram_free_gb = psutil.virtual_memory().available / (1024**3)
        low_ram = ram_free_gb < LOW_RAM_THRESHOLD_GB
        or_ready = self.openrouter_engine.health()

        if mode == "openrouter" or (mode == "auto" and low_ram and or_ready):
            active = "openrouter_free"
            model_name = self.openrouter_engine._get_model()
        else:
            active = "weondevice_llamacpp"
            model_name = self.local_engine.model_path.name

        return {
            "mode": mode,
            "active_engine": active,
            "active_model": model_name,
            "low_ram_detected": low_ram,
            "ram_free_gb": round(ram_free_gb, 2),
            "openrouter_configured": or_ready,
            "openrouter_model": self.openrouter_engine._get_model(),
        }

    def health(self) -> bool:
        return self.local_engine.health() or self.openrouter_engine.health()

    def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str = "",
        temperature: float = 0.3,
        max_tokens: int = 512,
    ) -> Dict[str, Any]:
        info = self.active_mode_info()
        if info["active_engine"] == "openrouter_free":
            return self.openrouter_engine.generate(
                messages,
                model=info["openrouter_model"],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        try:
            return self.local_engine.generate(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception:
            if self.openrouter_engine.health():
                return self.openrouter_engine.generate(
                    messages,
                    model=info["openrouter_model"],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            raise
