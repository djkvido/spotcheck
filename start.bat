@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call "%~dp0install.bat" /quiet || (pause & exit /b 1)
) else (
  fc /b pyproject.toml ".venv\pyproject.installed" >nul 2>nul || call "%~dp0install.bat" /quiet || (pause & exit /b 1)
)
".venv\Scripts\python.exe" -m spotcheck %*
if errorlevel 1 pause
