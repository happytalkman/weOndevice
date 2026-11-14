@echo off
cd /d C:\openjarvis
start "" http://localhost:8090
.venv\Scripts\weondevice.exe serve --port 8090 --root C:\openjarvis
