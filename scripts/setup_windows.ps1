# Windows Laptop One-Click Setup for C:\openjarvis
# Run in PowerShell: Set-ExecutionPolicy -Scope Process Bypass; .\setup_windows.ps1
$ErrorActionPreference = "Stop"
$TargetDir = "C:\openjarvis"

Write-Host "=== Jarvis-RG (OpenJarvis + ripgrep + Voice AI) C:\openjarvis Setup ===" -ForegroundColor Cyan

if (-not (Test-Path $TargetDir)) {
    git clone https://github.com/happytalkman/weOndevice.git $TargetDir
}
Set-Location $TargetDir

New-Item -ItemType Directory -Force -Path "third_party" | Out-Null
if (-not (Test-Path "third_party\OpenJarvis")) {
    git clone --depth 1 https://github.com/open-jarvis/OpenJarvis.git third_party\OpenJarvis
}
if (-not (Test-Path "third_party\ripgrep")) {
    git clone --depth 1 https://github.com/happytalkman/ripgrep.git third_party\ripgrep
}
if (-not (Test-Path "third_party\weaidL6")) {
    Write-Host "Cloning happytalkman/weaidL6 (using your local git credentials)..." -ForegroundColor Yellow
    try {
        git clone https://github.com/happytalkman/weaidL6.git third_party\weaidL6
    } catch {
        Write-Host "weaidL6 clone skipped (optional). You can clone it manually into C:\openjarvis\third_party\weaidL6." -ForegroundColor DarkYellow
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}

uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -e ".\third_party\OpenJarvis[server]" -e . llama-cpp-python fastapi uvicorn rank-bm25 mcp huggingface_hub psutil rich click

if (Get-Command cargo -ErrorAction SilentlyContinue) {
    $env:PYO3_PYTHON = "$TargetDir\.venv\Scripts\python.exe"
    cargo build --release --manifest-path rust\weondevice-rg\Cargo.toml
    Copy-Item "rust\weondevice-rg\target\release\weondevice_rg.dll" ".venv\Lib\site-packages\weondevice_rg.pyd" -Force
}

New-Item -ItemType Directory -Force -Path "models" | Out-Null
.venv\Scripts\python.exe -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='Qwen/Qwen2.5-0.5B-Instruct-GGUF', filename='qwen2.5-0.5b-instruct-q4_k_m.gguf', local_dir='models')"

Write-Host "Setup complete! Run C:\openjarvis\scripts\start_openjarvis.bat to launch." -ForegroundColor Green
