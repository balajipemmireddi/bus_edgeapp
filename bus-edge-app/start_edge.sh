#!/bin/bash
# One-command startup for the Pi. Run: bash start_edge.sh
# Edit BACKEND_IP below to match your Windows backend machine's actual IP.

BACKEND_IP="192.168.29.83"
BACKEND_URL="http://${BACKEND_IP}:8000"
BUS_ID="bus_14"

echo "============================================"
echo "  Starting face_processor.py in the background"
echo "  (this is what actually runs dlib - the backend delegates to it)"
echo "============================================"

# Kill any old face_processor processes first
pkill -f "face_processor.py" 2>/dev/null
sleep 1

# Start face_processor in background with proper logging
python3 face_processor.py --port 8095 > face_processor.log 2>&1 &
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

# Test if face_processor is responding
if ! curl -s http://localhost:8095/health > /dev/null 2>&1; then
    echo "[WARN] face_processor.py not responding on port 8095"
    echo "       Check face_processor.log for errors"
fi

echo ""
echo "============================================"
echo "  Starting main.py (camera + recognition + auto-sync)"
echo "  Backend target: $BACKEND_URL"
echo "  Running in HEADLESS mode (no video window)"
echo "  Events will sync to backend every 30 seconds"
echo "  Check dashboard: http://$BACKEND_IP:8000/dashboard"
echo "  Press Ctrl+C to stop (this will also stop face_processor)"
echo "============================================"

# Run WITHOUT display to avoid segfault on headless Pi
# Events still fire and sync - just no video window
python3 main.py --bus-id "$BUS_ID" --leg auto --backend-url "$BACKEND_URL"

# When main.py exits (Ctrl+C or crash), also stop the face processor
echo ""
echo "Stopping face_processor.py (pid $FP_PID)..."
kill $FP_PID 2>/dev/null
wait $FP_PID 2>/dev/null
echo "Done."

