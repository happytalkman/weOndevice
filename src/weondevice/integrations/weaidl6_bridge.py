"""Dynamic bridge for happytalkman/weaidL6 when cloned on the user's laptop."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List


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
