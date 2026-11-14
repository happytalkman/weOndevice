"""Tests for weOndevice (happytalkman/ripgrep + OpenJarvis integration)."""

from weondevice.tools.ripgrep_tool import (
    HybridLocalRetriever,
    RipgrepFilesTool,
    RipgrepSearchTool,
    run_ripgrep_search,
)


def test_ripgrep_search_finds_symbol():
    res = run_ripgrep_search("HybridLocalRetriever", root="src", max_results=5)
    assert res["total_matches"] >= 1
    assert any("ripgrep_tool.py" in m["path"] for m in res["matches"])


def test_openjarvis_tool_registry():
    tool = RipgrepSearchTool(default_root="src")
    out = tool.execute(query="IPWMonitor", max_results=3)
    assert out.success
    assert "telemetry.py" in out.content

    files_tool = RipgrepFilesTool(default_root="src")
    files_out = files_tool.execute(glob_pattern="*.py")
    assert files_out.success
    assert "agent.py" in files_out.content


def test_hybrid_retriever_token_budget():
    retriever = HybridLocalRetriever(root="src", token_budget=250)
    res = retriever.retrieve("OpenJarvis ToolRegistry ripgrep")
    assert res["total_matches"] >= 1
    assert res["context_tokens_est"] <= 300
    assert len(res["citations"]) >= 1
