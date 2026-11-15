"""Bridge to hugohe3/ppt-master for native editable PPTX generation in Jarvis-RG."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from xml.sax.saxutils import escape

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import Message, Role, ToolResult

try:
    from openjarvis.tools.base import BaseTool
except Exception:  # pragma: no cover
    class BaseTool:  # type: ignore[no-redef]
        name: str = ""
        description: str = ""
        parameters: Dict[str, Any] = {}


def _find_pptmaster_root(hint: Optional[str] = None) -> Optional[Path]:
    candidates = [
        Path(hint) if hint else None,
        Path.cwd() / "third_party" / "ppt-master",
        Path(__file__).resolve().parents[3] / "third_party" / "ppt-master",
        Path("C:/openjarvis/third_party/ppt-master"),
    ]
    for p in candidates:
        if p and (p / "skills" / "ppt-master" / "scripts" / "svg_to_pptx.py").exists():
            return p
    return None


def _default_outline(topic: str, num_slides: int = 5) -> Dict[str, Any]:
    clean_topic = re.sub(r"(ppt|피피티|발표자료|슬라이드|프레젠테이션).*", "", topic, flags=re.I).strip() or topic
    return {
        "title": clean_topic,
        "subtitle": "Jarvis-RG × PPT Master 자동 생성 프레젠테이션",
        "slides": [
            {
                "heading": "1. 핵심 개요 및 목적",
                "bullets": [
                    f"주제: {clean_topic}",
                    "온디바이스 AI(OpenJarvis) + Ripgrep 기반 로컬 자료 통합",
                    "실무에서 바로 편집 가능한 네이티브 PowerPoint 도형 구성",
                ],
                "kpi": "100% Editable",
                "note": f"{clean_topic}의 전체 개요와 핵심 목표를 소개합니다.",
            },
            {
                "heading": "2. 현황 및 주요 과제",
                "bullets": [
                    "기존 수작업 문서 정리 및 슬라이드 제작 시간 과다 소요",
                    "보안·프라이버시 민감 자료의 외부 클라우드 업로드 부담",
                    "실시간 음성 지시와 로컬 파일 검색의 유기적 연동 필요",
                ],
                "kpi": "3x Faster",
                "note": "현재 직면한 문제점과 자동화가 필요한 배경을 설명합니다.",
            },
            {
                "heading": "3. 제안 솔루션 및 아키텍처",
                "bullets": [
                    "음성·텍스트 자연어 명령으로 즉시 초안 및 슬라이드 구조화",
                    "SVG 벡터 레이아웃을 DrawingML 네이티브 도형으로 변환",
                    "발표자 노트(Speaker Notes) 및 화면 전환 효과 자동 포함",
                ],
                "kpi": "Native OOXML",
                "note": "Jarvis-RG와 PPT Master가 결합된 핵심 해결 방안을 안내합니다.",
            },
            {
                "heading": "4. 기대 효과 및 실행 로드맵",
                "bullets": [
                    "1단계: 로컬 자료 분석 및 핵심 메시지 구조화",
                    "2단계: 슬라이드 자동 생성 및 PowerPoint 세부 커스터마이징",
                    "3단계: 음성 내레이션 결합 및 최종 발표 리허설",
                ],
                "kpi": "Ready Now",
                "note": "단계별 실행 계획과 기대 성과를 정리하며 마무리합니다.",
            },
        ][: max(2, num_slides - 1)],
    }


def _generate_outline_with_llm(
    topic: str,
    engine: Any,
    num_slides: int = 5,
    source_text: Optional[str] = None,
) -> Dict[str, Any]:
    if engine is None:
        return _default_outline(topic, num_slides=num_slides)

    src_block = ""
    if source_text:
        src_block = f"\nUse this source material as the factual basis:\n---\n{source_text[:6000]}\n---\n"

    prompt = (
        f"Create a concise, executive Korean presentation outline for the topic: '{topic}'.\n"
        f"{src_block}"
        f"Return ONLY valid JSON (no markdown fences) with this exact schema:\n"
        "{\n"
        '  "title": "Deck main title",\n'
        '  "subtitle": "Short subtitle",\n'
        '  "slides": [\n'
        '    {"heading": "1. Section title", "bullets": ["point 1", "point 2", "point 3"], "kpi": "short badge", "note": "1-sentence Korean speaker note"}\n'
        "  ]\n"
        "}\n"
        f"Include {max(3, min(7, num_slides - 1))} content slides."
    )
    try:
        res = engine.generate(
            [
                Message(role=Role.SYSTEM, content="You are a presentation strategist. Output strict JSON only."),
                Message(role=Role.USER, content=prompt),
            ],
            temperature=0.3,
            max_tokens=1400,
        )
        raw = (res.get("content") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return _default_outline(topic, num_slides=num_slides)
        data = json.loads(m.group(0))
        if not isinstance(data.get("slides"), list) or not data["slides"]:
            return _default_outline(topic, num_slides=num_slides)
        return data
    except Exception:
        return _default_outline(topic, num_slides=num_slides)


def _wrap_text(text: str, max_chars: int = 34) -> List[str]:
    words = text.split()
    if not words:
        return [""]
    lines: List[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars:
            cur = f"{cur} {w}".strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:3]


def _render_cover_svg(title: str, subtitle: str) -> str:
    t_lines = _wrap_text(title, max_chars=22)
    t_svg = "\n".join(
        f'  <text x="110" y="{270 + i * 66}" font-family="Malgun Gothic, Segoe UI, sans-serif" '
        f'font-size="52" font-weight="700" fill="#F8FAFC">{escape(line)}</text>'
        for i, line in enumerate(t_lines)
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720">
  <rect width="1280" height="720" fill="#0B1220"/>
  <rect x="0" y="0" width="18" height="720" fill="#38BDF8"/>
  <rect x="110" y="135" width="230" height="36" rx="18" fill="#1E293B" stroke="#38BDF8" stroke-width="1.5"/>
  <text x="225" y="159" text-anchor="middle" font-family="Segoe UI, Malgun Gothic, sans-serif" font-size="16" font-weight="600" fill="#38BDF8">JARVIS-RG × PPT MASTER</text>
{t_svg}
  <text x="110" y="{310 + len(t_lines) * 66}" font-family="Malgun Gothic, Segoe UI, sans-serif" font-size="24" fill="#94A3B8">{escape(subtitle[:70])}</text>
  <rect x="110" y="610" width="1060" height="2" fill="#1E293B"/>
  <text x="110" y="650" font-family="Segoe UI, Malgun Gothic, sans-serif" font-size="16" fill="#64748B">Generated locally by OpenJarvis + hugohe3/ppt-master (Native Editable PPTX)</text>
</svg>
"""


def _render_content_svg(idx: int, total: int, slide: Dict[str, Any]) -> str:
    heading = str(slide.get("heading") or f"Slide {idx}")
    kpi = str(slide.get("kpi") or f"{idx}/{total}")[:24]
    bullets = [str(b) for b in (slide.get("bullets") or [])][:4]

    cards_svg = []
    y = 195
    for b_idx, bullet in enumerate(bullets):
        wrapped = _wrap_text(bullet, max_chars=44)
        card_h = 96 if len(wrapped) <= 2 else 116
        cards_svg.append(
            f'  <rect x="90" y="{y}" width="1100" height="{card_h}" rx="14" fill="#111C33" stroke="#1E293B" stroke-width="1.5"/>'
        )
        cards_svg.append(
            f'  <rect x="90" y="{y}" width="8" height="{card_h}" rx="4" fill="#38BDF8"/>'
        )
        cards_svg.append(
            f'  <text x="122" y="{y + 38}" font-family="Segoe UI, sans-serif" font-size="18" font-weight="700" fill="#38BDF8">0{b_idx + 1}</text>'
        )
        for l_idx, line in enumerate(wrapped):
            cards_svg.append(
                f'  <text x="172" y="{y + 40 + l_idx * 30}" font-family="Malgun Gothic, Segoe UI, sans-serif" font-size="22" fill="#E2E8F0">{escape(line)}</text>'
            )
        y += card_h + 18

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720">
  <rect width="1280" height="720" fill="#0B1220"/>
  <rect x="90" y="64" width="1100" height="84" rx="14" fill="#0F172A"/>
  <text x="115" y="116" font-family="Malgun Gothic, Segoe UI, sans-serif" font-size="32" font-weight="700" fill="#F8FAFC">{escape(heading[:46])}</text>
  <rect x="970" y="84" width="195" height="42" rx="21" fill="#1E293B" stroke="#818CF8" stroke-width="1.5"/>
  <text x="1067" y="111" text-anchor="middle" font-family="Segoe UI, Malgun Gothic, sans-serif" font-size="16" font-weight="600" fill="#A5B4FC">{escape(kpi)}</text>
{chr(10).join(cards_svg)}
  <text x="90" y="684" font-family="Segoe UI, Malgun Gothic, sans-serif" font-size="14" fill="#64748B">Jarvis-RG × PPT Master</text>
  <text x="1190" y="684" text-anchor="end" font-family="Segoe UI, sans-serif" font-size="14" fill="#64748B">{idx} / {total}</text>
</svg>
"""


class PPTMasterBridge:
    """Generates natively editable PPTX decks via hugohe3/ppt-master."""

    def __init__(self, workspace_root: str = ".") -> None:
        self.workspace_root = Path(workspace_root)
        self.repo_root = _find_pptmaster_root(str(self.workspace_root / "third_party" / "ppt-master"))
        self.exports_dir = self.workspace_root / ".weondevice" / "ppt_exports"
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def status(self) -> Dict[str, Any]:
        return {
            "installed": self.repo_root is not None,
            "repo_path": str(self.repo_root) if self.repo_root else None,
            "exports_dir": str(self.exports_dir),
        }

    def extract_source_text(self, file_path: Path) -> str:
        """Extract text/Markdown from a document using ppt-master source_to_md.py or direct read."""
        if self.repo_root is not None:
            s2m = self.repo_root / "skills" / "ppt-master" / "scripts" / "source_to_md.py"
            if s2m.exists():
                proc = subprocess.run(
                    [sys.executable, str(s2m), str(file_path)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                )
                if proc.returncode == 0 and (proc.stdout or "").strip():
                    return proc.stdout.strip()
        return file_path.read_text(encoding="utf-8", errors="replace")

    def generate_deck(
        self,
        topic: str,
        *,
        engine: Any = None,
        num_slides: int = 5,
        source_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        outline = _generate_outline_with_llm(
            topic,
            engine=engine,
            num_slides=num_slides,
            source_text=source_text,
        )
        ts = time.strftime("%Y%m%d_%H%M%S")
        slug = re.sub(r"[^A-Za-z0-9가-힣_-]+", "_", outline.get("title", "presentation")).strip("_")[:32] or "deck"
        project_dir = self.exports_dir / f"proj_{slug}_{ts}"
        svg_dir = project_dir / "svg_output"
        notes_dir = project_dir / "notes"
        svg_dir.mkdir(parents=True, exist_ok=True)
        notes_dir.mkdir(parents=True, exist_ok=True)

        slides = outline.get("slides", [])
        total = len(slides) + 1

        (svg_dir / "01_cover.svg").write_text(
            _render_cover_svg(outline.get("title", topic), outline.get("subtitle", "")),
            encoding="utf-8",
        )
        (notes_dir / "01_cover.md").write_text(
            f"# {outline.get('title', topic)}\n\n{outline.get('subtitle', '')}\n",
            encoding="utf-8",
        )

        for idx, s in enumerate(slides, start=2):
            stem = f"{idx:02d}_slide"
            (svg_dir / f"{stem}.svg").write_text(
                _render_content_svg(idx, total, s),
                encoding="utf-8",
            )
            (notes_dir / f"{stem}.md").write_text(
                f"# {s.get('heading', '')}\n\n{s.get('note', '')}\n",
                encoding="utf-8",
            )

        out_pptx = self.exports_dir / f"{slug}_{ts}.pptx"
        backend = "python-pptx fallback"

        if self.repo_root is not None:
            scripts_dir = self.repo_root / "skills" / "ppt-master" / "scripts"
            checker = scripts_dir / "svg_quality_checker.py"
            converter = scripts_dir / "svg_to_pptx.py"
            subprocess.run(
                [
                    sys.executable,
                    str(checker),
                    str(project_dir),
                    "--quick-generate",
                    "--canonical-authoring",
                    "--stage",
                    "final",
                    "--json",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            proc = subprocess.run(
                [
                    sys.executable,
                    str(converter),
                    str(project_dir),
                    "--quick-generate",
                    "--with-notes",
                    "--primary-language",
                    "ko",
                    "-o",
                    str(out_pptx),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if proc.returncode == 0 and out_pptx.exists():
                backend = "hugohe3/ppt-master (svg_to_pptx)"

        if not out_pptx.exists():
            from pptx import Presentation
            from pptx.util import Inches

            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)
            blank = prs.slide_layouts[6]
            s1 = prs.slides.add_slide(blank)
            tb = s1.shapes.add_textbox(Inches(1), Inches(2.2), Inches(11.3), Inches(2.5))
            tf = tb.text_frame
            tf.text = outline.get("title", topic)
            tf.add_paragraph().text = outline.get("subtitle", "")
            for s in slides:
                sl = prs.slides.add_slide(blank)
                b = sl.shapes.add_textbox(Inches(1), Inches(0.8), Inches(11.3), Inches(5.8)).text_frame
                b.text = s.get("heading", "")
                for item in s.get("bullets", []):
                    b.add_paragraph().text = f"• {item}"
            prs.save(str(out_pptx))

        return {
            "ok": True,
            "title": outline.get("title", topic),
            "filename": out_pptx.name,
            "pptx_path": str(out_pptx),
            "download_url": f"/api/ppt/download/{out_pptx.name}",
            "slide_count": total,
            "backend": backend,
        }


@ToolRegistry.register("pptmaster_generate")
class PPTMasterGenerateTool(BaseTool):
    """OpenJarvis BaseTool wrapper for hugohe3/ppt-master."""

    name = "pptmaster_generate"
    description = "Generate a natively editable PowerPoint (.pptx) presentation from a topic using hugohe3/ppt-master."
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Presentation topic or request."},
            "num_slides": {"type": "integer", "description": "Target number of slides (default 5)."},
        },
        "required": ["topic"],
    }

    def __init__(self, workspace_root: str = ".", engine: Any = None) -> None:
        self.bridge = PPTMasterBridge(workspace_root=workspace_root)
        self.engine = engine

    def execute(self, **kwargs: Any) -> ToolResult:
        topic = str(kwargs.get("topic", "")).strip()
        if not topic:
            return ToolResult(content="Error: 'topic' is required.", success=False)
        num_slides = int(kwargs.get("num_slides", 5))
        res = self.bridge.generate_deck(topic, engine=self.engine, num_slides=num_slides)
        summary = (
            f"PPT 생성 완료: {res['filename']} ({res['slide_count']}장, {res['backend']})\n"
            f"저장 위치: {res['pptx_path']}\n"
            f"다운로드: {res['download_url']}"
        )
        return ToolResult(content=summary, success=True, metadata=res)
