"""
Enrollment dashboard (Phase 7) - a small web app that lets staff enroll students
from ANY device on the network (laptop, phone) via a browser, without needing
dlib/face_recognition installed on that device.

Why this runs ON THE PI: it's the one device we've confirmed has dlib working.
The browser just submits a form; this app does the actual face processing, then
pushes the result to the central backend - same as enroll_student.py did, just
reachable over the network instead of needing SSH + a script per student.

Run on the Pi:
    pip install fastapi uvicorn python-multipart --break-system-packages
    python3 enrollment_dashboard.py --backend-url http://192.168.1.72:8000 --port 8090

Then from ANY device on the same network, open a browser to:
    http://<PI-IP>:8090

After a student is enrolled here, run sync_client.py on any bus (or set it to
--loop) to pull the update down - this is what makes it show up fleet-wide.
"""

import argparse
import io
import numpy as np
import cv2
import requests
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse

from enroll_student import quality_check, get_encoding_for_image

app = FastAPI(title="Student Enrollment Dashboard")
BACKEND_URL = None  # set from --backend-url at startup


FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Enroll a Student</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body { font-family: system-ui, sans-serif; max-width: 480px; margin: 40px auto; padding: 0 16px; color: #222; }
    h1 { font-size: 1.3rem; color: #1F4E79; }
    label { display: block; margin-top: 14px; font-weight: 600; font-size: 0.9rem; }
    input[type=text] { width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
    input[type=file] { margin-top: 6px; }
    button { margin-top: 20px; padding: 10px 20px; background: #1F4E79; color: white; border: none; border-radius: 4px; font-size: 1rem; cursor: pointer; }
    #result { margin-top: 20px; padding: 12px; border-radius: 6px; white-space: pre-wrap; font-size: 0.9rem; }
    .ok { background: #eafaf0; border: 1px solid #34c759; }
    .err { background: #fdeaea; border: 1px solid #e74c3c; }
    small { color: #666; }
  </style>
</head>
<body>
  <h1>Enroll a Student</h1>
  <form id="f">
    <label>Child ID (unique)</label>
    <input type="text" name="child_id" required>

    <label>Full Name</label>
    <input type="text" name="name" required>

    <label>Assigned Bus ID</label>
    <input type="text" name="bus_id" required>

    <label>Pickup Stop ID</label>
    <input type="text" name="pickup_stop_id" required>

    <label>Drop Stop ID</label>
    <input type="text" name="drop_stop_id" required>

    <label>Twin/Sibling Group (optional, leave blank if none)</label>
    <input type="text" name="twin_group">

    <label>Photos (3-5 recommended - different angles, good lighting)</label>
    <input type="file" name="photos" accept="image/*" multiple required>
    <small>On a phone this can open your camera directly to take photos.</small>

    <button type="submit">Enroll Student</button>
  </form>
  <div id="result"></div>

<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const result = document.getElementById('result');
  result.className = '';
  result.textContent = 'Processing... this can take a few seconds per photo.';
  const formData = new FormData(e.target);
  try {
    const resp = await fetch('/enroll', { method: 'POST', body: formData });
    const data = await resp.json();
    result.className = resp.ok ? 'ok' : 'err';
    result.textContent = data.message;
    if (resp.ok) e.target.reset();
  } catch (err) {
    result.className = 'err';
    result.textContent = 'Request failed: ' + err;
  }
});
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def form():
    return FORM_HTML


@app.post("/enroll")
async def enroll(
    child_id: str = Form(...),
    name: str = Form(...),
    bus_id: str = Form(...),
    pickup_stop_id: str = Form(...),
    drop_stop_id: str = Form(...),
    twin_group: str = Form(""),
    photos: list[UploadFile] = File(...),
):
    from matcher import RealBackend
    backend = RealBackend()

    good_encodings = []
    rejections = []
    for i, photo in enumerate(photos):
        raw = await photo.read()
        arr = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if image is None:
            rejections.append(f"Photo {i+1}: could not read file")
            continue
        ok, msg = quality_check(image)
        if not ok:
            rejections.append(f"Photo {i+1}: {msg}")
            continue
        encoding, err = get_encoding_for_image(image, backend)
        if err:
            rejections.append(f"Photo {i+1}: {err}")
            continue
        good_encodings.append(encoding.tolist())

    if not good_encodings:
        return JSONResponse(
            status_code=400,
            content={"message": "No usable photos.\n" + "\n".join(rejections)},
        )

    student = {
        "child_id": child_id,
        "name": name,
        "encodings": good_encodings,
        "assigned_bus_id": bus_id,
        "pickup_stop_id": pickup_stop_id,
        "drop_stop_id": drop_stop_id,
        "twin_group": twin_group or None,
    }

    try:
        resp = requests.post(f"{BACKEND_URL}/api/enroll", json=student, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        return JSONResponse(
            status_code=502,
            content={"message": f"Encodings generated ({len(good_encodings)} good photo(s)) "
                                 f"but could not reach backend at {BACKEND_URL}: {e}"},
        )

    summary = f"Enrolled '{name}' with {len(good_encodings)} good photo(s)."
    if rejections:
        summary += "\n\nSome photos were rejected:\n" + "\n".join(rejections)
    summary += "\n\nRun sync_client.py on each bus (or wait for its --loop cycle) to pick this up."
    return {"message": summary}


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    BACKEND_URL = args.backend_url.rstrip("/")
    print(f"Enrollment dashboard running. Open http://<this-device-IP>:{args.port} from any device on the network.")
    uvicorn.run(app, host="0.0.0.0", port=args.port)
