@echo off
setlocal
rem Reuse an already-running app before checking Python or installing packages.
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/documents' -UseBasicParsing -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 (
    echo Invoice reconciliation is already running at http://127.0.0.1:8000
    start "" http://127.0.0.1:8000
    exit /b 0
)
powershell -NoProfile -Command "$c = New-Object Net.Sockets.TcpClient; try { $c.Connect('127.0.0.1', 8000); exit 0 } catch { exit 1 } finally { $c.Close() }"
if not errorlevel 1 (
    echo ERROR: Port 8000 is occupied, but the invoice app did not respond.
    echo Close the process using port 8000 or change the app port, then retry.
    exit /b 1
)
set "PYTHON=python"
if exist .venv\Scripts\python.exe set "PYTHON=.venv\Scripts\python.exe"
if not exist .venv\Scripts\python.exe echo Using system Python because .venv\Scripts\python.exe is missing; no attempt will be made to recreate .venv.
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Dependency installation failed for %PYTHON%.
    exit /b 1
)
%PYTHON% -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 (
    echo ERROR: The server stopped. If port 8000 is occupied, close its owning app process or select another port.
    exit /b 1
)



