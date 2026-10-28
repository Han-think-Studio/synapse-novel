@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions DisableDelayedExpansion

set "SYNAPSE_ROOT=%~dp0"
for %%I in ("%SYNAPSE_ROOT%.") do set "SYNAPSE_ROOT=%%~fI"
set "SYNAPSE_PAUSE=1"
if /I "%~1"=="--auto" set "SYNAPSE_PAUSE=0"
if not exist "%SYNAPSE_ROOT%\pyproject.toml" goto root_error

pushd "%SYNAPSE_ROOT%" >nul 2>&1
if errorlevel 1 goto root_error

echo ===================================================
echo   Synapse Novel - general command help
echo ===================================================
echo [INFO] Synapse root detected: %SYNAPSE_ROOT%
echo [INFO] Launch directory is independent of the current folder.
echo.

set "PYTHON_EXE=%SYNAPSE_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" goto prepare_runtime
"%PYTHON_EXE%" --version 2^>^&1 | findstr /C:"Python 3.12." >nul
if not errorlevel 1 goto runtime_ready
echo [WARNING] The local .venv exists but is not a usable Python 3.12 runtime.

:prepare_runtime
echo [INFO] Local Python was not found. Starting portable setup...
call "%SYNAPSE_ROOT%\setup.bat" --auto
if errorlevel 1 goto setup_error
if not exist "%PYTHON_EXE%" goto setup_error

:runtime_ready
"%PYTHON_EXE%" --version 2^>^&1 | findstr /C:"Python 3.12." >nul
if errorlevel 1 goto setup_error
"%PYTHON_EXE%" "%SYNAPSE_ROOT%\editions\novel\synapse_novel.py" --help
set "SYNAPSE_EXIT=%ERRORLEVEL%"
if not "%SYNAPSE_EXIT%"=="0" echo [ERROR] The command list stopped with code %SYNAPSE_EXIT%.
goto finish

:root_error
echo [ERROR] This launcher is not beside a valid Synapse Novel root.
echo [ERROR] Expected pyproject.toml under: %SYNAPSE_ROOT%
set "SYNAPSE_EXIT=1"
goto finish

:setup_error
echo [ERROR] Portable runtime setup failed under: %SYNAPSE_ROOT%
set "SYNAPSE_EXIT=1"

:finish
popd >nul 2>&1
if "%SYNAPSE_PAUSE%"=="1" pause
exit /b %SYNAPSE_EXIT%
