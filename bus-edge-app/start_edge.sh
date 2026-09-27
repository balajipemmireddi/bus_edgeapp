#!/bin/bash
# One-command startup for the Pi. Run: bash start_edge.sh
# Edit BACKEND_IP below to match your Windows backend machine's actual IP.

BACKEND_IP="192.168.29.83"
BACKEND_URL="http://${BACKEND_IP}:8000"
BUS_ID="bus_14"

# Auto-detect DISPLAY - try common values
if [ -z "$DISPLAY" ]; then
  for d in :0 :1 :2; do
    if [ -S "/tmp/.X11-unix/$(echo $d | tr -d ':')" ]; then
      export DISPLAY=$d
      break
    fi
  done
fi

# If still no DISPLAY, warn but don't fail - run in headless mode
if [ -z "$DISPLAY" ]; then
  echo "[WARN] No X display found. Running in headless mode (no video window on Pi)."
  echo "       If using remote desktop, open a terminal FROM the remote desktop and run:"
  echo "       cd ~/Desktop/bus-edge-app && bash start_edge.sh"
  export DISPLAY=""
fi

echo "============================================"
echo "  Starting face_processor.py in the background"
echo "  (this is what actually runs dlib - the backend delegates to it)"
echo "============================================"
nohup python3 face_processor.py --port 8095 > face_processor.log 2>&1 &
FP_PID=$!
echo "face_processor.py running (pid $FP_PID), logging to face_processor.log"
sleep 5

echo ""
echo "============================================"
echo "  Starting main.py (camera + recognition + auto-sync)"
echo "  Backend target: $BACKEND_URL"
if [ -n "$DISPLAY" ]; then
  echo "  Display: $DISPLAY (video window will show on your screen)"
else
  echo "  Display: None (running headless - no video window)"
fi
echo "============================================"

if [ -n "$DISPLAY" ]; then
  DISPLAY=$DISPLAY python3 main.py --bus-id "$BUS_ID" --leg auto --backend-url "$BACKEND_URL"
else
  python3 main.py --bus-id "$BUS_ID" --leg auto --backend-url "$BACKEND_URL" 2>&1 | tee main.log
fi

# When main.py exits (Ctrl+C or window closed), also stop the face processor
echo "Stopping face_processor.py (pid $FP_PID)..."
kill $FP_PID 2>/dev/null
