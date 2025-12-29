#!/bin/bash

# Setup script for the frontend

cd "$(dirname "$0")/../frontend" || exit 1

echo "Installing npm dependencies..."
npm install

echo ""
echo "Setup complete!"
echo ""
echo "To start the frontend, run:"
echo "  ./scripts/run-frontend.sh"
