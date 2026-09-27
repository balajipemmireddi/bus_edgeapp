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
nohup python3 face_processor.py --port 8095 > face_processor.log 2>&1 &
FP_PID=$!
echo "face_processor.py running (pid $FP_PID), logging to face_processor.log"
sleep 5

echo ""
echo "============================================"
echo "  Starting main.py (camera + recognition + auto-sync)"
echo "  Backend target: $BACKEND_URL"
echo "  If there's a display connected, video will appear below."
echo "  If not, check dashboard for live events:"
echo "    http://$BACKEND_IP:8000/dashboard"
echo "============================================"

# Set DISPLAY to :0 if running on a physical Pi with a monitor/HDMI
export DISPLAY=:0

python3 main.py --bus-id "$BUS_ID" --leg auto --backend-url "$BACKEND_URL" --display :0

# When main.py exits (Ctrl+C or window closed), also stop the face processor
echo "Stopping face_processor.py (pid $FP_PID)..."
kill $FP_PID 2>/dev/null
