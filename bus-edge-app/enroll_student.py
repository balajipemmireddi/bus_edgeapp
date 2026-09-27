"""
Enroll a student: capture 3-5 photos (from files or live from the USB camera),
run automatic quality checks (§6 step 3), generate encodings, save to roster.

Usage:
    # From existing photo files:
    python3 enroll_student.py --id child_001 --name "Aarav Sharma" \\
        --bus bus_14 --pickup-stop stop_1 --drop-stop stop_1 \\
        --photos photo1.jpg photo2.jpg photo3.jpg

    # Live capture from the USB camera (5 shots, press SPACE to capture each):
    python3 enroll_student.py --id child_001 --name "Aarav Sharma" \\
        --bus bus_14 --pickup-stop stop_1 --drop-stop stop_1 --live
"""

import argparse
import os
import sys
from pathlib import Path
import cv2
import numpy as np

import db

MIN_RESOLUTION = (200, 200)   # width, height - reject anything smaller
# Lowered from an untested 80.0 default after real phone-camera uploads (via the
# enrollment dashboard) consistently scored 32-38 despite being genuinely usable
# photos - JPEG compression and browser-side resizing lower this score even for
# well-focused shots. 15.0 is a pragmatic unblock value; re-tune once we have a
# real dataset of clearly-blurry vs clearly-sharp photos to compare scores against.
BLUR_THRESHOLD = float(os.environ.get("BLUR_THRESHOLD", 15.0))


def quality_check(image) -> tuple[bool, str]:
    """§6 step 3: exactly one face, minimum resolution, not too blurry."""
    h, w = image.shape[:2]
    if w < MIN_RESOLUTION[0] or h < MIN_RESOLUTION[1]:
        return False, f"Resolution too low ({w}x{h}, need at least {MIN_RESOLUTION})"

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
    print(f"  [quality] {w}x{h}, sharpness={blur_score:.1f} (threshold={BLUR_THRESHOLD})")
    if blur_score < BLUR_THRESHOLD:
        return False, f"Image too blurry (sharpness score {blur_score:.1f}, need >{BLUR_THRESHOLD})"

    # Face-count check requires the real face_recognition backend - see get_encoding below.
    return True, "OK"


def get_encoding_for_image(image_bgr, backend):
    """Returns (encoding, error_message). error_message is None on success."""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    try:
        encoding, _ = backend.get_encoding(rgb)
    except Exception as e:  # pragma: no cover - depends on real dlib backend
        return None, f"Face detection failed: {e}"
    if encoding is None:
        return None, "No face detected in this photo - reject and re-take"
    return encoding, None


DEBUG_DIR = Path(__file__).parent / "enrollment" / "debug"


def capture_live(num_shots=5, debug_save=True):
    """Opens the USB camera, lets the operator press SPACE to capture each shot.

    debug_save=True writes every captured frame to enrollment/debug/ regardless
    of whether it later passes quality checks - lets you inspect the actual
    photos on a bigger screen (scp them off) instead of trusting a tiny 2" panel.
    """
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: could not open camera (check it's plugged in / index 0 is correct)")
        sys.exit(1)

    # Force a decent resolution - many USB webcams default to something tiny
    # (e.g. 160x120) unless explicitly told otherwise, which can be too small
    # for reliable face detection.
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    actual_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print(f"Camera resolution: requested 1280x720, got {int(actual_w)}x{int(actual_h)}")

    if debug_save:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    shots = []
    print(f"Live enrollment: press SPACE to capture (need {num_shots}), ESC to cancel.")
    window_name = "Enrollment"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.moveWindow(window_name, 0, 0)
    while len(shots) < num_shots:
        ret, frame = cap.read()
        if not ret:
            continue
        display = frame.copy()
        cv2.putText(display, f"Shot {len(shots)+1}/{num_shots} - SPACE to capture",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
        # Scale down for display only (on-disk capture stays full resolution) -
        # fits small screens like a 7" panel instead of overflowing.
        h, w = display.shape[:2]
        scale = min(760 / w, 420 / h, 1.0)
        if scale < 1.0:
            display = cv2.resize(display, (int(w * scale), int(h * scale)))
        cv2.imshow(window_name, display)
        key = cv2.waitKey(1) & 0xFF
        if key == 32:  # SPACE
            shots.append(frame.copy())
            brightness = float(np.mean(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)))
            print(f"  Captured shot {len(shots)}: {frame.shape[1]}x{frame.shape[0]}, "
                  f"avg brightness {brightness:.1f} (aim for 60-180; too dark/bright hurts detection)")
            if debug_save:
                path = DEBUG_DIR / f"shot_{len(shots)}.jpg"
                cv2.imwrite(str(path), frame)
                print(f"  Saved to {path} - pull it off with scp to inspect if detection fails")
        elif key == 27:  # ESC
            break

    cap.release()
    cv2.destroyAllWindows()
    return shots


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Unique child ID")
    parser.add_argument("--name", required=True)
    parser.add_argument("--bus", required=True, dest="bus_id")
    parser.add_argument("--pickup-stop", required=True, dest="pickup_stop_id")
    parser.add_argument("--drop-stop", required=True, dest="drop_stop_id")
    parser.add_argument("--twin-group", default=None, help="Set if this child has a lookalike sibling/twin also enrolled")
    parser.add_argument("--photos", nargs="*", help="Paths to existing photo files")
    parser.add_argument("--live", action="store_true", help="Capture live from USB camera instead")
    parser.add_argument("--backend-url", default=None,
                         help="If set, also POST this enrollment to the cloud backend "
                              "(e.g. http://192.168.1.72:8000) so it's centrally recorded "
                              "and survives the next roster sync. Strongly recommended - "
                              "local-only enrollment gets overwritten by sync_client.py.")
    args = parser.parse_args()

    if not args.photos and not args.live:
        print("ERROR: provide --photos <files...> or --live")
        sys.exit(1)

    images = []
    if args.live:
        images = capture_live()
    else:
        for path in args.photos:
            img = cv2.imread(path)
            if img is None:
                print(f"WARNING: could not read {path}, skipping")
                continue
            images.append(img)

    if len(images) < 3:
        print(f"WARNING: only {len(images)} usable photo(s) captured. "
              "3-5 recommended for good accuracy (spec §6).")

    # Try the real backend; fall back to a clear error if dlib isn't installed yet.
    try:
        from matcher import RealBackend
        backend = RealBackend()
    except ImportError:
        print("ERROR: face_recognition not installed. On the Pi, install via piwheels:\n"
              "  pip install face_recognition --extra-index-url https://www.piwheels.org/simple")
        sys.exit(1)

    good_encodings = []
    for i, img in enumerate(images):
        ok, msg = quality_check(img)
        if not ok:
            print(f"Photo {i+1}: REJECTED - {msg}")
            continue
        encoding, err = get_encoding_for_image(img, backend)
        if err:
            print(f"Photo {i+1}: REJECTED - {err}")
            continue
        good_encodings.append(encoding.tolist())
        print(f"Photo {i+1}: accepted")

    if not good_encodings:
        print("ERROR: no usable photos - enrollment aborted. Retake photos with "
              "better lighting/focus, single face per shot.")
        sys.exit(1)

    db.init_db()
    roster = db.load_roster()
    roster = [s for s in roster if s["child_id"] != args.id]  # replace if re-enrolling
    new_student = {
        "child_id": args.id,
        "name": args.name,
        "encodings": good_encodings,
        "assigned_bus_id": args.bus_id,
        "pickup_stop_id": args.pickup_stop_id,
        "drop_stop_id": args.drop_stop_id,
        "twin_group": args.twin_group,
    }
    roster.append(new_student)
    db.replace_roster(roster)
    print(f"\nEnrolled '{args.name}' ({args.id}) with {len(good_encodings)} encoding(s) locally.")

    if args.backend_url:
        try:
            import requests
            resp = requests.post(f"{args.backend_url.rstrip('/')}/api/enroll", json=new_student, timeout=10)
            if resp.status_code == 200:
                print(f"Also pushed to backend at {args.backend_url} - safe to sync now.")
            else:
                print(f"WARNING: backend rejected enrollment ({resp.status_code}): {resp.text}\n"
                      f"Local enrollment is still saved, but running sync_client.py will "
                      f"OVERWRITE it since the backend doesn't have this student yet.")
        except Exception as e:
            print(f"WARNING: could not reach backend ({e}).\n"
                  f"Local enrollment is still saved, but running sync_client.py will "
                  f"OVERWRITE it since the backend doesn't have this student yet.")
    else:
        print("NOTE: no --backend-url given - this enrollment is LOCAL ONLY. "
              "Running sync_client.py will overwrite it with whatever the backend has. "
              "Pass --backend-url http://<backend-ip>:8000 to record it centrally instead.")


if __name__ == "__main__":
    main()
