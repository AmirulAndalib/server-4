#!/bin/bash
set -e

echo "Setting up Music Assistant development container..."

# Fix permissions for the musicassistant data directory if needed
if [ -d "/home/mass/.musicassistant" ]; then
    echo "Fixing permissions for .musicassistant directory..."
    sudo chown -R mass:mass /home/mass/.musicassistant 2>/dev/null || true
fi

# Kill any existing processes on port 8095 to avoid conflicts
echo "Checking for port conflicts..."
sudo pkill -f ":8095" 2>/dev/null || true
sudo lsof -ti:8095 | xargs sudo kill -9 2>/dev/null || true

# Wait a moment for cleanup
sleep 2

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
echo "(Using host networking - no port forwarding needed)"
echo "=============================================================="
