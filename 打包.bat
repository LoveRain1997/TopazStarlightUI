@echo off
rem 一键重建两个可执行:Topaz星光UI.exe(窗口版) + Topaz星光CLI.exe(命令行版,供AI/脚本调用)
rem 需先 pip install pyinstaller
cd /d %~dp0
python -m PyInstaller --onefile --windowed --name "Topaz星光UI" ^
  --add-data "%~dp0slptune;slptune" ^
  --workpath _build --specpath _build --distpath . topaz_ui.py || goto :err
python -m PyInstaller --onefile --console --name "Topaz星光CLI" ^
  --add-data "%~dp0slptune;slptune" ^
  --workpath _build --specpath _build --distpath . topaz_ui.py || goto :err
echo 构建完成: Topaz星光UI.exe + Topaz星光CLI.exe
timeout /t 3 >nul
exit /b 0
:err
pause
