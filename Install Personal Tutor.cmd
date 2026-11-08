@echo off
rem Double-click to install Personal Tutor: downloads the tools and models into this folder (20-30 GB),
rem builds the Python environment and loads the bundled courses. Safe to run again: finished steps are skipped.
title Personal Tutor - installer
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
echo.
pause
