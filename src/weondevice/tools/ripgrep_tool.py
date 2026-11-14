"""RipgrepSearchTool & HybridLocalRetriever — happytalkman/ripgrep + OpenJarvis Tools & Memory.

Combines native Rust ripgrep crates (via PyO3 ``weondevice_rg`` or ``rg --json``
fallback) with BM25 lexical reranking and strict token-budgeted context packing
for bounded on-device LLM context windows.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec
from rank_bm25 import BM25Okapi

try:
    import weondevice_rg  # type: ignore[import-not-found]

    RUST_RG_AVAILABLE = True
except ImportError:
    weondevice_rg = None
    RUST_RG_AVAILABLE = False


def _find_rg_binary() -> Optional[str]:
    local_rg = Path("third_party/ripgrep/target/release/rg")
    if local_rg.exists():
        return str(local_rg)
    return shutil.which("rg")


def run_ripgrep_search(
    query: str,
    root: str = ".",
    *,
    case_insensitive: bool = True,
    max_results: int = 25,
    context_lines: int = 2,
    glob_pattern: Optional[str] = None,
    literal: bool = False,
) -> Dict[str, Any]:
    """Execute ultra-fast ripgrep search via native Rust PyO3 or rg --json fallback."""
    if RUST_RG_AVAILABLE:
        raw = weondevice_rg.search_json(
            query=query,
            root=root,
            case_insensitive=case_insensitive,
            max_results=max_results,
            context_lines=context_lines,
            glob_pattern=glob_pattern,
            literal=literal,
        )
        data = json.loads(raw)
        data["backend"] = "weondevice_rg (Rust PyO3 native)"
        return data

    rg_bin = _find_rg_binary()
    if not rg_bin:
        return {
            "query": query,
            "root": root,
            "elapsed_ms": 0.0,
            "files_scanned": 0,
            "total_matches": 0,
            "matches": [],
            "backend": "unavailable",
            "error": "Neither weondevice_rg nor rg binary is available.",
        }

    t0 = time.perf_counter()
    cmd = [rg_bin, "--json", "--max-count", str(max_results)]
    if case_insensitive:
        cmd.append("-i")
    if literal:
        cmd.append("-F")
    if context_lines > 0:
        cmd.extend(["-C", str(context_lines)])
    if glob_pattern:
        cmd.extend(["-g", glob_pattern])
    cmd.extend([query, root])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    matches: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "match":
            d = ev["data"]
            matches.append(
                {
                    "path": d["path"]["text"],
                    "line_number": d["line_number"],
                    "line": d["lines"]["text"].rstrip("\r\n"),
                    "context_before": [],
                    "context_after": [],
                }
            )
            if len(matches) >= max_results:
                break

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "query": query,
        "root": root,
        "elapsed_ms": round(elapsed_ms, 2),
        "files_scanned": len({m["path"] for m in matches}),
        "total_matches": len(matches),
        "matches": matches,
        "backend": f"rg subprocess ({rg_bin})",
    }


def run_ripgrep_files(
    root: str = ".",
    *,
    glob_pattern: Optional[str] = None,
    max_files: int = 300,
) -> List[Dict[str, Any]]:
    """List .gitignore-respected files using happytalkman/ripgrep's ignore crate."""
    if RUST_RG_AVAILABLE:
        raw = weondevice_rg.list_files_json(
            root=root,
            glob_pattern=glob_pattern,
            max_files=max_files,
        )
        return json.loads(raw)

    rg_bin = _find_rg_binary()
    if not rg_bin:
        return []
    cmd = [rg_bin, "--files"]
    if glob_pattern:
        cmd.extend(["-g", glob_pattern])
    cmd.append(root)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    files = []
    for line in proc.stdout.splitlines()[:max_files]:
        p = Path(line)
        files.append({"path": line, "size_bytes": p.stat().st_size if p.exists() else 0})
    return files


class HybridLocalRetriever:
    """Zero-vector Hybrid Retriever: Ripgrep candidate generation + BM25 reranking + Token Budgeter."""

    def __init__(self, root: str = ".", token_budget: int = 900) -> None:
        self.root = root
        self.token_budget = token_budget

    def retrieve(
        self,
        query: str,
        *,
        keywords: Optional[List[str]] = None,
        glob_pattern: Optional[str] = None,
        top_k: int = 6,
    ) -> Dict[str, Any]:
        terms = keywords or [t for t in query.replace("?", " ").replace(",", " ").split() if len(t) >= 2]
        if not terms:
            terms = [query]

        # Build regex OR pattern from extracted keywords for fast single-pass ripgrep scan
        import re as _re

        escaped = [_re.escape(t) for t in terms[:6]]
        pattern = "|".join(escaped)

        report = run_ripgrep_search(
            pattern,
            root=self.root,
            case_insensitive=True,
            max_results=40,
            context_lines=2,
            glob_pattern=glob_pattern,
            literal=False,
        )
        matches = report.get("matches", [])
        if not matches:
            return {
                "backend": report.get("backend", "unknown"),
                "elapsed_ms": report.get("elapsed_ms", 0.0),
                "files_scanned": report.get("files_scanned", 0),
                "total_matches": 0,
                "packed_context": "",
                "citations": [],
                "context_tokens_est": 0,
            }

        # Stage 2: BM25 reranking over ripgrep snippets
        docs_tokens = []
        snippets = []
        for m in matches:
            block = "\n".join(
                [*m.get("context_before", []), m["line"], *m.get("context_after", [])]
            )
            snippets.append((m, block))
            docs_tokens.append((m["path"] + " " + block).lower().split())

        bm25 = BM25Okapi(docs_tokens)
        scores = bm25.get_scores(query.lower().split())
        ranked = sorted(
            zip(scores, snippets),
            key=lambda x: x[0],
            reverse=True,
        )

        # Stage 3: Token-budgeted context packing (~4 chars per token)
        char_budget = self.token_budget * 4
        used_chars = 0
        packed_parts: List[str] = []
        citations: List[Dict[str, Any]] = []
        seen_keys = set()

        for score, (m, block) in ranked:
            key = (m["path"], m["line_number"])
            if key in seen_keys:
                continue
            seen_keys.add(key)

            entry = f"[{m['path']}:{m['line_number']}]\n{block}"
            if used_chars + len(entry) > char_budget and packed_parts:
                break
            packed_parts.append(entry)
            used_chars += len(entry)
            citations.append(
                {
                    "path": m["path"],
                    "line": m["line_number"],
                    "score": round(float(score), 3),
                    "preview": m["line"].strip()[:140],
                }
            )
            if len(citations) >= top_k:
                break

        packed_context = "\n---\n".join(packed_parts)
        return {
            "backend": report.get("backend", "unknown"),
            "elapsed_ms": report.get("elapsed_ms", 0.0),
            "files_scanned": report.get("files_scanned", 0),
            "total_matches": report.get("total_matches", 0),
            "packed_context": packed_context,
            "citations": citations,
            "context_tokens_est": max(1, len(packed_context) // 4) if packed_context else 0,
        }


@ToolRegistry.register("ripgrep_search")
class RipgrepSearchTool(BaseTool):
    """OpenJarvis Tool wrapping happytalkman/ripgrep for instant local search."""

    tool_id = "ripgrep_search"

    def __init__(self, default_root: str = ".") -> None:
        self.default_root = default_root

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ripgrep_search",
            description=(
                "Search local files, codebases, and notes at native Rust speed using"
                " happytalkman/ripgrep (respects .gitignore). Returns matching lines,"
                " line numbers, and surrounding context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Regex or text pattern to search."},
                    "root": {"type": "string", "description": "Directory path to search."},
                    "glob_pattern": {"type": "string", "description": "Optional glob filter e.g. '*.py'."},
                    "max_results": {"type": "integer", "description": "Max matches (default 15)."},
                },
                "required": ["query"],
            },
            category="knowledge",
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query", "")).strip()
        if not query:
            return ToolResult(tool_name=self.tool_id, content="Missing query.", success=False)
        root = str(params.get("root") or self.default_root)
        glob_pattern = params.get("glob_pattern")
        max_results = int(params.get("max_results") or 15)

        report = run_ripgrep_search(
            query,
            root=root,
            glob_pattern=glob_pattern,
            max_results=max_results,
        )
        return ToolResult(
            tool_name=self.tool_id,
            content=json.dumps(report, ensure_ascii=False, indent=2),
            success="error" not in report,
        )


@ToolRegistry.register("ripgrep_files")
class RipgrepFilesTool(BaseTool):
    """OpenJarvis Tool listing .gitignore-aware local files via ripgrep's ignore crate."""

    tool_id = "ripgrep_files"

    def __init__(self, default_root: str = ".") -> None:
        self.default_root = default_root

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ripgrep_files",
            description="List files in a local directory respecting .gitignore rules.",
            parameters={
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "Directory path."},
                    "glob_pattern": {"type": "string", "description": "Optional glob filter."},
                    "max_files": {"type": "integer", "description": "Maximum files to return."},
                },
                "required": [],
            },
            category="filesystem",
        )

    def execute(self, **params: Any) -> ToolResult:
        root = str(params.get("root") or self.default_root)
        glob_pattern = params.get("glob_pattern")
        max_files = int(params.get("max_files") or 100)
        files = run_ripgrep_files(root=root, glob_pattern=glob_pattern, max_files=max_files)
        return ToolResult(
            tool_name=self.tool_id,
            content=json.dumps(files, ensure_ascii=False, indent=2),
            success=True,
        )
