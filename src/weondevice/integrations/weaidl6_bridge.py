"""Dynamic bridge and OpenJarvis Tool for happytalkman/weaidL6 when cloned locally."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


def discover_weaidl6_path() -> Path | None:
    """Locate weaidL6 if present locally (e.g. C:\\openjarvis\\third_party\\weaidL6)."""
    candidates = [
        Path(os.environ.get("WEAIDL6_PATH", "")) if os.environ.get("WEAIDL6_PATH") else None,
        Path("third_party/weaidL6"),
        Path("external/weaidL6"),
        Path("../weaidL6"),
    ]
    for c in candidates:
        if c and c.exists() and c.is_dir():
            return c.resolve()
    return None


class WeAidL6Bridge:
    """Discovers weaidL6 modules/scripts and exposes them to the OpenJarvis agent."""

    def __init__(self) -> None:
        self.root = discover_weaidl6_path()
        self.available = self.root is not None
        if self.root and str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))

    def status(self) -> Dict[str, Any]:
        if not self.root:
            return {"available": False, "path": None, "modules": []}
        py_files: List[str] = [
            str(p.relative_to(self.root))
            for p in self.root.rglob("*.py")
            if ".venv" not in p.parts and "__pycache__" not in p.parts
        ][:50]
        return {
            "available": True,
            "path": str(self.root),
            "modules": py_files,
        }

    def summary(self) -> str:
        if not self.root:
            return "weaidL6 저장소가 로컬(third_party/weaidL6)에 없습니다."
        st = self.status()
        readme = ""
        for name in ("README.md", "readme.md", "README.txt"):
            rp = self.root / name
            if rp.exists():
                readme = rp.read_text(encoding="utf-8", errors="replace")[:1500]
                break
        return json.dumps(
            {
                "path": st["path"],
                "modules": st["modules"][:20],
                "readme_excerpt": readme,
            },
            ensure_ascii=False,
            indent=2,
        )


@ToolRegistry.register("weaidl6_inspect")
class WeAidL6InspectTool(BaseTool):
    """OpenJarvis Tool to inspect and summarize local happytalkman/weaidL6 capabilities."""

    tool_id = "weaidl6_inspect"

    def __init__(self) -> None:
        self.bridge = WeAidL6Bridge()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="weaidl6_inspect",
            description="Inspect local happytalkman/weaidL6 modules, README, and entrypoints.",
            parameters={"type": "object", "properties": {}, "required": []},
            category="integration",
        )

    def execute(self, **params: Any) -> ToolResult:
        return ToolResult(
            tool_name=self.tool_id,
            content=self.bridge.summary(),
            success=self.bridge.available,
        )
