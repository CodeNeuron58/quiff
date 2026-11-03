@echo off
rem Double-click to open Quiff: a page on this computer to teach chores and run them again.
cd /d "%~dp0"
uv run quiff app
if errorlevel 1 pause
