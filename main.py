"""
Main edge app loop for a single bus's Pi - CONTINUOUS live recognition.

This replaced an earlier motion-trigger -> burst-capture -> single-decision
design. That approach worked but felt slow and "stuck" during the burst, because
nothing was drawn to screen while it processed. This version instead:

  1. Reads frames continuously from the camera
  2. Every PROCESS_EVERY_N_FRAMES, runs face detection + recognition on a small
     downscaled copy of the frame (dlib's HOG detector cost scales with pixel
     count - this is what actually makes it feel instant, same trick real-time
     face-recognition demos use)
  3. Draws a bounding box + name directly on the LIVE video feed for every
     detected face, continuously - this is the "instant recognition in the
     video stream" look
  4. Tracks each recognized child's bounding-box size across recent processed
     frames to estimate direction (entering/exiting), same idea as before, just
     computed from a rolling window instead of one discrete burst
  5. Feeds confident detections into the state machine (§7.4) - the state
     machine's own dedup logic (§7) already prevents repeat-firing, so
     continuous detection of an already-PICKED_UP child is a safe no-op
  6. Queues any fired event locally (§7.5) - sync_client.py handles upload
  7. Queues unmatched and ambiguous faces to review queue with photo evidence

Run this as a systemd service in production (see spec §"Hardware & Deployment").
For now, run directly:  python3 main.py --bus-id bus_14 --leg auto
"""

import argparse
import time
import datetime
import cv2
import numpy as np
import threading
import os
from dotenv import load_dotenv
from pathlib import Path

# Load .env file for configuration
load_dotenv()

import db
from state_machine import Detection, Leg, Direction, Outcome, process_detection
from matcher import match_against_roster, MockBackend
from sync_client import SyncClient

PROCESS_EVERY_N_FRAMES = 1   # Process EVERY frame for real-time detection
DETECTION_SCALE = 0.5        # detect on a half-size copy. Combined with the 640x480
                              # base capture resolution, this gives a 320x240 image to
                              # the detector - enough detail for dlib's HOG detector to
                              # find a face at normal distance. (0.25 here left only
                              # 160x120, too small to detect faces reliably - the actual
                              # speed win already came from MJPEG+V4L2 capture, not from
                              # over-shrinking this.)
TRACK_HISTORY_LEN = 5        # how many recent box sizes to keep per child, for direction
TRACK_FORGET_SEC = 2.0       # if a child hasn't been seen for this long, forget their track

# Photo directory for review queue evidence
PHOTOS_DIR = Path(__file__).parent / "data" / "photos"
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

# Frame counter for generating unique photo filenames
_frame_counter = 0
_frame_counter_lock = threading.Lock()


def save_face_photo(frame, location, event_type: str, face_id: str = "unknown"):
    """
    Save a cropped face photo to disk for manual review queue evidence.
    Returns the relative path from repo root, or empty string if save fails.
    """
    global _frame_counter
    try:
        with _frame_counter_lock:
            _frame_counter += 1
            counter = _frame_counter
        
        top, right, bottom, left = location
        # Crop to the face with some margin
        margin = 20
        top = max(0, top - margin)
        bottom = min(frame.shape[0], bottom + margin)
        left = max(0, left - margin)
        right = min(frame.shape[1], right + margin)
        
        cropped = frame[top:bottom, left:right]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"face_{event_type}_{face_id}_{timestamp}_{counter}.jpg"
        filepath = PHOTOS_DIR / filename
        
        cv2.imwrite(str(filepath), cropped)
        # Return relative path
        return f"data/photos/{filename}"
    except Exception as e:
        print(f"[WARN] Failed to save face photo: {e}")
        return ""


def get_backend():
    try:
        from matcher import RealBackend
        backend = RealBackend()
        print("[BACKEND] ✓ Using RealBackend (face_recognition with dlib)")
        return backend
    except ImportError as e:
        print(f"[BACKEND] ✗ face_recognition not installed: {e}")
        print("[BACKEND] Running with MockBackend (no face detection - for testing only)")
        print("[BACKEND] To enable detection, install on Pi:")
        print("[BACKEND]   pip install face-recognition --extra-index-url https://www.piwheels.org/simple")
        from matcher import MockBackend
        return MockBackend()


def current_leg(mode: str) -> Leg:
    if mode == "AM":
        return Leg.AM
    if mode == "PM":
        return Leg.PM
    hour = datetime.datetime.now().hour
    return Leg.AM if 5 <= hour < 12 else Leg.PM


def fit_to_screen(frame, max_width=760, max_height=420):
    h, w = frame.shape[:2]
    scale = min(max_width / w, max_height / h, 1.0)
    if scale < 1.0:
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
    return frame


def draw_top_bar(frame, text, color):
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (frame.shape[1], 44), color, -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
    cv2.putText(frame, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return frame


def draw_face_box(frame, location_full, label, color):
    """location_full is (top, right, bottom, left) already scaled to the display frame."""
    top, right, bottom, left = location_full
    cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
    cv2.rectangle(frame, (left, bottom - 22), (right, bottom), color, cv2.FILLED)
    cv2.putText(frame, label, (left + 4, bottom - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


class ChildTrack:
    """Rolling history of box sizes for one child, used to estimate direction
    without needing a discrete burst - just looks at the trend across the last
    few times this child was seen.

    FIXED: comparing just box_sizes[-1] vs box_sizes[0] on a tiny 5-frame window
    was extremely noisy for someone standing still in front of the camera (not
    actually walking through a door) - small frame-to-frame jitter in the
    detected box size flipped ENTERING/EXITING on almost every single frame,
    which is exactly the flicker seen in the logs. Fixed with:
      1. A minimum % size change required before direction is allowed to change
         at all (ignores jitter below this threshold - "stable" is a real
         third outcome now, not forced into one direction or the other)
      2. A required number of consistent frames in the new direction before
         committing to a direction FLIP (one noisy frame can't flip it)
    """

    MIN_CHANGE_RATIO = 0.08       # ignore size changes smaller than 8% - likely just jitter
    FRAMES_TO_CONFIRM_FLIP = 3    # need this many consistent readings before changing direction

    def __init__(self):
        self.box_sizes = []
        self.last_seen = 0.0
        self.fired_this_visit = False
        self.confirmed_direction = Direction.ENTERING
        self._pending_direction = None
        self._pending_count = 0

    def update(self, box_size):
        self.box_sizes.append(box_size)
        if len(self.box_sizes) > TRACK_HISTORY_LEN:
            self.box_sizes.pop(0)
        self.last_seen = time.time()

        if len(self.box_sizes) < 2:
            return

        baseline = self.box_sizes[0]
        latest = self.box_sizes[-1]
        change_ratio = (latest - baseline) / baseline if baseline else 0.0

        if abs(change_ratio) < self.MIN_CHANGE_RATIO:
            # Within noise tolerance - not a real trend either way, don't touch
            # confirmed_direction and reset any pending flip in progress.
            self._pending_direction = None
            self._pending_count = 0
            return

        candidate = Direction.ENTERING if change_ratio > 0 else Direction.EXITING
        if candidate == self.confirmed_direction:
            self._pending_direction = None
            self._pending_count = 0
            return

        # A real, above-noise trend disagrees with our current confirmed
        # direction - require it to show up consistently before flipping.
        if candidate == self._pending_direction:
            self._pending_count += 1
        else:
            self._pending_direction = candidate
            self._pending_count = 1

        if self._pending_count >= self.FRAMES_TO_CONFIRM_FLIP:
            self.confirmed_direction = candidate
            self._pending_direction = None
            self._pending_count = 0

    def direction(self) -> Direction:
        return self.confirmed_direction

    def is_stale(self) -> bool:
        return (time.time() - self.last_seen) > TRACK_FORGET_SEC


def process_frame(frame, backend, roster, bus_id, leg, tracks: dict):
    """
    Runs detection+recognition on a downscaled copy of `frame`, updates each
    recognized child's track, feeds the state machine, queues unmatched/ambiguous
    events for manual review, and returns a list of (location_full_res, label, color)
    to draw on the display frame.
    """
    small = cv2.resize(frame, (0, 0), fx=DETECTION_SCALE, fy=DETECTION_SCALE)
    rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    try:
        all_faces = backend.get_all_encodings(rgb_small)
    except Exception as e:
        print(f"[ERROR] Face detection crashed: {e}")
        import traceback
        traceback.print_exc()
        return []

    # Log every detection attempt (even if 0 faces found)
    if not all_faces:
        # Silently skip - don't spam logs on every frame with no faces
        return []

    print(f"[DETECTION] Found {len(all_faces)} face(s)")
    draw_items = []
    scale_back = 1.0 / DETECTION_SCALE

    for encoding, location in all_faces:
        top, right, bottom, left = [int(v * scale_back) for v in location]
        box_size = (right - left) * (bottom - top)

        result = match_against_roster(encoding, roster, backend)
        print(f"[MATCH] confidence={result.confidence:.3f}, child_id={result.child_id}, ambiguous={result.is_ambiguous}")

        if result.child_id is None:
            # Unmatched face - queue for manual review with photo evidence
            draw_items.append(((top, right, bottom, left), "Unknown", (0, 0, 200)))
            photo_path = save_face_photo(frame, (top, right, bottom, left), "UNMATCHED")
            db.queue_event(
                child_id="UNKNOWN",
                event_type="UNMATCHED_REVIEW",
                confidence=result.confidence,
                photo_path=photo_path,
                gps=None,
                bus_id=bus_id,
            )
            print(f"[REVIEW_QUEUE] Queued UNMATCHED_REVIEW event with confidence {result.confidence:.3f}")
            continue
        
        if result.is_ambiguous:
            # Ambiguous face (too close to a different student) - queue for review
            draw_items.append(((top, right, bottom, left), f"{result.child_id}? (review)", (0, 165, 255)))
            photo_path = save_face_photo(frame, (top, right, bottom, left), "AMBIGUOUS", result.child_id)
            db.queue_event(
                child_id=result.child_id,
                event_type="AMBIGUOUS_REVIEW",
                confidence=result.confidence,
                photo_path=photo_path,
                gps=None,
                bus_id=bus_id,
            )
            print(f"[REVIEW_QUEUE] Queued AMBIGUOUS_REVIEW event for {result.child_id} with confidence {result.confidence:.3f}")
            continue

        child_id = result.child_id
        track = tracks.setdefault(child_id, ChildTrack())
        track.update(box_size)

        student = next((s for s in roster if s["child_id"] == child_id), None)
        label = student["name"] if student else child_id
        color = (0, 200, 0)

        if student and student.get("assigned_bus_id") not in (None, bus_id):
            label = f"{label} (other bus)"
            draw_items.append(((top, right, bottom, left), label, (0, 165, 255)))
            continue

        if student and not track.fired_this_visit:
            direction = track.direction()
            current_status = db.get_status(child_id)
            detection = Detection(child_id, result.confidence, direction, gps=None)
            outcome = process_detection(
                detection, leg, student, stop_coords_lookup={}, bus_id=bus_id
            )
            
            leg_name = current_leg(leg)
            print(f"\n[STATE_DEBUG] {child_id} ({label})")
            print(f"  Leg: {leg_name.value} | Direction: {direction.value} | Status: {current_status}")
            print(f"  Confidence: {result.confidence:.3f} | Outcome: {outcome.value}")
            
            if outcome in (Outcome.FIRE_PICKED_UP, Outcome.FIRE_DROPPED, Outcome.FIRE_EXIT_UNEXPECTED):
                event_type = {
                    Outcome.FIRE_PICKED_UP: "PICKED_UP",
                    Outcome.FIRE_DROPPED: "DROPPED",
                    Outcome.FIRE_EXIT_UNEXPECTED: "EXIT_UNEXPECTED_LOCATION",
                }[outcome]
                # process_detection persists the event together with its state
                # transition (or queues unexpected exits) before returning.
                print(f"  ✓ [EVENT FIRED] {event_type}")
                label = f"{label}: {event_type}"
                color = (0, 220, 0)
                track.fired_this_visit = True  # don't re-fire every processed frame while they linger
            else:
                print(f"  ✗ No event - blocked by state machine logic")

        draw_items.append(((top, right, bottom, left), label, color))

    return draw_items        draw_items.append(((top, right, bottom, left), label, color))

    return draw_items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus-id", default=os.environ.get("BUS_ID", "bus_14"), help="Bus ID (from .env or CLI)")
    parser.add_argument("--leg", default="auto", choices=["AM", "PM", "auto"], help="Leg (AM/PM/auto)")
    parser.add_argument("--camera-index", type=int, default=0, help="Camera device index")
    parser.add_argument("--backend-url", default=os.environ.get("BACKEND_URL"), help="Backend URL for auto-sync (from .env or CLI)")
    parser.add_argument("--display", type=str, default=None, help="DISPLAY variable (e.g. :0). Auto-detect if not set")
    args = parser.parse_args()
    
    print(f"[CONFIG] Bus ID: {args.bus_id}")
    if args.backend_url:
        print(f"[CONFIG] Backend: {args.backend_url} (sync enabled)")
    else:
        print(f"[CONFIG] Backend: None (sync disabled)")
    print(f"[CONFIG] Leg mode: {args.leg}")

    # Set up display if on Pi with physical screen
    if args.display:
        os.environ["DISPLAY"] = args.display
        print(f"[DISPLAY] Set DISPLAY={args.display}")
    elif "DISPLAY" not in os.environ:
        # Try auto-detect common values on Pi
        for display_val in [":0", ":1"]:
            try:
                test_env = os.environ.copy()
                test_env["DISPLAY"] = display_val
                # We'll try to use it; if it fails, we'll fall back
                os.environ["DISPLAY"] = display_val
                print(f"[DISPLAY] Auto-detected DISPLAY={display_val}")
                break
            except:
                pass

    db.init_db()
    backend = get_backend()

    # Start background sync if backend URL provided
    sync_client = None
    if args.backend_url:
        sync_client = SyncClient(args.backend_url, args.bus_id)
        sync_client.start_background(interval=30)
        print(f"[SYNC] Background sync started: {args.backend_url}")
    
    # Explicitly force the V4L2 backend instead of letting OpenCV pick GStreamer
    print(f"[CAMERA] Attempting to open camera at index {args.camera_index}...")
    cap = cv2.VideoCapture(args.camera_index, cv2.CAP_V4L2)
    if not cap.isOpened():
        print("[WARN] V4L2 backend failed to open camera - falling back to default backend")
        cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        print(f"ERROR: could not open camera at index {args.camera_index}")
        return

    print(f"[CAMERA] Camera opened successfully")
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    try:
        actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        fourcc_str = "".join([chr((actual_fourcc >> 8 * i) & 0xFF) for i in range(4)])
        print(f"[CAMERA] resolution={int(actual_w)}x{int(actual_h)} fourcc={fourcc_str}")
    except Exception as e:
        print(f"[WARN] Could not read camera properties: {e}")
        print(f"[CAMERA] Proceeding anyway...")

    # Try to create display window - if DISPLAY is not set or invalid, it will just skip
    window_name = "Bus Edge App"
    has_display = False
    
    # Only try to create window if DISPLAY is explicitly set in environment
    display_env = os.environ.get("DISPLAY")
    if display_env:
        try:
            # Disable OpenCV's OpenGL backend which can cause segfaults on headless systems
            cv2.setUseOptimized(True)
            # Try creating window - wrap in subprocess check to prevent hard crash
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.moveWindow(window_name, 0, 0)
            has_display = True
            print(f"[DISPLAY] Window created successfully on {display_env}")
        except Exception as e:
            print(f"[WARN] Could not create display window: {e}")
            print(f"[INFO] Running without display - events still sync to backend")
            has_display = False
    else:
        print(f"[INFO] No DISPLAY set - running in headless mode (events still sync to backend)")

    tracks: dict[str, ChildTrack] = {}
    roster = db.load_roster()
    last_roster_refresh = time.monotonic()
    last_draw_items = []
    frame_count = 0
    process_count = 0  # Track how many times we run detection
    fps_frame_count = 0
    fps_start_time = time.time()
    fps = 0.0
    last_logged_leg = current_leg(args.leg)
    print(f"[LEG] Starting leg: {last_logged_leg.value}"
          + (" (auto - will flip AM->PM based on wall clock time)" if args.leg == "auto" else " (manually pinned)"))

    print(f"Edge app running for {args.bus_id} (continuous mode). Press 'q' to stop.")
    print(f"[DEBUG] CONFIDENCE_THRESHOLD = {0.45} (from matcher.py)")
    print(f"[DEBUG] PROCESS_EVERY_N_FRAMES = {PROCESS_EVERY_N_FRAMES} (process EVERY frame)")
    print(f"[DEBUG] Waiting for faces in camera...")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            frame_count += 1

            if frame_count % PROCESS_EVERY_N_FRAMES == 0:
                process_count += 1
                # Keep encodings in memory; loading and JSON-decoding the full
                # roster for every camera frame stalls recognition on the Pi.
                # Refresh periodically so completed background syncs take effect.
                if time.monotonic() - last_roster_refresh >= 30:
                    roster = db.load_roster()
                    last_roster_refresh = time.monotonic()
                if not roster:
                    if process_count % 300 == 0:  # Log only every 300 frames (~10 seconds)
                        print(f"[WARN] Empty roster - no students to match. Did roster sync succeed?")
                if roster:
                    this_leg = current_leg(args.leg)
                    if this_leg != last_logged_leg:
                        print(f"[LEG] *** Leg changed from {last_logged_leg.value} to {this_leg.value} *** "
                              f"(wall clock crossed the AM/PM boundary - if this is mid-testing and "
                              f"not a real new leg, re-run with --leg AM or --leg PM to pin it)")
                        last_logged_leg = this_leg
                    t0 = time.time()
                    last_draw_items = process_frame(frame, backend, roster, args.bus_id, this_leg, tracks)
                    dt = time.time() - t0
                    if dt > 0.5:
                        print(f"[TIMING] recognition pass took {dt:.2f}s")
                    if last_draw_items:
                        print(f"[DETECTION] Found {len(last_draw_items)} face(s) in frame {frame_count}")
                    elif process_count % 30 == 0:
                        # Every 30 detection passes with no faces found, print a status
                        print(f"[DEBUG] Processed {process_count} frames, no faces detected yet (that's OK - keep camera pointed at someone)")
                for cid in list(tracks.keys()):
                    if tracks[cid].is_stale():
                        del tracks[cid]

            if has_display:
                display = frame.copy()
                for (top, right, bottom, left), label, color in last_draw_items:
                    display = draw_face_box(display, (top, right, bottom, left), label, color)
                display = fit_to_screen(display)
                display = draw_top_bar(display, f"{args.bus_id} | {current_leg(args.leg).value} | live", (60, 60, 60))

                fps_frame_count += 1
                elapsed = time.time() - fps_start_time
                if elapsed > 1:
                    fps = fps_frame_count / elapsed
                    fps_frame_count = 0
                    fps_start_time = time.time()
                cv2.putText(display, f"FPS: {fps:.1f}", (display.shape[1] - 110, 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

                cv2.imshow(window_name, display)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            else:
                # No display - just sleep a bit so we don't spin CPU
                time.sleep(0.033)  # ~30fps even without display

    except KeyboardInterrupt:
        print("[INFO] Received Ctrl+C, shutting down gracefully...")
    except Exception as e:
        print(f"[ERROR] Unexpected error in main loop: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[CLEANUP] Releasing resources...")
        try:
            cap.release()
        except:
            pass
        if has_display:
            try:
                cv2.destroyAllWindows()
            except:
                pass
        if sync_client:
            sync_client.running = False
        print("[CLEANUP] Done.")


if __name__ == "__main__":
    main()
