# weOndevice (Jarvis-RG) — 나만의 온디바이스 AI

**Stanford OpenJarvis**의 5-Primitive 온디바이스 AI 에이전트 아키텍처와 **happytalkman/ripgrep**의 초고속 Rust 검색 엔진을 결합한 100% 로컬 퍼스트(Local-First) 개인 AI 어시스턴트입니다.

## 주요 특징 (2026 최신 온디바이스 AI 트렌드 반영)
- **Zero-Vector Native Rust Ripgrep RAG**: 무거운 클라우드 벡터 DB 대신 `happytalkman/ripgrep`의 `grep-regex`, `grep-searcher`, `ignore`, `globset` 크레이트를 PyO3(`weondevice_rg`)로 Python에 직접 바인딩하여 `.gitignore`를 준수하는 초고속 검색 + BM25 리랭킹 + 토큰 예산 기반 컨텍스트 패킹을 제공합니다.
- **OpenJarvis 5-Primitive 통합**:
  - `EngineRegistry`: 인프로세스 `llama-cpp-python` 로컬 GGUF 추론 (`InProcessLlamaCppEngine`)
  - `ToolRegistry`: `ripgrep_search`, `ripgrep_files` 네이티브 툴 등록
  - `IPWMonitor` (Intelligence Per Watt): 지연 시간, 초당 토큰(tok/s), 소비 전력(W/J), 클라우드 비용 절감액(\$0.00) 실시간 추적 및 로컬 트레이스 저장(`.weondevice/traces.jsonl`)
- **MCP (Model Context Protocol) 서버 내장**: `python -m weondevice.mcp_server`로 외부 에이전트/IDE와 즉시 연동 가능
- **Jarvis-RG Web HUD & CLI**: 다크 모드 웹 대시보드 + Rich 터미널 CLI

## 실행 방법
```bash
# 1. 웹 HUD 서버 실행 (http://localhost:8090)
.venv/bin/weondevice serve --port 8090

# 2. CLI 질문
.venv/bin/weondevice ask "OpenJarvis와 ripgrep이 어떻게 통합되어 있나요?"

# 3. 초고속 네이티브 Ripgrep 검색
.venv/bin/weondevice search "ToolRegistry" --root src
```
