"""MCP (Model Context Protocol) server exposing happytalkman/ripgrep & OpenJarvis tools."""

from __future__ import annotations

import json
import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from weondevice.tools.ripgrep_tool import run_ripgrep_files, run_ripgrep_search

mcp = FastMCP("weondevice-jarvis-rg")
DEFAULT_ROOT = os.environ.get("WEONDEVICE_ROOT", ".")


@mcp.tool()
def ripgrep_search(
    query: str,
    root: str = DEFAULT_ROOT,
    glob_pattern: Optional[str] = None,
    max_results: int = 20,
) -> str:
    """Search local files & codebases using happytalkman/ripgrep (respects .gitignore)."""
    return json.dumps(
        run_ripgrep_search(
            query=query,
            root=root,
            glob_pattern=glob_pattern,
            max_results=max_results,
        ),
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def ripgrep_files(
    root: str = DEFAULT_ROOT,
    glob_pattern: Optional[str] = None,
    max_files: int = 200,
) -> str:
    """List local files respecting .gitignore using ripgrep's ignore crate."""
    return json.dumps(
        run_ripgrep_files(root=root, glob_pattern=glob_pattern, max_files=max_files),
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    mcp.run()
