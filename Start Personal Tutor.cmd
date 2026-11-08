@echo off
rem Double-click to start the tutor (Ollama + model on the GPU + web app window).
rem PowerShell runs hidden: a failure is written to logs\start.err.log and shown in a message box.
if not exist "%~dp0logs" mkdir "%~dp0logs"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\start.ps1" 2> "%~dp0logs\start.err.log"
if errorlevel 1 powershell.exe -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('The tutor did not start. Details: %~dp0logs\start.err.log', 'Personal Tutor', 'OK', 'Error')"
