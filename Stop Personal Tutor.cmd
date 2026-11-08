@echo off
rem Double-click to stop the tutor and free the GPU memory.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop.ps1"
