#!/bin/bash

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

python src/main.py 