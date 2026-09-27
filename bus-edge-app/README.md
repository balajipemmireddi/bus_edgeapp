# Bus Edge App — Setup on Your Raspberry Pi

This is Phase 1 of the spec (§14): capture + local matching + state machine,
built and tested. Runs with what you already have: Pi + USB camera + screen.

## 1. Install dependencies on the Pi

`dlib` (which `face_recognition` needs) takes ages to compile from source on a
Pi. Use **piwheels** — Raspberry Pi OS should already point at it by default,
but if not:

```bash
pip install --extra-index-url https://www.piwheels.org/simple face_recognition opencv-python numpy --break-system-packages
```

This should take a few minutes, not hours — if it starts compiling from source
instead of pulling a prebuilt wheel, double check you're on 64-bit Raspberry Pi
OS (Bookworm) and piwheels is reachable.

## 2. Copy this `edge/` folder onto the Pi

Everything needed is in this folder: `db.py`, `state_machine.py`, `matcher.py`,
`enroll_student.py`, `main.py`, `test_core_logic.py`.

## 3. Run the tests first

```bash
cd edge
python3 -m pytest test_core_logic.py -v
```

All 11 should pass — this validates the state machine/dedup/geofence logic
independent of your specific camera hardware.

## 4. Enroll a test student

Either from photo files you already have:
```bash
python3 enroll_student.py --id child_001 --name "Test Student" \
  --bus bus_14 --pickup-stop stop_1 --drop-stop stop_1 \
  --photos photo1.jpg photo2.jpg photo3.jpg
```

Or live from your HP USB camera (opens a window, press SPACE to capture each
of 5 shots, ESC to cancel):
```bash
python3 enroll_student.py --id child_001 --name "Test Student" \
  --bus bus_14 --pickup-stop stop_1 --drop-stop stop_1 --live
```

## 5. Run the main app

```bash
python3 main.py --bus-id bus_14 --leg auto
```

This opens your USB camera, shows the live feed with a status bar on your
attached screen (green = event fired, amber = silent state update, orange =
sent to review queue, grey = watching for movement), and starts detecting.

- `--leg auto` uses the time-of-day default (before noon = AM/pickup, after =
  PM/drop) - pass `--leg AM` or `--leg PM` to force it for testing regardless
  of time of day.
- Press `q` in the video window to quit.

## What's NOT built yet (still Phase 1 scope, coming next)

- `sync_client.py` — pushing queued events to the cloud (Phase 3 in the build
  prompts file). Right now events just sit in the local `event_queue` table
  in `data/edge_local.db` — nothing gets sent anywhere yet.
- Real GPS integration — `main.py` currently passes `gps=None` everywhere.
  Once you have a GPS module, wire its lat/lng into the `run_burst_capture()`
  call and the geofence checks in `state_machine.py` will start doing real
  work instead of failing open.
- IR break-beam direction sensing — only the software bounding-box approach
  (§7.2) is implemented. Only add hardware if testing shows this isn't
  reliable enough.
- The IR/motion trigger is currently a cheap frame-difference check
  (`detect_motion` in `main.py`) - tune `MOTION_THRESHOLD` and
  `CAPTURE_COOLDOWN_SEC` once you see it running against your actual camera
  and door setup, lighting will affect the right value.

## Known limitation while testing without face_recognition installed

If you run `main.py` before installing `face_recognition`, it falls back to a
`MockBackend` that can't actually recognize real faces — you'll see it print a
warning. This lets you test the camera/motion/display pipeline before dlib is
fully installed, but real matching needs the real backend from step 1.
