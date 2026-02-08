#!/bin/bash

# Setup script for the API backend

# Navigate to repo root
cd "$(dirname "$0")/../.." || exit 1

echo "Setting up Python environment with uv..."
uv sync --all-extras --dev

echo ""
echo "Setup complete!"
echo ""
echo "To start the server, run:"
echo "  uv run poe dev-api"
echo ""
echo "Or: ./infra/scripts/run-api.sh"
