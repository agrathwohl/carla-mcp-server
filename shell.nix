{
  pkgs ? import <nixpkgs> { },
}:

let
  # Python environment with MCP and audio processing packages
  pythonEnv = pkgs.python3.withPackages (
    ps: with ps; [
      # Core MCP - try to use what's available or install via pip
      pip
      setuptools
      wheel

      # Audio processing
      numpy
      scipy

      # System monitoring
      psutil

      # Async and file I/O
      aiofiles
      pyyaml
      python-dotenv

      mcp

      # Development tools
      pytest
    ]
  );

in
pkgs.mkShell {
  buildInputs = with pkgs; [
    pythonEnv

    # Just the bare minimum for MCP server
    pkg-config

    # Audio libs that Carla needs
    alsa-lib
    jack2
    libsndfile
    liblo
  ];

  shellHook = ''
    echo "Carla MCP Server Environment"
    echo "================================"
    echo "MCP Server: python server.py"
    echo "Test: python test_server.py"
    echo ""

    # Point to Carla frontend for Python imports
    export CARLA_PATH="$(pwd)/.."
    export PYTHONPATH="$CARLA_PATH/source/frontend:$PYTHONPATH"

    # Set LD_LIBRARY_PATH so the dynamic linker can find the libs
    export LD_LIBRARY_PATH="${pkgs.alsa-lib}/lib:${pkgs.jack2}/lib:${pkgs.libsndfile}/lib:${pkgs.liblo}/lib:$LD_LIBRARY_PATH"

    # Install MCP if not available
    if ! python -c "import mcp" 2>/dev/null; then
      echo "Installing MCP..."
      pip install --user mcp>=0.1.0
    fi

    # Install missing async packages if needed
    pip install --user asyncio-mqtt aiofiles pyyaml python-dotenv psutil numpy scipy

    echo "Ready!"
  '';

  # Minimal LD flags just for the audio libs Carla needs
  NIX_LDFLAGS = [
    "-L${pkgs.alsa-lib}/lib"
    "-L${pkgs.jack2}/lib"
    "-L${pkgs.libsndfile}/lib"
    "-L${pkgs.liblo}/lib"
  ];
}

