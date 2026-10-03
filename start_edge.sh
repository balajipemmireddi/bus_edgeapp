#!/bin/bash
# One-command startup for the Pi. Run: bash start_edge.sh
# 
# Configuration: Edit the .env file (or .env.example) with your IP addresses
# This script loads .env and passes variables to Python scripts

# Load .env if it exists, otherwise use .env.example as template
if [ -f ".env" ]; then
    source .env
else
    echo "[WARN] .env not found. Please copy .env.example to .env and edit it."
    if [ -f ".env.example" ]; then
        source .env.example
    else
        echo "[ERROR] .env.example not found either!"
        exit 1
    fi
fi

# Override with defaults if not set
BACKEND_URL="${BACKEND_URL:-http://192.168.1.72:8000}"
BUS_ID="${BUS_ID:-bus_14}"
ENROLLMENT_DASHBOARD_PORT="${ENROLLMENT_DASHBOARD_PORT:-8090}"
FACE_PROCESSOR_URL="${FACE_PROCESSOR_URL:-http://192.168.1.85:8095}"

echo "============================================"
echo "  Bus Edge App Startup"
echo "============================================"
echo ""
echo "Configuration (from .env):"
echo "  Bus ID: $BUS_ID"
echo "  Backend URL: $BACKEND_URL"
echo "  Face Processor: $FACE_PROCESSOR_URL"
echo "  Enrollment Dashboard Port: $ENROLLMENT_DASHBOARD_PORT"
echo ""

echo "============================================"
echo "  Starting face_processor.py in the background"
echo "  (this is what actually runs dlib - the backend delegates to it)"
echo "============================================"

# Kill any old face_processor processes first
pkill -f "face_processor.py" 2>/dev/null
sleep 1

# Start face_processor in background with proper logging
# Extract port from FACE_PROCESSOR_URL (e.g., http://192.168.1.85:8095 -> 8095)
FP_PORT=$(echo "$FACE_PROCESSOR_URL" | grep -oP ':\K[0-9]+$' || echo "8095")
python3 face_processor.py --port $FP_PORT > face_processor.log 2>&1 &
FP_PID=$!
echo "face_processor.py running (pid $FP_PID), logging to face_processor.log"

# Wait for it to start up
sleep 3

# Check if it actually started
if ! ps -p $FP_PID > /dev/null; then
    echo "[ERROR] face_processor.py failed to start. Check face_processor.log:"
    cat face_processor.log
    exit 1
fi

echo ""
echo "============================================"
echo "  Starting main.py (camera + recognition + auto-sync)"
echo "  Backend target: $BACKEND_URL"
echo "  Running in HEADLESS mode (no video window)"
echo "  Events will sync to backend every 30 seconds"
echo "  Press Ctrl+C to stop (this will also stop face_processor)"
echo "============================================"

# Export variables for subprocesses to use
export BACKEND_URL
export BUS_ID
export FACE_PROCESSOR_URL

# Run WITHOUT display to avoid segfault on headless Pi
# Events still fire and sync - just no video window
python3 main.py --bus-id "$BUS_ID" --leg auto --backend-url "$BACKEND_URL"

# When main.py exits (Ctrl+C or crash), also stop the face processor
echo ""
echo "Stopping face_processor.py (pid $FP_PID)..."
kill $FP_PID 2>/dev/null
wait $FP_PID 2>/dev/null
echo "Done."



