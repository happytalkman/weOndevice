"""CLI entrypoint for weOndevice (OpenJarvis + happytalkman/ripgrep)."""

from __future__ import annotations

import json
import os
from typing import Optional

import click
import uvicorn
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from weondevice.agent import WeOnDeviceAgent
from weondevice.tools.ripgrep_tool import run_ripgrep_search

console = Console()


@click.group()
def main() -> None:
    """weOndevice — 나만의 온디바이스 AI (OpenJarvis + happytalkman/ripgrep)."""


@main.command()
@click.argument("query")
@click.option("--root", default=".", help="Workspace root to search.")
@click.option("--glob", "glob_pattern", default=None, help="Optional glob filter, e.g. '*.py'.")
@click.option("--no-rag", is_flag=True, help="Disable ripgrep RAG context.")
def ask(query: str, root: str, glob_pattern: Optional[str], no_rag: bool) -> None:
    """Ask the on-device AI agent a question grounded in local files."""
    agent = WeOnDeviceAgent(workspace_root=root)
    with console.status("[bold cyan]Thinking locally on-device..."):
        res = agent.ask(query, glob_pattern=glob_pattern, use_rag=not no_rag)

    console.print(Panel(Markdown(res.answer), title="Jarvis-RG (On-Device)", border_style="cyan"))

    if res.citations:
        table = Table(title=f"Ripgrep Citations ({res.backend})", show_lines=False)
        table.add_column("File:Line", style="green")
        table.add_column("BM25", justify="right", style="magenta")
        table.add_column("Preview", style="dim")
        for c in res.citations:
            table.add_row(f"{c['path']}:{c['line']}", str(c["score"]), c["preview"])
        console.print(table)

    t = res.telemetry
    console.print(
        f"[dim]IPW Telemetry | Search: {t.search_ms}ms ({t.files_scanned} files) | "
        f"Inference: {t.inference_ms}ms ({t.tokens_per_sec} tok/s) | "
        f"Power: ~{t.estimated_watts}W ({t.energy_joules}J) | "
        f"Cloud Saved: ${t.cloud_cost_saved_usd:.6f}[/dim]"
    )


@main.command()
@click.argument("query")
@click.option("--root", default=".", help="Directory to search.")
@click.option("--glob", "glob_pattern", default=None, help="Optional glob filter.")
@click.option("--max-results", default=15, help="Max matches.")
def search(query: str, root: str, glob_pattern: Optional[str], max_results: int) -> None:
    """Run ultra-fast native Rust ripgrep search."""
    report = run_ripgrep_search(query, root=root, glob_pattern=glob_pattern, max_results=max_results)
    console.print_json(json.dumps(report, ensure_ascii=False))


@main.command()
@click.option("--host", default="0.0.0.0", help="Bind host.")
@click.option("--port", default=8090, help="Bind port.")
@click.option("--root", default=".", help="Workspace root.")
def serve(host: str, port: int, root: str) -> None:
    """Launch the Jarvis-RG On-Device AI Web HUD & API server."""
    os.environ["WEONDEVICE_ROOT"] = root
    console.print(f"[bold green]Starting weOndevice HUD at http://localhost:{port}[/bold green]")
    uvicorn.run("weondevice.server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
