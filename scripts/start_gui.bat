@echo off
rem Perfect World gs editor GUI launcher (pure ASCII, no third-party deps)
cd /d "%~dp0"
echo Starting Perfect World gs editor GUI at http://127.0.0.1:7860/
start "" python server.py
echo Browser will open shortly. If not, open http://127.0.0.1:7860/ manually.
pause
