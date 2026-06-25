@echo off
REM install.bat — set up Speech Monitor on Windows
echo === Speech Monitor - installer ===

python --version >nul 2>&1 || (echo Python 3.9+ is required. & pause & exit /b 1)

echo Creating virtual environment...
python -m venv .venv

echo Activating...
call .venv\Scripts\activate.bat

echo Installing dependencies...
pip install --upgrade pip -q
pip install -r requirements.txt

echo.
echo  Installation complete.
echo.
echo To launch:  .venv\Scripts\activate  ^&^&  python main.py
pause
