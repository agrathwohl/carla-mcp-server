#!/bin/bash
# Launch script for Carla MCP Server - BACKEND ONLY, NO QT5!
# Assumes JACK is already running and uv environment has all dependencies

set -e

# Carla paths
export CARLA_PATH="/home/gwohl/builds/Carla"
export PYTHONPATH="$CARLA_PATH/source/frontend:$PYTHONPATH"

# Set library paths for Carla standalone backend + ALL NixOS dependencies
export LD_LIBRARY_PATH="$CARLA_PATH/bin:/nix/store/wfna2fx4023769504rjzgn0mz0m6bc0c-libx11-1.8.12/lib/:/nix/store/sr98snb2zgnf5q2ss6vjwi0frhgx2v5m-alsa-lib-1.2.14/lib:/nix/store/bp4qwdyll7c128km238knfyjf7l5x0dy-jack2-1.9.22/lib:/nix/store/qs20y382adadwm5sr5gvpc343kj6208f-libsndfile-1.2.2/lib:/nix/store/n9hw8qzq7b3gjvicq1z4gh251dp19j9s-liblo-0.32/lib:/nix/store/v40ijzz8p2fpk9ihjck3a1ncqaqfmn3c-file-5.45/lib:/nix/store/30gbzik2rvw90hxx2ldhc6lb4gn1jfhi-fluidsynth-2.4.6/lib:/nix/store/0idq24xm0f6pg15ckii31agha3zn1whm-libpulseaudio-16.1/lib:/nix/store/090dqxvksccybvma300n4rx044y4cxvy-xgcc-13.3.0-libgcc/lib:$LD_LIBRARY_PATH"

# Verify Carla backend library exists
if [ ! -f "$CARLA_PATH/bin/libcarla_standalone2.so" ]; then
    echo "ERROR: Carla standalone library not found!"
    echo "Expected: $CARLA_PATH/bin/libcarla_standalone2.so"
    exit 1
fi

# Verify Python backend can be imported
python3 -c "
import sys
sys.path.append('$CARLA_PATH/source/frontend')
from carla_backend import *
print('✓ Carla backend verified')
" || {
    echo "ERROR: Cannot import carla_backend"
    exit 1
}

echo "🎵 Starting Carla MCP Server (Backend Only)"
echo "📁 Carla path: $CARLA_PATH"
echo "🔌 JACK assumed running"
echo "🚫 No Qt5 - Backend only!"
echo "🎯 MCP Server starting..."
echo ""

# Start the MCP server
cd "$(dirname "$0")"
python3 server.py "$@"