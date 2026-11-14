"""Gemini Multimodal Live API bidirectional audio + local OpenJarvis tool bridge."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from weondevice.engine import load_engine_config

DEFAULT_GEMINI_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
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
                            "query": {"type": "STRING", "description": "Natural language file search query including folder/extension."}
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
                            "query": {"type": "STRING", "description": "Text or regex pattern to search."}
                        },
                        "required": ["query"],
                    },
                },
                {
                    "name": "system_status",
                    "description": "Check laptop CPU, RAM, disk, and battery status.",
                    "parameters": {"type": "OBJECT", "properties": {}},
                },
            ]
        }
    ]


async def relay_gemini_live(client_ws: WebSocket, agent: Any) -> None:
    """Bridge browser WebSocket <-> Gemini Live API with local tool execution."""
    await client_ws.accept()
    cfg = load_engine_config()
    api_key = (os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key") or "").strip()
    model = (cfg.get("gemini_live_model") or DEFAULT_GEMINI_LIVE_MODEL).strip()

    if not api_key:
        await client_ws.send_json(
            {"type": "error", "message": "Gemini API 키가 설정되지 않았습니다. 사이드바에서 Gemini API 키를 저장해 주세요."}
        )
        await client_ws.close()
        return

    uri = f"{GEMINI_WS_BASE}?key={api_key}"
    try:
        async with websockets.connect(uri, max_size=8 * 1024 * 1024) as gemini_ws:
            setup_msg = {
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
            await gemini_ws.send(json.dumps(setup_msg))
            await client_ws.send_json({"type": "ready", "model": model})

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
        try:
            await client_ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
