"""OpenJarvis-compatible on-device inference engine (in-process llama.cpp + Ollama/OpenAI-compat fallback)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        if self.model_path.exists():
            return [self.model_path.name]
        return []

    def health(self) -> bool:
        return self.model_path.exists()

    def close(self) -> None:
        self._llm = None
