@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\flow2skill.exe" (
  ".venv\Scripts\flow2skill.exe" studio
) else (
  python -m flow2skill studio
)
if errorlevel 1 (
  echo.
  echo Flow2Skill could not start. Install it once with:
  echo   python -m pip install -e ".[dev]"
  pause
)
