#!/usr/bin/env bash
# Resume Tailor - Linux / WSL / macOS launcher
# Creates a .venv on first run, installs deps, then launches the Streamlit app.

set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo ">> Creating virtual environment ($VENV_DIR)..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo ">> Activating virtual environment..."
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo ">> Installing/updating dependencies..."
python -m pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo ">> Starting Resume Tailor at http://localhost:8501 ..."
exec streamlit run app.py
