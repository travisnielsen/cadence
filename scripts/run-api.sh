#!/bin/bash

# Run the FastAPI backend server

cd "$(dirname "$0")/../backend" || exit 1

# Check for virtual environment
if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment not found. Run setup first:"
    echo "  npm run setup:api"
    exit 1
fi

# Check for .env file
if [ ! -f ".env" ]; then
    echo "Warning: .env file not found. Copy .env.example to .env and configure it."
    echo "  cp backend/.env.example backend/.env"
    exit 1
fi

echo "Starting FastAPI server on http://localhost:8000..."
.venv/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
