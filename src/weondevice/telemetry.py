"""Intelligence-Per-Watt (IPW) telemetry and local trace store."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil


DEFAULT_TRACE_DIR = Path(os.environ.get("WEONDEVICE_HOME", ".weondevice"))


@dataclass
class QueryTelemetry:
    """Efficiency and quality metrics for a single on-device AI turn."""

    trace_id: str
    timestamp: float
    query: str
    model: str
    engine: str
    search_ms: float = 0.0
    files_scanned: int = 0
    matches_found: int = 0
    context_tokens_est: int = 0
    inference_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_sec: float = 0.0
    estimated_watts: float = 0.0
    energy_joules: float = 0.0
    cloud_cost_saved_usd: float = 0.0
    cpu_percent: float = 0.0
    ram_mb: float = 0.0
    feedback: Optional[str] = None
    tools_used: List[str] = field(default_factory=list)


def _read_rapl_uj() -> Optional[int]:
    """Read Intel/AMD RAPL energy counter in microjoules if accessible."""
    rapl_path = Path("/sys/class/powercap/intel-rapl:0/energy_uj")
    try:
        if rapl_path.exists():
            return int(rapl_path.read_text().strip())
    except OSError:
        return None
    return None


class IPWMonitor:
    """Measures CPU, memory, energy (RAPL or TDP-calibrated), and cloud cost savings."""

    # Reference cloud API pricing ($ per 1M tokens) for savings comparison
    CLOUD_INPUT_PER_M = 2.50
    CLOUD_OUTPUT_PER_M = 10.00

    def __init__(self, trace_dir: Path = DEFAULT_TRACE_DIR, cpu_tdp_watts: float = 28.0) -> None:
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.trace_file = self.trace_dir / "traces.jsonl"
        self.cpu_tdp_watts = cpu_tdp_watts
        self._process = psutil.Process()

    def start_span(self) -> Dict[str, Any]:
        psutil.cpu_percent(interval=None)
        return {
            "t0": time.perf_counter(),
            "rapl0": _read_rapl_uj(),
        }

    def finish_span(
        self,
        span: Dict[str, Any],
        *,
        trace_id: str,
        query: str,
        model: str,
        engine: str,
        search_ms: float,
        files_scanned: int,
        matches_found: int,
        context_tokens_est: int,
        prompt_tokens: int,
        completion_tokens: int,
        tools_used: List[str],
    ) -> QueryTelemetry:
        total_s = max(time.perf_counter() - span["t0"], 1e-6)
        inference_ms = max((total_s * 1000.0) - search_ms, 0.0)
        cpu_pct = psutil.cpu_percent(interval=None)
        ram_mb = self._process.memory_info().rss / (1024 * 1024)

        rapl1 = _read_rapl_uj()
        rapl0 = span.get("rapl0")
        if rapl0 is not None and rapl1 is not None and rapl1 >= rapl0:
            energy_joules = (rapl1 - rapl0) / 1_000_000.0
            estimated_watts = energy_joules / total_s
        else:
            # Calibrated CPU utilization power model (idle ~4W + dynamic TDP share)
            estimated_watts = 4.0 + (self.cpu_tdp_watts * (min(cpu_pct, 100.0) / 100.0))
            energy_joules = estimated_watts * total_s

        inf_s = max(inference_ms / 1000.0, 1e-4)
        tokens_per_sec = completion_tokens / inf_s if completion_tokens > 0 else 0.0

        cloud_cost = (
            (prompt_tokens / 1_000_000.0) * self.CLOUD_INPUT_PER_M
            + (completion_tokens / 1_000_000.0) * self.CLOUD_OUTPUT_PER_M
        )

        record = QueryTelemetry(
            trace_id=trace_id,
            timestamp=time.time(),
            query=query,
            model=model,
            engine=engine,
            search_ms=round(search_ms, 2),
            files_scanned=files_scanned,
            matches_found=matches_found,
            context_tokens_est=context_tokens_est,
            inference_ms=round(inference_ms, 2),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tokens_per_sec=round(tokens_per_sec, 2),
            estimated_watts=round(estimated_watts, 2),
            energy_joules=round(energy_joules, 3),
            cloud_cost_saved_usd=round(cloud_cost, 6),
            cpu_percent=round(cpu_pct, 1),
            ram_mb=round(ram_mb, 1),
            tools_used=tools_used,
        )
        self.append_trace(record)
        return record

    def append_trace(self, record: QueryTelemetry) -> None:
        with self.trace_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")

    def record_feedback(self, trace_id: str, feedback: str) -> bool:
        if not self.trace_file.exists():
            return False
        lines = self.trace_file.read_text(encoding="utf-8").splitlines()
        updated = False
        out: List[str] = []
        for line in lines:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("trace_id") == trace_id:
                obj["feedback"] = feedback
                updated = True
            out.append(json.dumps(obj, ensure_ascii=False))
        if updated:
            self.trace_file.write_text("\n".join(out) + "\n", encoding="utf-8")
        return updated

    def summary(self, limit: int = 50) -> Dict[str, Any]:
        if not self.trace_file.exists():
            return {
                "total_queries": 0,
                "avg_search_ms": 0.0,
                "avg_tokens_per_sec": 0.0,
                "total_energy_joules": 0.0,
                "total_cost_saved_usd": 0.0,
                "recent_traces": [],
            }
        lines = [l for l in self.trace_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        records = [json.loads(l) for l in lines[-limit:]]
        n = len(records)
        return {
            "total_queries": len(lines),
            "avg_search_ms": round(sum(r["search_ms"] for r in records) / n, 2) if n else 0.0,
            "avg_tokens_per_sec": round(sum(r["tokens_per_sec"] for r in records) / n, 2) if n else 0.0,
            "total_energy_joules": round(sum(r["energy_joules"] for r in records), 3),
            "total_cost_saved_usd": round(sum(r["cloud_cost_saved_usd"] for r in records), 6),
            "recent_traces": list(reversed(records[-10:])),
        }
