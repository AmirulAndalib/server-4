#!/bin/env bash
set -e

echo "Setting up Music Assistant development container..."

# Fix permissions for the musicassistant data directory if needed
if [ -d "/home/mass/.musicassistant" ]; then
    echo "Fixing permissions for .musicassistant directory..."
    sudo chown -R mass:mass /home/mass/.musicassistant 2>/dev/null || true
fi

echo "Running setup script..."
# Run the original setup script
./scripts/setup.sh "$@"

echo ""
echo "=============================================================="
echo "Setup complete!"
echo ""
echo "To start Music Assistant:"
echo "1. Press F5 in VS Code to start with debugging"
echo "2. Or run: python -m music_assistant --log-level debug"
echo ""
echo "Access the UI at: http://localhost:8095"
echo "=============================================================="
