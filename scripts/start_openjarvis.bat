@echo off
cd /d C:\openjarvis
set PYTHONPATH=C:\openjarvis\src
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8090 ^| findstr LISTENING') do taskkill /f /pid %%a >nul 2>&1
start /b cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8090"
.venv\Scripts\python.exe -m weondevice.cli serve --port 8090 --root C:\openjarvis
