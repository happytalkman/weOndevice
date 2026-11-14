"""On-device laptop assistant tools registered in OpenJarvis ToolRegistry."""

from __future__ import annotations

import json
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("system_status")
class SystemStatusTool(BaseTool):
    """Report laptop CPU, RAM, disk, and battery status."""

    tool_id = "system_status"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="system_status",
            description="Get current laptop CPU, memory, disk, and battery status.",
            parameters={"type": "object", "properties": {}, "required": []},
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(".")
        try:
            battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
        except Exception:
            battery = None
        info = {
            "os": platform.platform(),
            "cpu_percent": psutil.cpu_percent(interval=0.1),
            "cpu_cores": os.cpu_count(),
            "ram_used_gb": round(vm.used / (1024**3), 2),
            "ram_total_gb": round(vm.total / (1024**3), 2),
            "ram_percent": vm.percent,
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "battery_percent": round(battery.percent, 1) if battery else None,
            "power_plugged": battery.power_plugged if battery else None,
        }
        return ToolResult(
            tool_name=self.tool_id,
            content=json.dumps(info, ensure_ascii=False, indent=2),
            success=True,
        )


@ToolRegistry.register("local_file_read")
class LocalFileReadTool(BaseTool):
    """Safely read a snippet of a local text file within workspace."""

    tool_id = "local_file_read"

    def __init__(self, workspace_root: str = ".") -> None:
        self.workspace_root = Path(workspace_root).resolve()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="local_file_read",
            description="Read lines from a local file inside the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative or absolute path."},
                    "start_line": {"type": "integer", "description": "1-based start line."},
                    "max_lines": {"type": "integer", "description": "Max lines to read."},
                },
                "required": ["path"],
            },
            category="filesystem",
        )

    def execute(self, **params: Any) -> ToolResult:
        raw_path = str(params.get("path", "")).strip()
        if not raw_path:
            return ToolResult(tool_name=self.tool_id, content="Missing path.", success=False)
        target = (self.workspace_root / raw_path).resolve()
        if not target.exists() or not target.is_file():
            return ToolResult(tool_name=self.tool_id, content=f"File not found: {raw_path}", success=False)
        start = max(1, int(params.get("start_line") or 1))
        max_lines = min(200, max(1, int(params.get("max_lines") or 80)))
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        snippet = "\n".join(
            f"{i}: {line}" for i, line in enumerate(lines[start - 1 : start - 1 + max_lines], start=start)
        )
        return ToolResult(tool_name=self.tool_id, content=snippet, success=True)


@ToolRegistry.register("quick_note")
class QuickNoteTool(BaseTool):
    """Save or list personal voice notes locally in .weondevice/notes.md."""

    tool_id = "quick_note"

    def __init__(self, notes_file: Path = Path(".weondevice/notes.md")) -> None:
        self.notes_file = notes_file

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="quick_note",
            description="Save a quick personal note or read recent notes locally.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'add' or 'list'"},
                    "text": {"type": "string", "description": "Note content when action='add'"},
                },
                "required": ["action"],
            },
            category="memory",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action", "list")).lower()
        self.notes_file.parent.mkdir(parents=True, exist_ok=True)
        if action == "add":
            text = str(params.get("text", "")).strip()
            if not text:
                return ToolResult(tool_name=self.tool_id, content="Empty note.", success=False)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            with self.notes_file.open("a", encoding="utf-8") as f:
                f.write(f"- [{ts}] {text}\n")
            return ToolResult(tool_name=self.tool_id, content=f"메모 저장 완료: {text}", success=True)

        if not self.notes_file.exists():
            return ToolResult(tool_name=self.tool_id, content="저장된 메모가 없습니다.", success=True)
        lines = self.notes_file.read_text(encoding="utf-8").splitlines()[-15:]
        return ToolResult(tool_name=self.tool_id, content="\n".join(lines), success=True)
