@echo off
cd /d C:\openjarvis
set PYTHONPATH=C:\openjarvis\src
start "" http://localhost:8090
.venv\Scripts\python.exe -m weondevice.cli serve --port 8090 --root C:\openjarvis
