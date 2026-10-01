"""
Enrollment dashboard - runs on the Pi (port 8090 by default).
Photos are sent to backend for encoding via face_processor.
Staff opens this from any device on the network, uploads photos.
Backend validates + encodes, then stores in central database.

Configuration via .env file or command-line arguments:
    cp .env.example .env
    # Edit .env with your backend URL
    python3 enrollment_dashboard.py

Or pass via CLI:
    python3 enrollment_dashboard.py --backend-url http://192.168.1.72:8000 --port 8090
    
Then open: http://<Pi-IP>:8090 from any browser on the network.
"""

import argparse
import base64
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import requests
import uvicorn
import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()

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
    """Decode base64 string to OpenCV image."""
    try:
        img_data = base64.b64decode(b64_str)
        arr = np.frombuffer(img_data, np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


@app.post("/enroll")
async def enroll(req: EnrollmentRequest, backend_url: str):
    """Enroll a student - delegate encoding to backend (no local face_recognition needed)."""
    if not req.photos:
        raise HTTPException(400, "No photos provided")
    
    print(f"\n[ENROLL] Processing {len(req.photos)} photos for {req.name}...")
    
    # Send photos to backend for encoding (backend has face_processor)
    try:
        payload = {
            "child_id": req.child_id,
            "name": req.name,
            "bus_id": req.bus_id,
            "pickup_stop_id": req.pickup_stop_id,
            "drop_stop_id": req.drop_stop_id,
            "twin_group": req.twin_group,
            "photos": req.photos  # base64 photos
        }
        
        resp = requests.post(
            f"{backend_url.rstrip('/')}/api/enroll/centralized",
            json=payload,
            timeout=60
        )
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("status") == "success":
            print(f"  ✓ Enrolled with {result.get('encodings_count', 0)} encoding(s)")
            return result
        else:
            raise Exception(result.get("message", "Unknown error"))
    
    except requests.exceptions.Timeout:
        raise HTTPException(504, "Backend timeout - face_processor may be slow or unreachable")
    except Exception as e:
        print(f"  ✗ Failed: {str(e)}")
        raise HTTPException(502, f"Backend enrollment failed: {str(e)}")


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
    .status.partial {
      background: #fff3cd;
      color: #856404;
      border: 1px solid #ffeaa7;
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
      statusDiv.classList.remove('show', 'success', 'error', 'partial');
      
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
        
        if (response.ok) {
          if (data.status === 'success') {
            showStatus(`✓ ${data.message}`, 'success');
          } else if (data.status === 'partial') {
            showStatus(`⚠️ ${data.message}`, 'partial');
          }
          form.reset();
          photos = [];
          photoGrid.innerHTML = '';
          photoCount.textContent = '0 photos selected';
        } else {
          showStatus(`✗ ${data.message || data.detail}`, 'error');
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
    parser.add_argument("--backend-url", default=os.environ.get("BACKEND_URL"), help="Backend URL (from .env or CLI)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ENROLLMENT_DASHBOARD_PORT", "8090")), help="Port to run on (default 8090)")
    args = parser.parse_args()
    
    # Validate backend URL
    if not args.backend_url:
        print("[ERROR] --backend-url is required or set BACKEND_URL in .env")
        exit(1)
    
    # Store backend_url for use in endpoint
    app.backend_url = args.backend_url
    
    # Update the enroll endpoint to use the backend_url
    original_enroll = enroll
    async def enroll_wrapper(req: EnrollmentRequest):
        return await original_enroll(req, args.backend_url)
    
    # Replace the route
    for i, route in enumerate(app.routes):
        if hasattr(route, 'path') and route.path == '/enroll':
            app.routes[i].endpoint = enroll_wrapper
            break
    
    print(f"\n🎓 Enrollment Dashboard")
    print(f"Open http://<this-device-IP>:{args.port} from any browser on the network")
    print(f"Backend: {args.backend_url}")
    print()
    
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
