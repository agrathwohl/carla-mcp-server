#!/bin/bash
# Start script for Carla MCP Server

# Set Carla path
export CARLA_PATH="${CARLA_PATH:-/home/gwohl/builds/Carla}"

# Check if Carla exists
if [ ! -d "$CARLA_PATH" ]; then
    echo "Error: Carla not found at $CARLA_PATH"
    echo "Please set CARLA_PATH environment variable"
    exit 1
fi

# Check if library exists
if [ ! -f "$CARLA_PATH/bin/libcarla_standalone2.so" ]; then
    echo "Error: Carla library not found at $CARLA_PATH/bin/libcarla_standalone2.so"
    echo "Please build Carla first: cd $CARLA_PATH && make"
    exit 1
fi

# Add Carla Python modules to path
export PYTHONPATH="$CARLA_PATH/source/frontend:$PYTHONPATH"

# Set library paths for dynamic linker - all audio/Qt libs Carla needs
export LD_LIBRARY_PATH="/nix/store/wfna2fx4023769504rjzgn0mz0m6bc0c-libx11-1.8.12/lib/:/nix/store/sr98snb2zgnf5q2ss6vjwi0frhgx2v5m-alsa-lib-1.2.14/lib:/nix/store/bp4qwdyll7c128km238knfyjf7l5x0dy-jack2-1.9.22/lib:/nix/store/qs20y382adadwm5sr5gvpc343kj6208f-libsndfile-1.2.2/lib:/nix/store/n9hw8qzq7b3gjvicq1z4gh251dp19j9s-liblo-0.32/lib:/nix/store/v40ijzz8p2fpk9ihjck3a1ncqaqfmn3c-file-5.45/lib:/nix/store/30gbzik2rvw90hxx2ldhc6lb4gn1jfhi-fluidsynth-2.4.6/lib:/nix/store/lrag8bdlffk1im9kj60b3xc6ivlhkm8q-qtbase-5.15.7/lib:$LD_LIBRARY_PATH"

# Check for Python dependencies
echo "Checking dependencies..."
python3 -c "import mcp" 2>/dev/null || {
    echo "MCP not installed. Installing dependencies..."
    pip3 install -r requirements.txt
}

# Start JACK if not running (optional)
if command -v jack_control &> /dev/null; then
    jack_control status | grep -q "started" || {
        echo "Starting JACK audio server..."
        jack_control start
        sleep 2
    }
fi

# Run the server
echo "Starting Carla MCP Server..."
echo "Carla path: $CARLA_PATH"
echo "Server will listen on localhost:8765"
echo "Press Ctrl+C to stop"
echo ""

python3 server.py "$@"
