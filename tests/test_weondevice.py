"""Tests for weOndevice (happytalkman/ripgrep + OpenJarvis integration)."""

from weondevice.agent import _resolve_search_root
from weondevice.integrations.weaidl6_bridge import WeAidL6InspectTool
from weondevice.tools.laptop_tools import QuickNoteTool, SystemStatusTool
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


def test_laptop_tools_and_weaidl6_bridge(tmp_path):
    sys_res = SystemStatusTool().execute()
    assert sys_res.success
    assert "cpu_cores" in sys_res.content

    note_tool = QuickNoteTool(notes_file=tmp_path / "notes.md")
    add_res = note_tool.execute(action="add", text="테스트 메모")
    assert add_res.success
    list_res = note_tool.execute(action="list")
    assert "테스트 메모" in list_res.content

    w_res = WeAidL6InspectTool().execute()
    assert isinstance(w_res.content, str)

    # Korean particle stripping fallback
    root = _resolve_search_root("c:\\Users에서 pdf 파일 찾아줘", ".")
    assert "에서" not in root
