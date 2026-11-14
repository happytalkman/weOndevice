"""WeOnDeviceAgent — Stanford OpenJarvis Primitives + happytalkman/ripgrep Hybrid Agent."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import Message, Role

from weondevice.engine import HybridOnDeviceEngine
from weondevice.integrations.pptmaster_bridge import PPTMasterGenerateTool
from weondevice.integrations.weaidl6_bridge import WeAidL6Bridge, WeAidL6InspectTool
from weondevice.telemetry import IPWMonitor, QueryTelemetry
from weondevice.tools.laptop_tools import (
    LocalFileReadTool,
    QuickNoteTool,
    SystemStatusTool,
)
from weondevice.tools.ripgrep_tool import (
    HybridLocalRetriever,
    RipgrepFilesTool,
    RipgrepSearchTool,
)

# Register OpenJarvis built-in tools when available
try:
    import openjarvis.tools.calculator  # noqa: F401
    import openjarvis.tools.think  # noqa: F401
except Exception:
    pass


SYSTEM_PROMPT = """\
You are J.A.R.V.I.S. (자비스), the user's witty, warm, and deeply capable personal AI assistant powered by Stanford OpenJarvis and happytalkman/ripgrep.
Rules:
1. Answer in the user's language (always respond in natural, engaging, conversational Korean with subtle wit and warmth when the user speaks or writes Korean).
2. Ground your answer strictly in the provided [LOCAL EVIDENCE] or [TOOL OUTPUT] when available.
3. Cite exact file paths and line numbers like `path/to/file:123` only when referencing code or notes.
4. Keep answers snappy (2-4 sentences) and natural for voice playback (TTS) — never output internal planning, English meta-commentary, or markdown symbols, and feel free to ask a thoughtful follow-up question to keep the conversation lively.
"""


@dataclass
class AgentTurnResult:
    answer: str
    citations: List[Dict[str, Any]]
    telemetry: QueryTelemetry
    backend: str
    packed_context: str


def _extract_search_keywords(query: str) -> List[str]:
    """Extract high-signal search tokens (identifiers, paths, Korean/English keywords)."""
    stopwords = {
        "the", "and", "for", "with", "what", "how", "where", "when", "why", "this", "that",
        "from", "into", "about", "find", "search", "show", "tell", "explain", "code", "file",
        "있는", "어디", "무엇", "어떻게", "설명", "알려줘", "찾아줘", "찾아저", "검색", "코드", "파일",
        "에서", "으로", "하고", "하는", "대한", "대해", "관련", "보여줘", "확인",
    }
    raw_tokens = re.findall(r"[A-Za-z0-9_./-]+|[가-힣]{2,}", query)
    keywords = [t for t in raw_tokens if len(t) >= 2 and t.lower() not in stopwords]
    return keywords[:6] if keywords else raw_tokens[:4]


def _resolve_search_root(query: str, default_root: str) -> str:
    """Extract Windows/Linux path from query (stripping Korean particles like '에서') or use home."""
    from pathlib import Path

    q_low = query.lower()
    if "바탕화면" in q_low or "desktop" in q_low:
        p = Path.home() / "Desktop"
        if p.exists():
            return str(p)
    if "다운로드" in q_low or "downloads" in q_low:
        p = Path.home() / "Downloads"
        if p.exists():
            return str(p)
    if "내 문서" in q_low or "문서폴더" in q_low:
        p = Path.home() / "Documents"
        if p.exists():
            return str(p)

    drive_match = re.search(r"([a-zA-Z]:\\(?:[A-Za-z0-9_.\-\\/ ]+)?)", query)
    if drive_match:
        raw = drive_match.group(1).strip()
        raw = re.sub(r"(에서|에|의|안의|폴더)$", "", raw).strip()
        if Path(raw).exists():
            return raw

    home = Path.home()
    return str(home) if home.exists() else default_root


class WeOnDeviceAgent:
    """Composable on-device agent uniting OpenJarvis primitives with ripgrep search & voice tools."""

    def __init__(
        self,
        workspace_root: str = ".",
        model_path: Optional[str] = None,
        token_budget: int = 850,
    ) -> None:
        self.workspace_root = workspace_root
        self.engine = HybridOnDeviceEngine(model_path=model_path)
        self.retriever = HybridLocalRetriever(root=workspace_root, token_budget=token_budget)
        self.monitor = IPWMonitor()
        self.weaidl6 = WeAidL6Bridge()

        # Tools registered in OpenJarvis ToolRegistry
        self.search_tool = RipgrepSearchTool(default_root=workspace_root)
        self.files_tool = RipgrepFilesTool(default_root=workspace_root)
        self.system_tool = SystemStatusTool()
        self.file_read_tool = LocalFileReadTool(workspace_root=workspace_root)
        self.note_tool = QuickNoteTool()
        self.weaidl6_tool = WeAidL6InspectTool()
        self.ppt_tool = PPTMasterGenerateTool(workspace_root=workspace_root, engine=self.engine)
        self.history: List[Message] = []

    def _route_laptop_tools(self, query: str) -> tuple[str, List[str]]:
        """Deterministic natural-language intent routing for fast, reliable on-device tool use."""
        import os
        from pathlib import Path

        q_low = query.lower()
        outputs: List[str] = []
        used: List[str] = []

        if any(k in q_low for k in ("ppt", "피피티", "발표자료", "슬라이드", "프레젠테이션")) and any(
            v in q_low for v in ("만들", "생성", "제작", "작성", "짜줘", "해줘", "create", "make")
        ):
            outputs.append(f"[pptmaster_generate]\n{self.ppt_tool.execute(topic=query).content}")
            used.append("pptmaster_generate")

        if "weaidl6" in q_low:
            res = self.weaidl6_tool.execute()
            outputs.append(f"[weaidl6_inspect]\n{res.content}")
            used.append("weaidl6_inspect")

        if any(k in q_low for k in ("배터리", "메모리", "ram", "cpu", "디스크", "시스템 상태", "노트북 상태", "battery", "system status")):
            res = self.system_tool.execute()
            outputs.append(f"[system_status]\n{res.content}")
            used.append("system_status")

        if any(k in q_low for k in ("메모 적어", "메모 저장", "노트 저장", "기억해", "save note", "add note")):
            cleaned = re.sub(r"^(메모\s*적어줘|메모\s*저장해줘|기억해줘|메모\s*적어|save note|add note)[:\s]*", "", query.strip(), flags=re.I)
            res = self.note_tool.execute(action="add", text=cleaned or query)
            outputs.append(f"[quick_note]\n{res.content}")
            used.append("quick_note")
        elif any(k in q_low for k in ("메모 보여", "메모 목록", "노트 보여", "저장된 메모", "list notes", "show notes")):
            res = self.note_tool.execute(action="list")
            outputs.append(f"[quick_note]\n{res.content}")
            used.append("quick_note")

        ext_match = re.search(r"\b(ppt|pptx|pdf|docx?|xlsx?|hwp|csv|txt|md|py|rs)\b", q_low)
        is_file_query = any(k in q_low for k in ("찾아", "검색", "어디", "목록", "문서", "파일", "find", "list", "search"))
        skip_dirs = {"Windows", "Program Files", "Program Files (x86)", "ProgramData", "$Recycle.Bin", "AppData", ".git", ".venv", "node_modules"}

        if ext_match and is_file_query:
            ext = ext_match.group(1)
            exts = (".ppt", ".pptx") if ext in ("ppt", "pptx") else (f".{ext}",)
            search_root = _resolve_search_root(query, self.workspace_root)
            found: List[str] = []
            for dirpath, dirnames, filenames in os.walk(search_root, onerror=lambda _: None):
                dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
                for fn in filenames:
                    if fn.lower().endswith(exts):
                        found.append(str(Path(dirpath) / fn))
                        if len(found) >= 30:
                            break
                if len(found) >= 30:
                    break
            outputs.append(
                f"[file_finder ({search_root}, {exts})]\n"
                + ("\n".join(found) if found else f"No matching {exts} files found in {search_root}.")
            )
            used.append("file_finder")
        elif is_file_query and any(k in q_low for k in ("문서", "파일", "찾아줘", "찾아저")):
            kw = [t for t in _extract_search_keywords(query) if t not in ("문서", "파일")]
            if kw:
                search_root = _resolve_search_root(query, self.workspace_root)
                found = []
                for dirpath, dirnames, filenames in os.walk(search_root, onerror=lambda _: None):
                    dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
                    for fn in filenames:
                        if any(k.lower() in fn.lower() for k in kw):
                            found.append(str(Path(dirpath) / fn))
                            if len(found) >= 30:
                                break
                    if len(found) >= 30:
                        break
                outputs.append(
                    f"[file_finder ({search_root}, keywords={kw})]\n"
                    + ("\n".join(found) if found else f"'{', '.join(kw)}' 관련 파일을 {search_root}에서 찾지 못했습니다.")
                )
                used.append("file_finder")

        return "\n\n".join(outputs), used

    def ask(
        self,
        query: str,
        *,
        glob_pattern: Optional[str] = None,
        use_rag: bool = True,
        max_tokens: int = 384,
        temperature: float = 0.2,
    ) -> AgentTurnResult:
        trace_id = uuid.uuid4().hex[:12]
        span = self.monitor.start_span()

        tool_output, tools_used = self._route_laptop_tools(query)

        retrieval: Dict[str, Any] = {
            "backend": "none",
            "elapsed_ms": 0.0,
            "files_scanned": 0,
            "total_matches": 0,
            "packed_context": "",
            "citations": [],
            "context_tokens_est": 0,
        }

        if use_rag and not tools_used:
            keywords = _extract_search_keywords(query)
            retrieval = self.retriever.retrieve(
                query,
                keywords=keywords,
                glob_pattern=glob_pattern,
                top_k=5,
            )
            tools_used.append("ripgrep_search")

        sections: List[str] = []
        if tool_output:
            sections.append(f"[TOOL OUTPUT]\n{tool_output}")
        packed_context = retrieval["packed_context"]
        if packed_context:
            sections.append(
                f"[LOCAL EVIDENCE (ripgrep {retrieval['elapsed_ms']:.1f}ms, "
                f"{retrieval['files_scanned']} files scanned)]\n{packed_context}"
            )
        sections.append(f"[USER QUESTION]\n{query}")
        user_content = "\n\n".join(sections)

        messages = [
            Message(role=Role.SYSTEM, content=SYSTEM_PROMPT),
            *self.history[-10:],
            Message(role=Role.USER, content=user_content),
        ]

        try:
            gen = self.engine.generate(
                messages,
                model=self.engine.model_path.name,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            answer_text = (gen.get("content") or "").strip()
            if not answer_text:
                answer_text = tool_output or packed_context or "응답을 생성하지 못했습니다. 다시 시도해 주세요."
            usage = gen.get("usage", {})
            self.history.append(Message(role=Role.USER, content=query))
            self.history.append(Message(role=Role.ASSISTANT, content=answer_text))
            if len(self.history) > 20:
                self.history = self.history[-20:]
        except Exception as exc:
            fallback_body = tool_output or packed_context or "로컬 검색 결과가 없습니다."
            answer_text = f"{fallback_body}\n\n(참고: 로컬 LLM 추론 생략 - {exc})"
            gen = {"model": self.engine.model_path.name}
            usage = {}

        prompt_tokens = int(usage.get("prompt_tokens", max(1, len(user_content) // 4)))
        completion_tokens = int(usage.get("completion_tokens", max(1, len(answer_text) // 4)))

        telemetry = self.monitor.finish_span(
            span,
            trace_id=trace_id,
            query=query,
            model=gen.get("model", self.engine.model_path.name),
            engine=gen.get("engine", "hybrid"),
            search_ms=float(retrieval["elapsed_ms"]),
            files_scanned=int(retrieval["files_scanned"]),
            matches_found=int(retrieval["total_matches"]),
            context_tokens_est=int(retrieval["context_tokens_est"]),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tools_used=tools_used,
        )

        return AgentTurnResult(
            answer=answer_text,
            citations=retrieval["citations"],
            telemetry=telemetry,
            backend=retrieval["backend"],
            packed_context=packed_context,
        )
