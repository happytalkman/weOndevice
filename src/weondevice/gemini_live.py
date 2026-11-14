"""Gemini Multimodal Live API bidirectional audio + local OpenJarvis tool bridge."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from weondevice.engine import load_engine_config

DEFAULT_GEMINI_LIVE_MODELS = [
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
    "models/gemini-2.5-flash-native-audio-preview-09-2025",
    "models/gemini-3.1-flash-live-preview",
]
DEFAULT_GEMINI_LIVE_MODEL = DEFAULT_GEMINI_LIVE_MODELS[0]
GEMINI_WS_BASE = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def _tool_declarations() -> List[Dict[str, Any]]:
    return [
        {
            "functionDeclarations": [
                {
                    "name": "file_finder",
                    "description": "Find files by extension or keyword on the user's local Windows/Linux computer (e.g. pdf, ppt, 사업계획서).",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "query": {
                                "type": "STRING",
                                "description": "Natural language file search query including folder/extension.",
                            }
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "ripgrep_search",
                    "description": "Search text or code inside local workspace files using happytalkman/ripgrep.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "query": {
                                "type": "STRING",
                                "description": "Text or regex pattern to search.",
                            }
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "system_status",
                    "description": "Check laptop CPU, RAM, disk, and battery status.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "scope": {
                                "type": "STRING",
                                "description": "Status scope such as all, cpu, ram, disk, battery.",
                            }
                        },
                    },
                },
            ]
        }
    ]


def _build_setup_msg(model: str) -> Dict[str, Any]:
    return {
        "setup": {
            "model": model,
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Aoede"}}
                },
            },
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are Jarvis-RG, a warm, natural Korean on-device personal voice assistant. "
                            "Always respond concisely in natural spoken Korean. "
                            "Use your local tools (file_finder, ripgrep_search, system_status) whenever the user asks about local files, code, or laptop status."
                        )
                    }
                ]
            },
            "tools": _tool_declarations(),
        }
    }


async def _connect_gemini_ws(uri: str):
    """Connect to Gemini Live WebSocket, bypassing broken Windows system proxies first."""
    connect_variants = [
        {"proxy": None, "compression": None, "open_timeout": 20, "max_size": 8 * 1024 * 1024},
        {"proxy": True, "compression": None, "open_timeout": 20, "max_size": 8 * 1024 * 1024},
    ]
    last_exc: Exception | None = None
    for kwargs in connect_variants:
        try:
            return await websockets.connect(uri, **kwargs)
        except Exception as exc:
            last_exc = exc
            # If server rejected with an HTTP status (e.g. 400/403/404), don't retry proxy
            if hasattr(exc, "response"):
                raise
    assert last_exc is not None
    raise last_exc


async def relay_gemini_live(client_ws: WebSocket, agent: Any) -> None:
    """Bridge browser WebSocket <-> Gemini Live API with local tool execution."""
    await client_ws.accept()
    cfg = load_engine_config()
    api_key = (os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key") or "").strip()
    configured_model = (cfg.get("gemini_live_model") or "").strip()

    if not api_key:
        await client_ws.send_json(
            {"type": "error", "message": "Gemini API 키가 설정되지 않았습니다. 사이드바에서 Gemini API 키를 저장해 주세요."}
        )
        await client_ws.close()
        return

    candidates = (
        [configured_model] + [m for m in DEFAULT_GEMINI_LIVE_MODELS if m != configured_model]
        if configured_model
        else list(DEFAULT_GEMINI_LIVE_MODELS)
    )
    uri = f"{GEMINI_WS_BASE}?key={api_key}"

    gemini_ws = None
    active_model = candidates[0]
    last_error = ""

    try:
        for candidate in candidates:
            ws = None
            try:
                ws = await _connect_gemini_ws(uri)
                await ws.send(json.dumps(_build_setup_msg(candidate)))
                raw_setup = await asyncio.wait_for(ws.recv(), timeout=15)
                setup_ack = json.loads(raw_setup)
                if "setupComplete" in setup_ack:
                    gemini_ws = ws
                    active_model = candidate
                    break
                # Unexpected first message, still accept if not error
                gemini_ws = ws
                active_model = candidate
                break
            except Exception as exc:
                last_error = str(exc)
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass
                # Stop trying other models if handshake/auth itself failed
                if "did not receive a valid HTTP response" in last_error or "403" in last_error or "401" in last_error:
                    break

        if gemini_ws is None:
            raise RuntimeError(last_error or "Gemini Live 연결에 실패했습니다.")

        await client_ws.send_json({"type": "ready", "model": active_model})

        async def client_to_gemini() -> None:
            try:
                while True:
                    raw = await client_ws.receive_text()
                    msg = json.loads(raw)
                    if msg.get("type") == "audio":
                        await gemini_ws.send(
                            json.dumps(
                                {
                                    "realtimeInput": {
                                        "mediaChunks": [
                                            {
                                                "mimeType": "audio/pcm;rate=16000",
                                                "data": msg["data"],
                                            }
                                        ]
                                    }
                                }
                            )
                        )
                    elif msg.get("type") == "text":
                        await gemini_ws.send(
                            json.dumps(
                                {
                                    "clientContent": {
                                        "turns": [
                                            {
                                                "role": "user",
                                                "parts": [{"text": msg["text"]}],
                                            }
                                        ],
                                        "turnComplete": True,
                                    }
                                }
                            )
                        )
            except WebSocketDisconnect:
                pass

        async def gemini_to_client() -> None:
            async for raw in gemini_ws:
                data = json.loads(raw)
                if "toolCall" in data:
                    calls = data["toolCall"].get("functionCalls", [])
                    responses = []
                    for fc in calls:
                        name = fc.get("name", "")
                        args = fc.get("args", {})
                        if name == "system_status":
                            out = agent.system_tool.execute().content
                        elif name == "ripgrep_search":
                            out = agent.search_tool.execute(query=args.get("query", "")).content[:1500]
                        elif name == "file_finder":
                            out, _ = agent._route_laptop_tools(args.get("query", ""))
                            out = out or "일치하는 파일을 찾지 못했습니다."
                        else:
                            out = "Unsupported tool"
                        responses.append(
                            {
                                "id": fc.get("id"),
                                "name": name,
                                "response": {"result": out},
                            }
                        )
                    await gemini_ws.send(
                        json.dumps({"toolResponse": {"functionResponses": responses}})
                    )
                    continue

                sc = data.get("serverContent")
                if sc:
                    if sc.get("interrupted"):
                        await client_ws.send_json({"type": "interrupted"})
                    model_turn = sc.get("modelTurn", {})
                    for part in model_turn.get("parts", []):
                        if "inlineData" in part:
                            await client_ws.send_json(
                                {
                                    "type": "audio",
                                    "data": part["inlineData"]["data"],
                                }
                            )
                        elif "text" in part:
                            await client_ws.send_json(
                                {"type": "text", "text": part["text"]}
                            )
                    if sc.get("turnComplete"):
                        await client_ws.send_json({"type": "turn_complete"})

        await asyncio.gather(client_to_gemini(), gemini_to_client())
    except Exception as exc:
        detail = str(exc)
        if hasattr(exc, "response") and exc.response is not None:
            detail = f"HTTP {exc.response.status_code}: {detail}"
        try:
            await client_ws.send_json({"type": "error", "message": detail})
        except Exception:
            pass
    finally:
        if gemini_ws is not None:
            try:
                await gemini_ws.close()
            except Exception:
                pass

