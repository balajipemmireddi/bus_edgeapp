"""
Enrollment dashboard - runs on the Pi (port 8090 by default).
Staff opens this from any device on the network, uploads photos.
Pi validates + encodes locally, then pushes to backend.

Run:
    python3 enrollment_dashboard.py --backend-url http://192.168.29.83:8000 --port 8090
    
Then open: http://<Pi-IP>:8090 from any browser on the network.
"""

import argparse
import base64
import io
import json
import os
import cv2
import numpy as np
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
import uvicorn

import db
import face_processor

BLUR_THRESHOLD = float(os.environ.get("BLUR_THRESHOLD", 15.0))

app = FastAPI(title="Student Enrollment Dashboard")

class EnrollmentRequest(BaseModel):
    child_id: str
    name: str
    bus_id: str
    pickup_stop_id: str
    drop_stop_id: str
    twin_group: str | None = None
    photos: list[str]  # base64


def base64_to_cv2(b64_str: str):
    try:
        img_data = base64.b64decode(b64_str)
        arr = np.frombuffer(img_data, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def quality_check(image) -> tuple[bool, str]:
    """Check if image is suitable for enrollment."""
    if image is None:
        return False, "Could not decode image"
    
    h, w = image.shape[:2]
    if w < 100 or h < 100:
        return False, f"Resolution too low ({w}x{h})"
    
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
    
    if sharpness < BLUR_THRESHOLD:
        return False, f"Too blurry (sharpness={sharpness:.1f}, need >{BLUR_THRESHOLD})"
    
    return True, f"OK (sharpness={sharpness:.1f})"


@app.post("/enroll")
def enroll(req: EnrollmentRequest, backend_url: str):
    """Enroll a student with photos."""
    if not req.photos:
        raise HTTPException(400, "No photos provided")
    
    good_encodings = []
    rejections = []
    
    print(f"\n[ENROLL] Processing {len(req.photos)} photos for {req.name}...")
    
    for i, b64_photo in enumerate(req.photos):
        image = base64_to_cv2(b64_photo)
        ok, msg = quality_check(image)
        
        print(f"  Photo {i+1}: {msg}")
        
        if not ok:
            rejections.append(f"Photo {i+1}: {msg}")
            continue
        
        # Get face encoding
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        import face_recognition
        locations = face_recognition.face_locations(rgb)
        
        if len(locations) != 1:
            rejections.append(f"Photo {i+1}: Expected 1 face, found {len(locations)}")
            continue
        
        encodings = face_recognition.face_encodings(rgb, known_face_locations=locations)
        if not encodings:
            rejections.append(f"Photo {i+1}: Encoding failed")
            continue
        
        good_encodings.append(encodings[0].tolist())
        print(f"    ✓ Encoding generated")
    
    if not good_encodings:
        msg = f"No usable photos. {'; '.join(rejections)}"
        print(f"  [FAILED] {msg}")
        return {"status": "error", "message": msg}
    
    # Push to backend
    try:
        payload = {
            "child_id": req.child_id,
            "name": req.name,
            "encodings": good_encodings,
            "assigned_bus_id": req.bus_id,
            "pickup_stop_id": req.pickup_stop_id,
            "drop_stop_id": req.drop_stop_id,
            "twin_group": req.twin_group,
        }
        
        resp = requests.post(f"{backend_url}/api/enroll", json=payload, timeout=10)
        resp.raise_for_status()
        
        result = resp.json()
        msg = f"✓ Enrolled with {len(good_encodings)} encodings"
        print(f"  [SUCCESS] {msg}")
        return {"status": "success", "message": msg, "child_id": req.child_id}
    except Exception as e:
        msg = f"Backend error: {str(e)}"
        print(f"  [FAILED] {msg}")
        return {"status": "error", "message": msg}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Enrollment dashboard UI."""
    return """<!DOCTYPE html>
<html>
<head>
  <title>Enroll Student</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { 
      font-family: 'Inter', sans-serif; 
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .container {
      background: white;
      border-radius: 12px;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
      max-width: 600px;
      width: 100%;
      padding: 40px;
    }
    h1 { 
      color: #333; 
      margin-bottom: 10px;
      font-size: 28px;
    }
    .subtitle {
      color: #666;
      margin-bottom: 30px;
      font-size: 14px;
    }
    .form-group {
      margin-bottom: 20px;
    }
    label {
      display: block;
      margin-bottom: 8px;
      font-weight: 600;
      color: #333;
      font-size: 14px;
    }
    input, select {
      width: 100%;
      padding: 12px;
      border: 1px solid #ddd;
      border-radius: 8px;
      font-size: 14px;
      font-family: inherit;
      transition: border-color 0.2s;
    }
    input:focus, select:focus {
      outline: none;
      border-color: #667eea;
      box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
    }
    .photo-input {
      padding: 30px;
      border: 2px dashed #ddd;
      border-radius: 8px;
      text-align: center;
      cursor: pointer;
      transition: all 0.2s;
    }
    .photo-input:hover {
      border-color: #667eea;
      background: rgba(102, 126, 234, 0.05);
    }
    .photo-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(80px, 1fr));
      gap: 10px;
      margin-top: 15px;
    }
    .photo-thumb {
      position: relative;
      width: 80px;
      height: 80px;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid #ddd;
    }
    .photo-thumb img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .photo-thumb .remove {
      position: absolute;
      top: 2px;
      right: 2px;
      background: rgba(0,0,0,0.6);
      color: white;
      border: none;
      width: 24px;
      height: 24px;
      border-radius: 50%;
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
      padding: 0;
    }
    button {
      width: 100%;
      padding: 14px;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      font-size: 16px;
      cursor: pointer;
      transition: transform 0.1s, box-shadow 0.2s;
      margin-top: 20px;
    }
    button:hover {
      box-shadow: 0 10px 25px rgba(102, 126, 234, 0.4);
      transform: translateY(-2px);
    }
    button:active {
      transform: translateY(0);
    }
    button:disabled {
      background: #ccc;
      cursor: not-allowed;
      transform: none;
    }
    .status {
      margin-top: 20px;
      padding: 15px;
      border-radius: 8px;
      font-size: 14px;
      display: none;
    }
    .status.show { display: block; }
    .status.success {
      background: #d4edda;
      color: #155724;
      border: 1px solid #c3e6cb;
    }
    .status.error {
      background: #f8d7da;
      color: #721c24;
      border: 1px solid #f5c6cb;
    }
    .photo-count {
      color: #666;
      font-size: 12px;
      margin-top: 8px;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>📚 Enroll Student</h1>
    <p class="subtitle">Upload 3-5 photos of the student's face</p>
    
    <form id="enrollForm">
      <div class="form-group">
        <label>Child ID</label>
        <input type="text" id="childId" placeholder="e.g. child_001" required>
      </div>
      
      <div class="form-group">
        <label>Full Name</label>
        <input type="text" id="name" placeholder="e.g. John Doe" required>
      </div>
      
      <div class="form-group">
        <label>Assigned Bus ID</label>
        <input type="text" id="busId" placeholder="e.g. bus_14" required>
      </div>
      
      <div class="form-group">
        <label>Pickup Stop ID</label>
        <input type="text" id="pickupStopId" placeholder="e.g. stop_1" required>
      </div>
      
      <div class="form-group">
        <label>Drop Stop ID</label>
        <input type="text" id="dropStopId" placeholder="e.g. stop_2" required>
      </div>
      
      <div class="form-group">
        <label>Twin/Sibling Group (optional)</label>
        <input type="text" id="twinGroup" placeholder="Leave blank if none">
      </div>
      
      <div class="form-group">
        <label>📷 Upload Photos</label>
        <div class="photo-input" onclick="document.getElementById('photoInput').click()">
          <div style="font-size: 32px; margin-bottom: 10px;">📸</div>
          <div>Click to select photos or drag & drop</div>
        </div>
        <input type="file" id="photoInput" multiple accept="image/*" style="display: none;">
        <div class="photo-grid" id="photoGrid"></div>
        <div class="photo-count" id="photoCount">0 photos selected</div>
      </div>
      
      <button type="submit" id="submitBtn">Enroll Student</button>
    </form>
    
    <div class="status" id="status"></div>
  </div>

  <script>
    let photos = [];
    const photoInput = document.getElementById('photoInput');
    const photoGrid = document.getElementById('photoGrid');
    const photoCount = document.getElementById('photoCount');
    const form = document.getElementById('enrollForm');
    const statusDiv = document.getElementById('status');
    const submitBtn = document.getElementById('submitBtn');
    
    photoInput.addEventListener('change', (e) => {
      for (let file of e.target.files) {
        const reader = new FileReader();
        reader.onload = (evt) => {
          const b64 = evt.target.result.split(',')[1];
          photos.push(b64);
          
          const thumb = document.createElement('div');
          thumb.className = 'photo-thumb';
          thumb.innerHTML = `
            <img src="data:image/jpeg;base64,${b64}">
            <button type="button" class="remove" onclick="removePhoto(this, '${b64}')">×</button>
          `;
          photoGrid.appendChild(thumb);
          photoCount.textContent = `${photos.length} photo${photos.length !== 1 ? 's' : ''} selected`;
        };
        reader.readAsDataURL(file);
      }
    });
    
    window.removePhoto = function(btn, b64) {
      photos = photos.filter(p => p !== b64);
      btn.closest('.photo-thumb').remove();
      photoCount.textContent = `${photos.length} photo${photos.length !== 1 ? 's' : ''} selected`;
    };
    
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      
      if (photos.length === 0) {
        showStatus('Please select at least one photo', 'error');
        return;
      }
      
      submitBtn.disabled = true;
      statusDiv.classList.remove('show', 'success', 'error');
      
      const payload = {
        child_id: document.getElementById('childId').value,
        name: document.getElementById('name').value,
        bus_id: document.getElementById('busId').value,
        pickup_stop_id: document.getElementById('pickupStopId').value,
        drop_stop_id: document.getElementById('dropStopId').value,
        twin_group: document.getElementById('twinGroup').value || null,
        photos: photos
      };
      
      try {
        const response = await fetch('/enroll', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        
        const data = await response.json();
        
        if (response.ok && data.status === 'success') {
          showStatus(`✓ ${data.message}`, 'success');
          form.reset();
          photos = [];
          photoGrid.innerHTML = '';
          photoCount.textContent = '0 photos selected';
        } else {
          showStatus(`✗ ${data.message}`, 'error');
        }
      } catch (err) {
        showStatus(`✗ Network error: ${err.message}`, 'error');
      } finally {
        submitBtn.disabled = false;
      }
    });
    
    function showStatus(msg, type) {
      statusDiv.textContent = msg;
      statusDiv.className = `status show ${type}`;
    }
  </script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    
    db.init_db()
    
    # Make backend_url available to the API
    app.backend_url = args.backend_url
    
    # Wrap the enroll endpoint to inject backend_url
    original_enroll = app.routes[1].endpoint
    async def enroll_with_url(req: EnrollmentRequest):
        return original_enroll(req, args.backend_url)
    
    print(f"Enrollment dashboard running.")
    print(f"Open http://<this-device-IP>:{args.port} from any device on the network.")
    print(f"Backend: {args.backend_url}")
    
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
