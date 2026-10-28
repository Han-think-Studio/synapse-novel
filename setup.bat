@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "SYNAPSE_ROOT=%~dp0"
for %%I in ("%SYNAPSE_ROOT%.") do set "SYNAPSE_ROOT=%%~fI"
for %%I in ("%SYNAPSE_ROOT%\.venv") do set "SYNAPSE_VENV_PATH=%%~fI"
set "SYNAPSE_LOG_DIR=%TEMP%"
if not defined SYNAPSE_LOG_DIR set "SYNAPSE_LOG_DIR=%SYNAPSE_ROOT%"
if not exist "%SYNAPSE_LOG_DIR%" set "SYNAPSE_LOG_DIR=%SYNAPSE_ROOT%"
set "SYNAPSE_PAUSE=1"
set "SYNAPSE_DIAGNOSE=0"
set "SYNAPSE_HEALTH_DIAGNOSE=0"
set "SYNAPSE_VENV_CREATED=0"
if /I "%~1"=="--auto" set "SYNAPSE_PAUSE=0"
if /I "%~1"=="--diagnose" set "SYNAPSE_DIAGNOSE=1"
if /I "%~1"=="--diagnose-health" set "SYNAPSE_HEALTH_DIAGNOSE=1"
if /I "%~2"=="--auto" set "SYNAPSE_PAUSE=0"
if /I "%~2"=="--diagnose" set "SYNAPSE_DIAGNOSE=1"
if /I "%~2"=="--diagnose-health" set "SYNAPSE_HEALTH_DIAGNOSE=1"

pushd "%SYNAPSE_ROOT%" >nul 2>&1
if errorlevel 1 goto root_error
if not exist "%SYNAPSE_ROOT%\pyproject.toml" goto root_error
if not exist "%SYNAPSE_ROOT%\requirements.txt" goto root_error
if not exist "%SYNAPSE_ROOT%\editions\novel\synapse_novel.py" goto root_error

echo ===================================================
echo [Synapse Novel] Portable setup
echo ===================================================
echo [INFO] Synapse root detected: %SYNAPSE_ROOT%
echo [INFO] Current directory is not used for path resolution.
echo.

set "PYTHON_LAUNCHER="
python --version >nul 2>&1
if not errorlevel 1 (
    python --version 2^>^&1 | findstr /C:"Python 3.12." >nul
    if not errorlevel 1 set "PYTHON_LAUNCHER=python"
)
if defined PYTHON_LAUNCHER goto python_ready

py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    py -3.12 --version 2^>^&1 | findstr /C:"Python 3.12." >nul
    if not errorlevel 1 set "PYTHON_LAUNCHER=py -3.12"
)
if defined PYTHON_LAUNCHER goto python_ready

echo [ERROR] Python 3.12.x was not found on PATH or through the py launcher.
echo [NEXT] Install the Python 3.12.x Windows release from:
echo        https://www.python.org/downloads/windows/
echo [NEXT] Add Python to PATH or make `py -3.12` available, then run setup.bat again.
echo [INFO] Synapse Novel does not download or bundle Python or model files.
goto setup_error

:python_ready
echo [INFO] Selected system launcher: %PYTHON_LAUNCHER%
%PYTHON_LAUNCHER% -c "import sys; print('[INFO] Selected executable: ' + sys.executable); print('[INFO] Selected version: ' + sys.version.split()[0])"
if errorlevel 1 goto setup_error
if "%SYNAPSE_HEALTH_DIAGNOSE%"=="1" goto diagnose_health
if "%SYNAPSE_DIAGNOSE%"=="1" (
    echo [SUCCESS] Runtime diagnosis completed. No environment changes were made.
    set "SYNAPSE_EXIT=0"
    goto finish
)

if not exist "%SYNAPSE_VENV_PATH%\Scripts\python.exe" goto create_venv
if exist "%SYNAPSE_VENV_PATH%\Scripts\python.exe\NUL" goto create_venv
for %%I in ("%SYNAPSE_VENV_PATH%\Scripts\python.exe") do if %%~zI LEQ 0 goto create_venv
cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)"" >nul 2>&1
if errorlevel 1 goto create_venv
goto venv_ready

:create_venv
call :archive_existing_venv
if errorlevel 1 goto setup_error
echo [1/2] Creating virtual environment at %SYNAPSE_VENV_PATH% ...
set "SYNAPSE_FAILURE_CONTEXT=virtual environment creation"
set "SYNAPSE_SETUP_LOG=%SYNAPSE_LOG_DIR%\synapse-novel-venv-%RANDOM%.log"
call %PYTHON_LAUNCHER% -m venv "%SYNAPSE_VENV_PATH%" >"%SYNAPSE_SETUP_LOG%" 2>&1
set "SYNAPSE_SETUP_EXIT=%ERRORLEVEL%"
if not "%SYNAPSE_SETUP_EXIT%"=="0" (
    type "%SYNAPSE_SETUP_LOG%"
    call :report_setup_failure
    goto setup_error
)
del /q "%SYNAPSE_SETUP_LOG%" >nul 2>&1
set "SYNAPSE_VENV_CREATED=1"
goto venv_ready

:archive_existing_venv
set "SYNAPSE_VENV_REPARSE=0"
fsutil reparsepoint query "%SYNAPSE_VENV_PATH%" >nul 2>&1
if not errorlevel 1 set "SYNAPSE_VENV_REPARSE=1"
if not exist "%SYNAPSE_VENV_PATH%" if "%SYNAPSE_VENV_REPARSE%"=="0" exit /b 0
set "SYNAPSE_RECOVERY_ATTEMPTS=0"

:archive_existing_venv_choose
set /a SYNAPSE_RECOVERY_ATTEMPTS+=1
if %SYNAPSE_RECOVERY_ATTEMPTS% GTR 10 (
    echo [ERROR] Could not choose a free recovery name under %SYNAPSE_ROOT%.
    echo [NEXT] No existing .venv content was deleted.
    exit /b 1
)
set "SYNAPSE_RECOVERY_PATH=%SYNAPSE_ROOT%\.venv.invalid-%RANDOM%"
if exist "%SYNAPSE_RECOVERY_PATH%" goto archive_existing_venv_choose
if "%SYNAPSE_VENV_REPARSE%"=="1" (
    echo [WARNING] A reparse point or junction was found at the exact .venv target.
) else (
    echo [WARNING] An invalid or stale .venv was found at the exact .venv target.
)
echo [INFO] Preserving it by renaming to %SYNAPSE_RECOVERY_PATH% ...
move "%SYNAPSE_VENV_PATH%" "%SYNAPSE_RECOVERY_PATH%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Could not safely preserve the existing .venv.
    echo [NEXT] No existing .venv content was deleted. Resolve the target manually, then run setup.bat again.
    exit /b 1
)
echo [INFO] Existing .venv preserved at %SYNAPSE_RECOVERY_PATH%.
exit /b 0

:venv_ready
echo [INFO] Local Python detected: "%SYNAPSE_VENV_PATH%\Scripts\python.exe"
if "%SYNAPSE_VENV_CREATED%"=="0" (
    cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" -c "import yaml"" >nul 2>&1
    if not errorlevel 1 cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" "%SYNAPSE_ROOT%\editions\novel\synapse_novel.py" --help" >nul 2>&1
    if not errorlevel 1 (
        echo [INFO] Existing runtime passed the dependency and CLI checks.
        echo [INFO] No pip download or upgrade was needed.
        goto setup_success
    )
    echo [WARNING] Existing runtime needs dependency repair; running the declared install.
)
echo [1/2] Virtual environment is ready.
echo [2/2] Installing the declared runtime dependency...
set "SYNAPSE_FAILURE_CONTEXT=declared dependency installation"
set "SYNAPSE_SETUP_LOG=%SYNAPSE_LOG_DIR%\synapse-novel-pip-%RANDOM%.log"
call "%SYNAPSE_VENV_PATH%\Scripts\python.exe" -m pip install -r "%SYNAPSE_ROOT%\requirements.txt" >"%SYNAPSE_SETUP_LOG%" 2>&1
set "SYNAPSE_SETUP_EXIT=%ERRORLEVEL%"
if not "%SYNAPSE_SETUP_EXIT%"=="0" (
    type "%SYNAPSE_SETUP_LOG%"
    call :report_setup_failure
    goto setup_error
)
del /q "%SYNAPSE_SETUP_LOG%" >nul 2>&1
cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" -c "import yaml"" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The installed runtime cannot import pyyaml.
    goto setup_error
)
cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" "%SYNAPSE_ROOT%\editions\novel\synapse_novel.py" --help" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] The Synapse Novel CLI could not be executed by the prepared runtime.
    goto setup_error
)

:setup_success
echo.
echo ===================================================
echo [SUCCESS] Synapse Novel portable setup completed.
echo [INFO] New runtime path: "%SYNAPSE_VENV_PATH%"
echo [INFO] Run run.bat or run-core.bat from any directory.
echo ===================================================
set "SYNAPSE_EXIT=0"
goto finish

:diagnose_health
echo [INFO] Checking the existing .venv without changing it.
if not exist "%SYNAPSE_VENV_PATH%\Scripts\python.exe" (
    echo [ERROR] Health check: .venv is missing or has no Python executable.
    set "SYNAPSE_EXIT=1"
    goto finish
)
if exist "%SYNAPSE_VENV_PATH%\Scripts\python.exe\NUL" (
    echo [ERROR] Health check: .venv Python path is a directory, not an executable.
    set "SYNAPSE_EXIT=1"
    goto finish
)
for %%I in ("%SYNAPSE_VENV_PATH%\Scripts\python.exe") do if %%~zI LEQ 0 (
    echo [ERROR] Health check: .venv Python executable is empty or corrupt.
    set "SYNAPSE_EXIT=1"
    goto finish
)
cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)"" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Health check: .venv is not Python 3.12.x.
    set "SYNAPSE_EXIT=1"
    goto finish
)
cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" -c "import sys; print('[INFO] .venv executable: ' + sys.executable); print('[INFO] .venv version: ' + sys.version.split()[0])""
cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" -c "import yaml"" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Health check: pyyaml could not be imported.
    set "SYNAPSE_EXIT=1"
    goto finish
)
echo [INFO] Health check: pyyaml import succeeded.
cmd.exe /d /c ""%SYNAPSE_VENV_PATH%\Scripts\python.exe" "%SYNAPSE_ROOT%\editions\novel\synapse_novel.py" --help" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Health check: Synapse Novel CLI could not be executed.
    set "SYNAPSE_EXIT=1"
    goto finish
)
echo [SUCCESS] Runtime health diagnosis completed. No environment changes were made.
set "SYNAPSE_EXIT=0"
goto finish

:report_setup_failure
set "SYNAPSE_FAILURE_CLASS=dependency"
findstr /I /C:"Could not fetch URL" /C:"Temporary failure in name resolution" /C:"Failed to establish a new connection" /C:"Connection timed out" "%SYNAPSE_SETUP_LOG%" >nul
if not errorlevel 1 set "SYNAPSE_FAILURE_CLASS=network"
findstr /I /C:"Access is denied" /C:"Permission denied" /C:"PermissionError" /C:"WinError 5" /C:"not permitted" "%SYNAPSE_SETUP_LOG%" >nul
if not errorlevel 1 set "SYNAPSE_FAILURE_CLASS=permission"
findstr /I /C:"Requires-Python" /C:"requires a different Python" /C:"Unsupported Python" /C:"not supported" "%SYNAPSE_SETUP_LOG%" >nul
if not errorlevel 1 set "SYNAPSE_FAILURE_CLASS=version"
echo [ERROR] %SYNAPSE_FAILURE_CONTEXT% failed (%SYNAPSE_FAILURE_CLASS%).
if "%SYNAPSE_FAILURE_CLASS%"=="network" (
    echo [NEXT] Check the package index, network, proxy, or local package source, then run setup.bat again.
) else if "%SYNAPSE_FAILURE_CLASS%"=="permission" (
    echo [NEXT] Move the checkout to a writable folder or fix permissions, then run setup.bat again.
) else if "%SYNAPSE_FAILURE_CLASS%"=="version" (
    echo [NEXT] Use Python 3.12.x and compatible declared dependencies, then run setup.bat again.
) else (
    echo [NEXT] Inspect the pip output above and correct the declared dependency, then run setup.bat again.
)
echo [INFO] Existing environment content was not recursively deleted; retry is safe.
echo [INFO] Full failure log: %SYNAPSE_SETUP_LOG%
exit /b 0

:root_error
echo [ERROR] This launcher is not beside a valid Synapse Novel root.
echo [ERROR] Expected pyproject.toml and requirements.txt under:
echo          %SYNAPSE_ROOT%
set "SYNAPSE_EXIT=1"
goto finish

:setup_error
echo [ERROR] Portable setup failed. The detected root was:
echo          %SYNAPSE_ROOT%
set "SYNAPSE_EXIT=1"

:finish
popd >nul 2>&1
if "%SYNAPSE_PAUSE%"=="1" pause
exit /b %SYNAPSE_EXIT%
