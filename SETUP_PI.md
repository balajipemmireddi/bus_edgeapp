# Bus System - Complete Setup Guide

## Architecture: Backend-Centric Design

```
Backend (Windows) - Central Authority
├── All student data (CRUD operations)
├── Management dashboard (view/edit/delete)
├── Event storage & live dashboard
└── Roster sync API

Pi (Edge Device) - Read-Only Worker
├── Face detection & recognition (local)
├── Event queue (fires PICKED_UP/DROPPED locally)
└── Auto-syncs events to backend every 30s
```

**Key Benefit:** If Pi fails, all student data and management remains on Windows. Pi just syncs roster every 30s and caches locally for fast detection.

---

## Step 1: Find Your Windows Hostname

**On Windows (Command Prompt):**
```cmd
hostname
```

Example output: `DESKTOP-ABC123`

---

## Step 2: Configure Pi Connection

**On Pi (SSH Terminal):**

Edit config file:
```bash
cd ~/Desktop/bus-edge-app/bus-edge-app
nano config.yaml
```

Update these values:

```yaml
backend_hostname: "DESKTOP-ABC123"    # ← your Windows hostname from Step 1
backend_ip: "192.168.1.72"            # ← your Windows IP (fallback)
backend_port: 8000
bus_id: "bus_14"
leg: "auto"
```

Save: `Ctrl+O` → Enter → `Ctrl+X`

---

## Step 3: Install Dependencies on Pi

```bash
cd ~/Desktop/bus-edge-app/bus-edge-app
bash install_dependencies.sh
```

Installs:
- `face_recognition` (dlib face detection)
- `opencv-python` (camera capture)
- `zeroconf` (auto IP discovery)

---

## Step 4: Start Backend on Windows

**Command Prompt:**
```cmd
cd c:\Users\balaj\Documents\projects\SFace\bus-backend\bus-backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Wait for:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

---

## Step 5: Start Pi Edge App

**Pi Terminal 1:**
```bash
cd ~/Desktop/bus-edge-app/bus-edge-app
bash start_edge.sh
```

Expected output:
```
[BACKEND] ✓ Using RealBackend (face_recognition with dlib)
[SYNC] Background sync started: http://192.168.1.72:8000
[HEARTBEAT] bus_14 online
[SYNC] roster updated: 6 students
[DEBUG] Waiting for faces in camera...
```

---

## Dashboard Access Points

| Tool | URL | Purpose |
|------|-----|---------|
| **Management** | `http://192.168.1.72:8000/management` | View, edit, delete students (CENTRAL) |
| **Live Events** | `http://192.168.1.72:8000/dashboard` | See PICKED_UP/DROPPED events |
| **Enrollment** | `http://<pi-ip>:8090` | Upload photos & enroll new students |

---

## How Everything Works

### 1. Enroll a New Student
```
You open: http://<pi-ip>:8090
  ↓
Upload photos from phone/camera
  ↓
Pi processes: dlib detects face → creates encoding
  ↓
Backend stores: student + encodings in database
  ↓
Every Pi syncs: pulls fresh roster every 30s
  ↓
Next detection: face matched → event fires → dashboard updates
```

### 2. Manage Students (BACKEND ONLY)
```
You open: http://192.168.1.72:8000/management
  ↓
View all students (sorted by name)
  ↓
Edit: change name, bus, stops (data stays on backend)
  ↓
Delete: removes from all Pis on next sync
  ↓
Export: download JSON backup
```

### 3. View Live Events
```
You open: http://192.168.1.72:8000/dashboard
  ↓
See PICKED_UP/DROPPED in real-time
  ↓
Events from any Pi auto-sync every 30s
  ↓
Sorted by timestamp (newest first)
```

---

## Resilience Design

### Pi Fails
- ✓ Backend still has all data
- ✓ Management dashboard still works
- ✓ Can add/edit/delete students
- ✓ When Pi restarts: syncs roster, resumes detection

### Backend Fails
- ✓ Pi still detects faces locally
- ✓ Pi still fires events, queues them
- ✓ When backend restarts: Pi auto-syncs events
- ✓ No data loss (queued locally)

### Network Issues
- ✓ Pi retries sync every 30s
- ✓ Backend falls back to hardcoded IP if mDNS fails
- ✓ Events queue locally until sync succeeds
- ✓ Configurable IP in `config.yaml`

---

## Multi-Pi Setup

If running multiple Pis (multiple buses):

**Each Pi:**
```bash
# Update for each bus
backend_ip: "192.168.1.72"      # Same backend for all
bus_id: "bus_14"                # Different per Pi
bus_id: "bus_15"                # Another bus
```

**All Pis:**
- Pull same roster from backend
- All sync events to backend
- All show on one dashboard

**Backend:**
- Single database for all buses
- One management dashboard for all students
- Events tagged by bus_id

---

## Troubleshooting

### Backend Not Reachable from Pi
```bash
# From Pi, test:
ping 192.168.1.72
# Should respond immediately

# Check if backend is running:
# (On Windows, make sure you ran: python -m uvicorn main:app)
```

### No Face Detection
```bash
# Check face_recognition is installed:
python3 -c "import face_recognition; print('OK')"

# Check lighting: need bright room, face centered in camera
# Check camera: ls /dev/video*
```

### Students Not Syncing to Pi
```bash
# Check backend has students:
# Open: http://192.168.1.72:8000/management

# Wait 30s for sync
# Check Pi logs: look for [SYNC] roster updated
```

### Want to Delete All Students
```bash
# Option 1: Delete one-by-one via management dashboard
# Open: http://192.168.1.72:8000/management → click Delete

# Option 2: Reset backend database
# On Windows, delete: c:\Users\balaj\Documents\projects\SFace\bus-backend\bus-backend\data\backend.db
# Backend recreates empty DB on next startup
```

---

## Key Design Principles

✓ **Backend is the source of truth** - all data lives here
✓ **Pi is read-only for roster** - syncs every 30s, can't modify students locally
✓ **Events sync automatically** - no manual intervention
✓ **Resilient to failures** - either Pi or backend can fail independently
✓ **Horizontally scalable** - add more Pis, all sync to same backend

---

## One-Time Setup Checklist

- [ ] Windows hostname found
- [ ] `config.yaml` updated on Pi
- [ ] Backend started on Windows (port 8000)
- [ ] `install_dependencies.sh` ran successfully on Pi
- [ ] `bash start_edge.sh` running on Pi
- [ ] Opened `http://192.168.1.72:8000/management` and see student list
- [ ] Opened `http://<pi-ip>:8090` and enrolled a test student
- [ ] Walked in front of camera, saw `[DETECTION]` logs
- [ ] Event appeared in `http://192.168.1.72:8000/dashboard` within 30s
- [ ] Deleted test student via management dashboard, verified Pi synced

---

## Production Deployment Notes

When ready for production:

1. **Database:** Switch backend from SQLite to PostgreSQL (§4 of spec)
   - Just change connection string in `main.py`
   - Schema stays the same

2. **Security:** Add authentication to dashboard
   - Add JWT tokens or password protection
   - Enroll endpoint should require API key

3. **Multiple Buses:** Scale horizontally
   - Keep backend on Windows
   - Add more Pis, each with unique `bus_id`
   - All sync to same backend

4. **Backup:** Export students regularly
   - Via management dashboard: "📥 Export All"
   - Stores JSON with all encodings
   - Can restore to any backend

---

## Support

**Check logs where you started the process:**
- Edge app: Terminal where you ran `bash start_edge.sh`
- Backend: Command Prompt where you ran `python -m uvicorn main:app`

**Dashboards:**
- **Management:** `http://192.168.1.72:8000/management`
- **Events:** `http://192.168.1.72:8000/dashboard`
- **Enrollment:** `http://<pi-ip>:8090`
