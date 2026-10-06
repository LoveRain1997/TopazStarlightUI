@echo off
rem 一键重建 Topaz星光UI.exe(需先 pip install pyinstaller)
cd /d %~dp0
python -m PyInstaller --onefile --windowed --name "Topaz星光UI" ^
  --add-data "%~dp0slptune;slptune" ^
  --workpath _build --specpath _build --distpath . topaz_ui.py
if errorlevel 1 (pause) else (echo 构建完成: Topaz星光UI.exe & timeout /t 3 >nul)
