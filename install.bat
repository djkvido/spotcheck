@echo off
setlocal
cd /d "%~dp0"
set "QUIET="
if /i "%~1"=="/quiet" set "QUIET=1"

call :find_python
if not defined PY (
  where winget >nul 2>nul
  if errorlevel 1 goto :no_python
  echo Python 3.10 or newer was not found. Installing Python 3.12...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-source-agreements --accept-package-agreements
  call :find_python
)
if not defined PY goto :no_python

if not exist ".venv\Scripts\python.exe" (
  echo Creating a virtual environment...
  %PY% -m venv .venv || goto :err
)
echo Installing SpotCheck and its dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip || goto :err
".venv\Scripts\python.exe" -m pip uninstall --yes --quiet spotdl >nul 2>nul
".venv\Scripts\python.exe" -m pip install --quiet -e . || goto :err
copy /y pyproject.toml ".venv\pyproject.installed" >nul
echo.
".venv\Scripts\python.exe" -m spotcheck --setup
echo.
if defined QUIET exit /b 0

powershell -NoProfile -ExecutionPolicy Bypass -Command "$d = [Environment]::GetFolderPath('Desktop'); $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'SpotCheck.lnk')); $s.TargetPath = '%~dp0start.bat'; $s.WorkingDirectory = '%~dp0'; $s.IconLocation = '%~dp0spotcheck\static\spotcheck.ico'; $s.Description = 'SpotCheck'; $s.Save()" >nul 2>nul
if not errorlevel 1 echo A SpotCheck shortcut was added to your desktop.
echo Done. Press any key to start SpotCheck, or close this window.
pause >nul
call "%~dp0start.bat"
exit /b 0

:find_python
set "PY="
call :try_python py -3
call :try_python python
call :try_python "%LocalAppData%\Programs\Python\Python313\python.exe"
call :try_python "%LocalAppData%\Programs\Python\Python312\python.exe"
exit /b 0

:try_python
if defined PY exit /b 0
%* -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul
if not errorlevel 1 set "PY=%*"
exit /b 0

:no_python
echo Python 3.10 or newer was not found and could not be installed automatically.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run install.bat again.
if not defined QUIET pause
exit /b 1

:err
echo.
echo Installation failed. See the error above.
if not defined QUIET pause
exit /b 1
