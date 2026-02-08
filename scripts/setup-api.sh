#!/bin/bash

# Setup script for the API backend

cd "$(dirname "$0")/../backend" || exit 1

echo "Setting up Python virtual environment..."
python3 -m venv .venv

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Installing dependencies..."
pip install --pre -e ".[dev]"

echo ""
echo "Setup complete!"
echo ""
echo "To activate the environment, run:"
echo "  source backend/.venv/bin/activate"
echo ""
echo "To start the server, run:"
echo "  ./scripts/run-api.sh"
