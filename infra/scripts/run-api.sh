#!/bin/bash

# Run the FastAPI backend server

# Navigate to repo root (scripts is at infra/scripts/)
cd "$(dirname "$0")/../.." || exit 1

# Check for virtual environment
if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment not found. Run setup first:"
    echo "  ./devsetup.sh"
    exit 1
fi

# Check for .env file
if [ ! -f "src/backend/.env" ]; then
    echo "Warning: .env file not found. Copy .env.example and configure it."
    echo "  cp src/backend/.env.example src/backend/.env"
    exit 1
fi

# Load .env
set -a
source src/backend/.env
set +a

echo "Starting FastAPI server on http://localhost:8000..."
PYTHONPATH=src/backend .venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
