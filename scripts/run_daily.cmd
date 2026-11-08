@echo off
setlocal
rem Manual runner. The scheduled task calls jd_daily_beans.py directly via
rem pythonw.exe, so this wrapper is only for interactive troubleshooting.
set "SCRIPT_DIR=%~dp0"
if exist "%SCRIPT_DIR%.runtime.cmd" call "%SCRIPT_DIR%.runtime.cmd"
if not defined PYTHON_EXE set "PYTHON_EXE=python"
if not defined CONFIG_PATH set "CONFIG_PATH=%SCRIPT_DIR%..\config.json"

"%PYTHON_EXE%" "%SCRIPT_DIR%jd_daily_beans.py" --config "%CONFIG_PATH%" %*
set "RC=%ERRORLEVEL%"
echo.
echo jd-daily-beans exit=%RC%
exit /b %RC%
