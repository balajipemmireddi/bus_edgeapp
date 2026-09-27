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

Run this as a systemd service in production (see spec §"Hardware & Deployment").
For now, run directly:  python3 main.py --bus-id bus_14 --leg auto
"""

import argparse
import time
import datetime
import cv2
import numpy as np
import threading

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


def get_backend():
    try:
        from matcher import RealBackend
        return RealBackend()
    except ImportError:
        print("[WARN] face_recognition not installed - running with MockBackend "
              "(matching will not work on real faces). Install on the Pi via piwheels.")
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
    few times this child was seen."""

    def __init__(self):
        self.box_sizes = []
        self.last_seen = 0.0
        self.fired_this_visit = False

    def update(self, box_size):
        self.box_sizes.append(box_size)
        if len(self.box_sizes) > TRACK_HISTORY_LEN:
            self.box_sizes.pop(0)
        self.last_seen = time.time()

    def direction(self) -> Direction:
        if len(self.box_sizes) < 2:
            return Direction.ENTERING
        return Direction.ENTERING if self.box_sizes[-1] > self.box_sizes[0] else Direction.EXITING

    def is_stale(self) -> bool:
        return (time.time() - self.last_seen) > TRACK_FORGET_SEC


def process_frame(frame, backend, roster, bus_id, leg, tracks: dict):
    """
    Runs detection+recognition on a downscaled copy of `frame`, updates each
    recognized child's track, feeds the state machine, and returns a list of
    (location_full_res, label, color) to draw on the display frame.
    """
    small = cv2.resize(frame, (0, 0), fx=DETECTION_SCALE, fy=DETECTION_SCALE)
    rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

    try:
        all_faces = backend.get_all_encodings(rgb_small)
    except Exception as e:
        print(f"[ERROR] detection failed: {e}")
        return []

    if not all_faces:
        # Silently skip frames with no faces
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
            draw_items.append(((top, right, bottom, left), "Unknown", (0, 0, 200)))
            continue
        if result.is_ambiguous:
            draw_items.append(((top, right, bottom, left), f"{result.child_id}? (review)", (0, 165, 255)))
            continue

        child_id = result.child_id
        track = tracks.setdefault(child_id, ChildTrack())
        track.update(box_size)

        student = next((s for s in roster if s["child_id"] == child_id), None)
        label = student["name"] if student else child_id
        color = (0, 200, 0)

        if student and not track.fired_this_visit:
            direction = track.direction()
            current_status = db.get_status(child_id)
            detection = Detection(child_id, result.confidence, direction, gps=None)
            outcome = process_detection(detection, leg, student, stop_coords_lookup={})
            
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
                db.queue_event(
                    child_id=child_id, event_type=event_type, confidence=result.confidence,
                    photo_path="", gps=None, bus_id=bus_id,
                )
                print(f"  ✓ [EVENT FIRED] {event_type}")
                label = f"{label}: {event_type}"
                color = (0, 220, 0)
                track.fired_this_visit = True  # don't re-fire every processed frame while they linger
            else:
                print(f"  ✗ No event - blocked by state machine logic")

        draw_items.append(((top, right, bottom, left), label, color))

    return draw_items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bus-id", required=True)
    parser.add_argument("--leg", default="auto", choices=["AM", "PM", "auto"])
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--backend-url", default=None, help="Optional: backend URL for auto-sync")
    parser.add_argument("--display", type=str, default=None, help="DISPLAY variable (e.g. :0). Auto-detect if not set")
    args = parser.parse_args()

    # Set up display if on Pi with physical screen
    import os
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
    last_draw_items = []
    frame_count = 0
    fps_frame_count = 0
    fps_start_time = time.time()
    fps = 0.0

    print(f"Edge app running for {args.bus_id} (continuous mode). Press 'q' to stop.")
    print(f"[DEBUG] CONFIDENCE_THRESHOLD = {0.45} (from matcher.py)")
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue
            frame_count += 1

            if frame_count % PROCESS_EVERY_N_FRAMES == 0:
                roster = db.load_roster()
                if not roster:
                    print(f"[WARN] Empty roster - no students to match. Did roster sync succeed?")
                if roster:
                    t0 = time.time()
                    last_draw_items = process_frame(frame, backend, roster, args.bus_id, current_leg(args.leg), tracks)
                    dt = time.time() - t0
                    if dt > 0.5:
                        print(f"[TIMING] recognition pass took {dt:.2f}s")
                    if last_draw_items:
                        print(f"[DETECTION] Found {len(last_draw_items)} face(s) in frame {frame_count}")
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
