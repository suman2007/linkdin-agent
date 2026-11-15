@echo off
rem LinkedIn LTE/4G agent - one-click launcher.
rem   run.bat            normal mode: asks before each like, loops every interval
rem   run.bat dry        dry run: prints matches, never likes
rem   run.bat once       single scan, then exit
rem   run.bat watch      dry run with a visible browser + HTML scan report
rem   run.bat login      only (re)login to LinkedIn
rem   run.bat setup      choose companies / keywords / connections to look for
rem   run.bat jobs       find recent jobs (see jobs.yaml), skip ones you applied to, write a report
rem   run.bat applied <job link or id> ...   record jobs you applied to outside LinkedIn
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11+ from python.org and retry.
  pause & exit /b 1
)

rem --- first-run setup (dependencies + browser), skipped afterwards
if not exist ".setup_done" (
  echo Installing dependencies...
  python -m pip install -q -r requirements.txt || (echo pip install failed & pause & exit /b 1)
  python -m playwright install chromium || (echo browser install failed & pause & exit /b 1)
  echo done> .setup_done
)

if /i "%~1"=="login" goto :login
if /i "%~1"=="setup" goto :setup
if /i "%~1"=="applied" goto :applied

rem --- API key check (key lives in .env, which is gitignored)
if not exist ".env" (
  echo Missing .env. Copy .env.example to .env and set NVIDIA_API_KEY=nvapi-... or ANTHROPIC_API_KEY=sk-ant-...
  pause & exit /b 1
)
findstr /b /c:"NVIDIA_API_KEY=nvapi-" /c:"ANTHROPIC_API_KEY=sk-ant-" ".env" >nul
if errorlevel 1 (
  echo .env must contain NVIDIA_API_KEY=nvapi-... or ANTHROPIC_API_KEY=sk-ant-...
  pause & exit /b 1
)

if /i "%~1"=="jobs" goto :jobs
set ARGS=
if /i "%~1"=="dry"  set ARGS=--once --notify-only
if /i "%~1"=="once" set ARGS=--once
if /i "%~1"=="watch" set ARGS=--once --notify-only --show --open

python -m agent run %ARGS%
if not errorlevel 1 goto :end

rem --- run failed: most likely not logged in yet
echo.
echo Not logged in or session expired. Opening login...
:login
python -m agent login
if errorlevel 1 (pause & exit /b 1)
if /i "%~1"=="login" goto :end
python -m agent run %ARGS%

goto :end

:setup
python -m agent setup
goto :end

:applied
python -m agent applied %2 %3 %4 %5 %6 %7 %8 %9
goto :end

:jobs
python -m agent jobs --open

:end
pause
