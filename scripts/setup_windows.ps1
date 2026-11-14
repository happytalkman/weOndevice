# Windows Laptop One-Click Setup for C:\openjarvis
# Uses signed system Python (-m venv) to avoid Windows Smart App Control (os error 4551) blocking uv's unsigned venv stub.
$ErrorActionPreference = "Stop"
$TargetDir = "C:\openjarvis"

Write-Host "=== Jarvis-RG (OpenJarvis + ripgrep + weaidL6 + Voice AI) C:\openjarvis Setup ===" -ForegroundColor Cyan

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
        Write-Host "weaidL6 clone skipped (optional)." -ForegroundColor DarkYellow
    }
}
if (-not (Test-Path "third_party\ppt-master")) {
    Write-Host "Cloning hugohe3/ppt-master..." -ForegroundColor Yellow
    git clone --depth 1 https://github.com/hugohe3/ppt-master.git third_party\ppt-master
}

# Find signed Python (avoid uv's unsigned python-build-standalone / venv launcher on Smart App Control PCs)
$PyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PyCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PyCmd = "python"
} else {
    Write-Host "Installing signed Python 3.11 via winget..." -ForegroundColor Yellow
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    $PyCmd = "py"
}

if (Test-Path ".venv") {
    Remove-Item -Recurse -Force ".venv"
}

& $PyCmd -m venv .venv
$VenvPy = "$TargetDir\.venv\Scripts\python.exe"

& $VenvPy -m pip install --upgrade pip
& $VenvPy -m pip install -e ".\third_party\OpenJarvis[server]" -e . fastapi uvicorn rank-bm25 mcp huggingface_hub psutil rich click websockets python-pptx Pillow XlsxWriter PyYAML
& $VenvPy -m pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

if (Get-Command cargo -ErrorAction SilentlyContinue) {
    $env:PYO3_PYTHON = $VenvPy
    cargo build --release --manifest-path rust\weondevice-rg\Cargo.toml
    Copy-Item "rust\weondevice-rg\target\release\weondevice_rg.dll" ".venv\Lib\site-packages\weondevice_rg.pyd" -Force
}

New-Item -ItemType Directory -Force -Path "models" | Out-Null
& $VenvPy -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='Qwen/Qwen2.5-0.5B-Instruct-GGUF', filename='qwen2.5-0.5b-instruct-q4_k_m.gguf', local_dir='models')"

Write-Host "Setup complete! Run C:\openjarvis\scripts\start_openjarvis.bat to launch." -ForegroundColor Green
