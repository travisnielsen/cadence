#!/bin/bash

# Run the Next.js frontend

cd "$(dirname "$0")/../frontend" || exit 1

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "node_modules not found. Running pnpm install..."
    pnpm install
fi

echo "Starting Next.js frontend on http://localhost:3000..."
pnpm run dev
