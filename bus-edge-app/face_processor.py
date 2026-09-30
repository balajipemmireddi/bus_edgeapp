"""
Face processing microservice - runs on the Pi (dlib already works here via
piwheels). The Windows backend delegates ALL face encoding work to this service
over HTTP instead of importing face_recognition itself - this is what removes
the need to ever install dlib on Windows.

Run on the Pi:
    pip install fastapi uvicorn --break-system-packages
    python3 face_processor.py --port 8095

The backend calls this at FACE_PROCESSOR_URL (set in backend/main.py or via the
FACE_PROCESSOR_URL environment variable).
"""

import argparse
import base64
import sys
import numpy as np
import cv2
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Face Processor (internal - called by backend only)")

MIN_RESOLUTION = (100, 100)
BLUR_THRESHOLD = 15.0  # matches the value already tuned on real phone-camera uploads

# Import face_recognition ONCE at startup, not lazily inside the request handler.
# Lazy import meant a broken dlib install would let this service start up fine
# and only fail the moment someone actually tried to enroll a student - the
# worst possible time to discover it. Fail loudly now instead.
try:
    import face_recognition
    print("[STARTUP] face_recognition imported successfully - dlib is working.")
except ImportError as e:
    print(f"[FATAL] face_recognition/dlib is NOT installed or broken: {e}")
    print("[FATAL] Fix this before running the app - install via piwheels:")
    print("[FATAL]   pip install face-recognition --extra-index-url https://www.piwheels.org/simple")
    sys.exit(1)


class EncodeRequest(BaseModel):
    photos: list[str]  # base64-encoded JPEG/PNG images


def base64_to_cv2(b64_str: str):
    try:
        img_data = base64.b64decode(b64_str)
        arr = np.frombuffer(img_data, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def quality_check(image) -> tuple[bool, str]:
    if image is None:
        return False, "Could not decode image"
    h, w = image.shape[:2]
    if w < MIN_RESOLUTION[0] or h < MIN_RESOLUTION[1]:
        return False, f"Resolution too low ({w}x{h})"
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    print(f"  [quality] {w}x{h}, sharpness={sharpness:.1f} (threshold={BLUR_THRESHOLD})")
    if sharpness < BLUR_THRESHOLD:
        return False, f"Too blurry (sharpness={sharpness:.1f}, need >{BLUR_THRESHOLD})"
    return True, "OK"


@app.post("/encode")
def encode(req: EncodeRequest):
    good_encodings = []
    rejections = []

    for i, b64_photo in enumerate(req.photos):
        try:
            image = base64_to_cv2(b64_photo)
            ok, msg = quality_check(image)
            if not ok:
                rejections.append(f"Photo {i+1}: {msg}")
                continue

            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb)
            if len(locations) != 1:
                rejections.append(f"Photo {i+1}: expected 1 face, found {len(locations)}")
                continue

            encodings = face_recognition.face_encodings(rgb, known_face_locations=locations)
            if not encodings:
                rejections.append(f"Photo {i+1}: encoding failed")
                continue

            good_encodings.append(encodings[0].tolist())
            print(f"  Photo {i+1}: accepted")
        except Exception as e:
            # A single bad photo should never take down the whole request -
            # log it clearly and keep processing the rest.
            print(f"[ERROR] Photo {i+1} raised an unexpected exception: {e}")
            import traceback
            traceback.print_exc()
            rejections.append(f"Photo {i+1}: unexpected error - {e}")

    print(f"[RESULT] {len(good_encodings)} accepted, {len(rejections)} rejected")
    return {"encodings": good_encodings, "rejections": rejections}


@app.get("/health")
def health():
    return {"status": "ok", "service": "face_processor"}


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8095)
    args = parser.parse_args()
    print(f"Face processor running on port {args.port} - called by the backend only, never directly by a browser.")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
