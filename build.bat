@echo off
REM Genera dist\UnificadorPDF.exe (requiere Python 3.10+ instalado en Windows)
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name UnificadorPDF UnificadorPDF.py
echo.
echo Listo: dist\UnificadorPDF.exe
pause
