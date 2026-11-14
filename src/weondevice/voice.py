"""On-device Voice AI helpers (optional faster-whisper STT + Web Speech API seamless fallback)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict


def stt_status() -> Dict[str, Any]:
    """Report available local STT/TTS capabilities."""
    has_whisper = False
    try:
        import faster_whisper  # type: ignore[import-not-found]  # noqa: F401

        has_whisper = True
    except ImportError:
        pass
    return {
        "local_whisper_available": has_whisper,
        "browser_web_speech_supported": True,
    }


def transcribe_audio_bytes(audio_bytes: bytes, suffix: str = ".webm", language: str = "ko") -> Dict[str, Any]:
    """Transcribe uploaded audio locally using faster-whisper if installed."""
    try:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]
    except ImportError:
        return {
            "ok": False,
            "text": "",
            "error": "faster-whisper is not installed; using browser Web Speech API.",
        }

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)

    try:
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(tmp_path), language=language)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {
            "ok": True,
            "text": text,
            "language": getattr(info, "language", language),
        }
    finally:
        tmp_path.unlink(missing_ok=True)
