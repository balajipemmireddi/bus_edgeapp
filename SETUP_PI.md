# Pi Edge App - Complete Setup Guide

## Problem: IP Address Keeps Changing

Your Pi's IP keeps changing (e.g., 192.168.29.220 → 192.168.29.100). This breaks the hardcoded connection.

**Solution:** We now use mDNS (Multicast DNS) to auto-discover the backend by hostname instead of hardcoding IP.

---

## Step 1: Find Your Windows Hostname

**On Windows (Command Prompt):**
```cmd
hostname
```

You'll see something like: `DESKTOP-ABC123`

---

## Step 2: Configure the Pi App

**On Pi (via SSH):**

Edit the config file:
```bash
cd ~/Desktop/bus-edge-app/bus-edge-app
nano config.yaml
```

Update these two values:

```yaml
backend_hostname: "DESKTOP-ABC123"    # ← your Windows hostname from Step 1
backend_ip: "192.168.29.83"           # ← your current Windows IP (as fallback)
```

Save: `Ctrl+O` → Enter → `Ctrl+X`

---

## Step 3: Install mDNS Library (Optional but Recommended)

This enables automatic hostname discovery. If skipped, the app falls back to hardcoded IP.

```bash
pip install zeroconf
```

---

## Step 4: Install Dependencies

```bash
cd ~/Desktop/bus-edge-app/bus-edge-app
bash install_dependencies.sh
```

This installs:
- `face_recognition` (dlib - face detection)
- `opencv-python` (camera)
- `numpy` (math)

---

## Step 5: Start the App

```bash
bash start_edge.sh
```

You should see:
```
[IP_DISCOVERY] Attempting mDNS lookup for DESKTOP-ABC123.local...
[IP_DISCOVERY] ✓ Found backend at http://192.168.X.X:8000
[BACKEND] ✓ Using RealBackend (face_recognition with dlib)
[SYNC] Background sync started: http://192.168.X.X:8000
[HEARTBEAT] bus_14 online
[SYNC] roster updated: 6 students
[DEBUG] Waiting for faces in camera...
```

---

## Step 6: Test Event Detection

1. Walk in front of camera
2. Look for logs:
   ```
   [DETECTION] Found 1 face(s) in frame 45
   [MATCH] confidence=0.87, child_id=child_001
   ✓ [EVENT FIRED] PICKED_UP
   ```
3. Check dashboard: `http://192.168.29.83:8000/dashboard`
4. Event should appear in "Live Events" tab within 30 seconds

---

## If IP Changes Again

The beauty of this setup: **you don't need to change anything!**

- If Pi IP changes: mDNS will automatically find the backend by hostname
- If Windows IP changes: mDNS will automatically find it
- If mDNS fails: it falls back to the IP in `config.yaml`
- No manual updates needed!

---

## Troubleshooting

### "mDNS lookup failed"
- Make sure `zeroconf` is installed: `pip install zeroconf`
- Or manually update `config.yaml` with your Windows IP

### "Backend not reachable"
- Check Windows is running: `ping 192.168.29.83` from Pi
- Update the fallback IP in `config.yaml`
- Make sure both are on same WiFi network

### "No faces detected"
- Run: `python3 -c "import face_recognition; print('OK')"`
- If error: run `install_dependencies.sh` again
- Check lighting (need bright room)
- Check camera angle (face should be centered)

### "Still getting segfault"
- This was fixed in latest version
- Pull latest: `git pull origin main`

---

## One-Time Setup Checklist

- [ ] Found Windows hostname (Step 1)
- [ ] Updated `config.yaml` with hostname and IP (Step 2)
- [ ] Installed `zeroconf` (Step 3)
- [ ] Ran `install_dependencies.sh` (Step 4)
- [ ] Verified `face_recognition` works: `python3 -c "import face_recognition; print('OK')"`
- [ ] Ran `bash start_edge.sh` successfully
- [ ] Walked in front of camera and saw `[DETECTION]` logs
- [ ] Event appeared in dashboard within 30 seconds

---

## Architecture (How It Works)

```
Pi Edge App (192.168.29.220)
    ↓ (uses mDNS to find)
    ↓
Windows Backend (DESKTOP-ABC123 → resolves to 192.168.29.83:8000)
    ↓ (syncs events every 30s)
    ↓
Dashboard (displays PICKED_UP/DROPPED)
```

If Pi IP changes to 192.168.29.100 → still finds backend (mDNS)
If Windows IP changes to 192.168.29.50 → still finds backend (mDNS)
No code changes needed!

---

## Support

If something doesn't work:

1. Check logs on Pi: `tail -f ~/Desktop/bus-edge-app/bus-edge-app/face_processor.log`
2. Check backend dashboard: `http://192.168.29.83:8000/dashboard`
3. Verify connectivity: `ping 192.168.29.83` from Pi
4. Reinstall: `bash install_dependencies.sh`

