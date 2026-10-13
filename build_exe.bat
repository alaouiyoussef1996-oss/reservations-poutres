@echo off
REM Cree un .exe autonome (dossier dist\ReservationsPoutre)
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\pip install -r requirements.txt
)
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconfirm --windowed --name ReservationsPoutre --icon assets\logo.ico --add-data "assets;assets" --add-data "Livret_utilisation.pdf;." app.py
echo.
echo Termine : dist\ReservationsPoutre\ReservationsPoutre.exe
pause
