@echo off
rem Topaz 星光独立UI —— 双击即用(优先 exe;没有 exe 时用 python 源码运行)
cd /d %~dp0
if exist "Topaz星光UI.exe" (
  start "" "Topaz星光UI.exe"
) else (
  python topaz_ui.py
)
