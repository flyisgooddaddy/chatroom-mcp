@echo off
REM start.bat - start chatroom server + openwriter agent (one each)
cd /d C:\dev\chatroom-mcp
set PYTHONPATH=src
set PY=C:\Users\波波\Desktop\openwriter-dev\.venv\Scripts\python.exe
start "chatroom-server" /MIN %PY% -m chatroom.server --comms-dir C:\dev\test-chat --port 7777
timeout /t 4 /nobreak >nul
start "chatroom-openwriter" /MIN %PY% -u -m scripts.openwriter_agent --comms-dir C:\dev\test-chat --port 9000
echo started. open http://127.0.0.1:7777/
