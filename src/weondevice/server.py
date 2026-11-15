"""FastAPI local-first server and Jarvis-RG HUD for weOndevice."""

from __future__ import annotations

import os
import platform
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

import psutil
from fastapi import FastAPI, File, UploadFile, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from weondevice.agent import WeOnDeviceAgent
from weondevice.engine import load_engine_config, save_engine_config
from weondevice.gemini_live import (
    DEFAULT_GEMINI_LIVE_MODELS,
    GEMINI_WS_BASE,
    _build_setup_msg,
    _discover_live_models,
    relay_gemini_live,
)
from weondevice.tools.ripgrep_tool import (
    RUST_RG_AVAILABLE,
    run_ripgrep_files,
    run_ripgrep_search,
)
from weondevice.voice import stt_status, transcribe_audio_bytes

STATIC_DIR = Path(__file__).parent / "static"
WORKSPACE_ROOT = os.environ.get("WEONDEVICE_ROOT", ".")

app = FastAPI(title="weOndevice — Jarvis-RG On-Device Voice AI", version="0.3.0")
_agent: Optional[WeOnDeviceAgent] = None


def get_agent() -> WeOnDeviceAgent:
    global _agent
    if _agent is None:
        _agent = WeOnDeviceAgent(workspace_root=WORKSPACE_ROOT)
    return _agent


class ChatRequest(BaseModel):
    query: str
    glob_pattern: Optional[str] = None
    use_rag: bool = True
    max_tokens: int = 384
    temperature: float = 0.2


class SearchRequest(BaseModel):
    query: str
    root: Optional[str] = None
    glob_pattern: Optional[str] = None
    max_results: int = 20
    context_lines: int = 2
    case_insensitive: bool = True


class FeedbackRequest(BaseModel):
    trace_id: str
    feedback: str  # "up" or "down"


class OpenRouterConfigRequest(BaseModel):
    openrouter_api_key: Optional[str] = None
    openrouter_model: Optional[str] = None
    mode: Optional[str] = None  # "auto" | "openrouter" | "local"


class GeminiConfigRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    gemini_live_model: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health")
def health() -> Dict[str, Any]:
    agent = get_agent()
    vm = psutil.virtual_memory()
    engine_info = agent.engine.active_mode_info()
    return {
        "status": "ok",
        "rust_ripgrep_native": RUST_RG_AVAILABLE,
        "model": engine_info["active_model"],
        "model_ready": agent.engine.health(),
        "engine_info": engine_info,
        "voice": stt_status(),
        "weaidl6": agent.weaidl6.status(),
        "workspace_root": os.path.abspath(agent.workspace_root),
        "system": {
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_available_gb": round(vm.available / (1024**3), 2),
        },
    }


@app.post("/api/config/openrouter")
def config_openrouter(req: OpenRouterConfigRequest) -> Dict[str, Any]:
    save_engine_config(req.model_dump())
    return {"ok": True, "engine_info": get_agent().engine.active_mode_info()}


@app.post("/api/config/gemini")
def config_gemini(req: GeminiConfigRequest) -> Dict[str, Any]:
    save_engine_config(req.model_dump())
    return {"ok": True}


class GeminiToolRequest(BaseModel):
    name: str
    args: Dict[str, Any] = {}


@app.get("/api/gemini-live-session")
async def gemini_live_session() -> Dict[str, Any]:
    cfg = load_engine_config()
    api_key = (os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key") or "").strip()
    if not api_key:
        return {"error": "Gemini API 키가 설정되지 않았습니다. 사이드바에서 Gemini API 키를 저장해 주세요."}
    configured = (cfg.get("gemini_live_model") or "").strip()
    discovered = await _discover_live_models(api_key)
    models = []
    for m in ([configured] if configured else []) + discovered + DEFAULT_GEMINI_LIVE_MODELS:
        if m and m not in models:
            models.append(m)
    return {
        "ws_base": GEMINI_WS_BASE,
        "api_key": api_key,
        "models": models,
        "setup_template": _build_setup_msg(models[0], include_tools=True),
    }


class PPTGenerateRequest(BaseModel):
    topic: str
    num_slides: int = 5


@app.post("/api/ppt/generate")
def ppt_generate(req: PPTGenerateRequest) -> Dict[str, Any]:
    agent = get_agent()
    res = agent.ppt_tool.bridge.generate_deck(
        req.topic,
        engine=agent.engine,
        num_slides=req.num_slides,
    )
    return res


@app.get("/api/ppt/list")
def ppt_list() -> Dict[str, Any]:
    exports_dir = get_agent().ppt_tool.bridge.exports_dir
    files = sorted(exports_dir.glob("*.pptx"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    return {
        "files": [
            {
                "filename": p.name,
                "size_kb": round(p.stat().st_size / 1024, 1),
                "download_url": f"/api/ppt/download/{p.name}",
            }
            for p in files
        ]
    }


@app.get("/api/ppt/download/{filename}")
def ppt_download(filename: str) -> Any:
    from urllib.parse import quote
    from fastapi.responses import FileResponse

    safe_name = Path(filename).name
    target = get_agent().ppt_tool.bridge.exports_dir / safe_name
    if not target.exists():
        return JSONResponse(status_code=404, content={"error": "파일을 찾을 수 없습니다."})
    encoded = quote(safe_name)
    return FileResponse(
        path=str(target),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={
            "Content-Disposition": f"attachment; filename=\"presentation.pptx\"; filename*=UTF-8''{encoded}",
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/gemini-tool")
def gemini_tool(req: GeminiToolRequest) -> Dict[str, Any]:
    agent = get_agent()
    name = req.name
    args = req.args or {}
    if name == "system_status":
        out = agent.system_tool.execute().content
    elif name == "ripgrep_search":
        out = agent.search_tool.execute(query=args.get("query", "")).content[:1500]
    elif name == "file_finder":
        out, _ = agent._route_laptop_tools(args.get("query", ""))
        out = out or "일치하는 파일을 찾지 못했습니다."
    elif name == "pptmaster_generate":
        out = agent.ppt_tool.execute(topic=args.get("topic", "프레젠테이션")).content
    else:
        out = "Unsupported tool"
    return {"result": out}


@app.websocket("/ws/gemini-live")
async def ws_gemini_live(websocket: WebSocket) -> None:
    await relay_gemini_live(websocket, get_agent())


@app.post("/api/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...), language: str = "ko") -> Dict[str, Any]:
    data = await file.read()
    suffix = Path(file.filename or "audio.webm").suffix or ".webm"
    return transcribe_audio_bytes(data, suffix=suffix, language=language)


@app.post("/api/chat")
def chat(req: ChatRequest) -> Any:
    try:
        agent = get_agent()
        res = agent.ask(
            req.query,
            glob_pattern=req.glob_pattern,
            use_rag=req.use_rag,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
        )
        return {
            "answer": res.answer,
            "citations": res.citations,
            "backend": res.backend,
            "telemetry": asdict(res.telemetry),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/api/search")
def search(req: SearchRequest) -> Dict[str, Any]:
    return run_ripgrep_search(
        query=req.query,
        root=req.root or WORKSPACE_ROOT,
        case_insensitive=req.case_insensitive,
        max_results=req.max_results,
        context_lines=req.context_lines,
        glob_pattern=req.glob_pattern,
    )


@app.get("/api/files")
def list_files(glob_pattern: Optional[str] = None, max_files: int = 100) -> Dict[str, Any]:
    files = run_ripgrep_files(root=WORKSPACE_ROOT, glob_pattern=glob_pattern, max_files=max_files)
    return {"count": len(files), "files": files}


@app.get("/api/telemetry")
def telemetry() -> Dict[str, Any]:
    return get_agent().monitor.summary()


@app.post("/api/feedback")
def feedback(req: FeedbackRequest) -> Dict[str, Any]:
    ok = get_agent().monitor.record_feedback(req.trace_id, req.feedback)
    return {"updated": ok}
