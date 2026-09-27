# Event Detection Diagnostic Guide

Walk through these steps in order to find where events stop flowing.

---

## Step 1: Verify Student is in Roster

**On Pi Terminal 1 (edge app - should still be running):**

Look for this line during startup:
```
[SYNC] roster updated: X students
```

If it says `0 students`, the roster didn't sync. If it says a number, continue.

**Check that your enrolled student is actually there:**

```bash
sqlite3 ~/Desktop/bus-edge-app/data/edge_local.db "SELECT child_id, name FROM roster LIMIT 5;"
```

You should see your enrolled student. If not, the problem is **roster sync**, not event firing.

---

## Step 2: Walk in Front of Camera

**On Pi Terminal 1**, watch for these lines:

```
[DETECTION] Found 1 face(s) in frame 120
[MATCH] confidence=0.856, child_id=child_001, ambiguous=False
[STATE] child_001: direction=ENTERING, outcome=FIRE_PICKED_UP, status=NOT_PICKED_UP
[EVENT] John Doe: PICKED_UP (confidence=0.856)
```

**If you see these lines:** Go to **Step 3** (events queued locally).  
**If you see `[DETECTION] Found 0 face(s)`:** Camera isn't detecting faces → lighting issue or camera angle.  
**If you see `[MATCH] confidence=0.12`:** Face detected but confidence too low (below 0.45 threshold) → try different angles, better lighting.  
**If you see `[STATE] No event fired - outcome was DISCARD`:** State machine blocking it (direction/geofence) → go to **Step 4**.

---

## Step 3: Check Local Event Queue

Events are queued locally before syncing. Check if they're there:

```bash
sqlite3 ~/Desktop/bus-edge-app/data/edge_local.db "SELECT child_id, event_type, synced FROM event_queue ORDER BY id DESC LIMIT 5;"
```

Output should show:
```
child_001|PICKED_UP|0
```

The `0` means `synced=0` (not yet synced).

**If events are here:** Go to **Step 5** (sync to backend).  
**If events are NOT here:** The state machine didn't fire. Go to **Step 4**.

---

## Step 4: Debug State Machine Logic

Watch the console output more carefully. You should see:

```
[STATE] child_001: direction=ENTERING, outcome=FIRE_PICKED_UP, status=NOT_PICKED_UP
```

**Check each part:**

- **`direction=ENTERING` or `EXITING`?** 
  - ENTERING = moving toward camera (bounding box growing)
  - EXITING = moving away (bounding box shrinking)
  - Wrong direction = silently discarded (by design)
  
- **`outcome=`?**
  - `FIRE_PICKED_UP` = should fire an event ✓
  - `FIRE_DROPPED` = should fire an event ✓
  - `SILENT_UPDATE` = state changed but no event
  - `DISCARD` = rejected (direction/state/geofence mismatch)

- **`status=`?**
  - Should be `NOT_PICKED_UP` for PICKED_UP to fire
  - Should be `ON_BUS_TO_HOME` for DROPPED to fire
  - If it says `PICKED_UP` already, repeat sightings are silently discarded (by design)

**If `outcome=DISCARD`:**

Run this to see why:
```bash
sqlite3 ~/Desktop/bus-edge-app/data/edge_local.db "SELECT child_id, status FROM daily_state WHERE child_id='child_001';"
```

This shows the current state. If it says `PICKED_UP` already, the student already fired the event and repeat sightings are ignored (correct behavior — walk away and come back).

---

## Step 5: Check Sync to Backend

Events should sync every 30 seconds. Watch Pi Terminal 1 for:

```
[SYNC] events: 1 pushed, 0 queued
```

**If you see this:** Events were pushed. Go to **Step 6** (backend received them).  
**If you see `events: 0 pushed`:** No events queued (go back to Step 3).  
**If you see error like `HTTPConnectionPool` or `Connection refused`:** Backend not reachable.

---

## Step 6: Check Backend Event Storage

The backend stores events in its own database. SSH to Windows machine or check directly:

```bash
# On Windows, in backend folder:
sqlite3 data/backend.db "SELECT child_id, event_type, timestamp FROM events ORDER BY id DESC LIMIT 5;"
```

**If events are here:** Go to **Step 7** (dashboard displaying them).  
**If events are NOT here:** Backend API isn't storing them.

---

## Step 7: Check Dashboard

Open your browser and go to:
```
http://192.168.29.83:8000/dashboard
```

Click the **"Live Events"** tab. Scroll to the top — newest events should be there.

**If events appear:** ✅ Everything is working!  
**If events don't appear:** The `/api/live` endpoint might be broken.

---

## Quick Test: Manual Event Injection

If events aren't being created, inject one manually to test the pipeline:

```bash
# On Pi, inject a test event directly:
sqlite3 ~/Desktop/bus-edge-app/data/edge_local.db \
  "INSERT INTO event_queue (child_id, event_type, confidence, gps_lat, gps_lng, bus_id, timestamp) 
   VALUES ('child_001', 'PICKED_UP', 0.95, NULL, NULL, 'bus_14', datetime('now'));"

# Then trigger sync:
python3 sync_client.py --backend-url http://192.168.29.83:8000 --bus-id bus_14 --loop
```

Then check the dashboard. If a manually-injected event appears but camera-detected events don't, the problem is camera→detection→state_machine, not sync→backend.

---

## Checklist Summary

- [ ] Roster synced (shows enrolled students)
- [ ] Camera detects face (logs show `[DETECTION]`)
- [ ] Face matches student (logs show `[MATCH] confidence=X.XXX`)
- [ ] State machine fires event (logs show `[EVENT]`)
- [ ] Event queued locally (check event_queue table)
- [ ] Event synced to backend (logs show `[SYNC] events: 1 pushed`)
- [ ] Backend stored event (check backend.db events table)
- [ ] Dashboard displays event (Live Events tab)

Check these top-to-bottom. First one that fails is the problem.
