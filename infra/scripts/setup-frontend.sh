#!/bin/bash

# Setup script for the frontend

cd "$(dirname "$0")/../frontend" || exit 1

echo "Installing pnpm dependencies..."
pnpm install

echo ""
echo "Setup complete!"
echo ""
echo "To start the frontend, run:"
echo "  ./infra/scripts/run-frontend.sh"
