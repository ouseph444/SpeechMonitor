#!/usr/bin/env bash
# install.sh — set up Speech Monitor on macOS / Linux
set -e

echo "=== Speech Monitor — installer ==="

# On macOS, ensure tkinter is available (Homebrew Python needs python-tk)
if [[ "$OSTYPE" == "darwin"* ]] && command -v brew &>/dev/null; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    python3 -c "import tkinter" 2>/dev/null || {
        echo "Installing tkinter support for Python $PY_VER..."
        brew install "python-tk@$PY_VER"
    }
fi

# Check Python 3.9+
python3 --version >/dev/null 2>&1 || { echo "Python 3.9+ is required."; exit 1; }

echo "Creating virtual environment..."
python3 -m venv .venv

echo "Activating..."
source .venv/bin/activate

echo "Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt

echo ""
echo "✓ Installation complete."
echo ""
echo "To launch:  source .venv/bin/activate && python main.py"
