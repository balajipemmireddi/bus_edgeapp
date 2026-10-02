"""
Production-Level Edge Biometric Enrollment Kiosk (Port 8090)
Serves a responsive, clean, high-performance UI for student registration.
Supports dual-mode photo ingest:
1. Live in-browser webcam viewfinder with face framing guide and snapshot burst
2. High-res photo drag-and-drop
Delegates face encoding to the central backend.
"""

import argparse
import base64
import os
import requests
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Load .env file
load_dotenv()

app = FastAPI(title="SafeBus Edge Enrollment Kiosk")


class EnrollmentRequest(BaseModel):
    child_id: str
    name: str
    bus_id: str
    pickup_stop_id: str
    drop_stop_id: str
    twin_group: str | None = None
    photos: list[str]  # base64 photos


@app.post("/enroll")
async def enroll_handler(req: EnrollmentRequest):
    """Enroll endpoint - wraps the enroll function with stored backend URL."""
    backend_url = getattr(app, 'backend_url', None)
    if not backend_url:
        raise HTTPException(500, "Backend URL not configured")
    
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
            "photos": req.photos
        }
        
        resp = requests.post(
            f"{backend_url.rstrip('/')}/api/enroll/centralized",
            json=payload,
            timeout=180
        )
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("status") == "success":
            print(f"  [SUCCESS] Enrolled with {result.get('encodings_count', 0)} encoding(s)")
            return result
        else:
            raise Exception(result.get("message", "Unknown error"))
    
    except requests.exceptions.Timeout:
        raise HTTPException(504, "Enrollment timeout - Pi face processor took too long. Try with fewer photos.")
    except Exception as e:
        print(f"  [ERROR] Failed: {str(e)}")
        raise HTTPException(502, f"Backend enrollment failed: {str(e)}")


@app.get("/health")
def kiosk_health():
    """Kiosk heartbeat & backend connectivity probe."""
    backend_url = getattr(app, 'backend_url', 'http://localhost:8000')
    backend_online = False
    try:
        r = requests.get(f"{backend_url.rstrip('/')}/health", timeout=2)
        backend_online = r.status_code == 200
    except Exception:
        backend_online = False

    return {
        "status": "online",
        "backend_url": backend_url,
        "backend_online": backend_online
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Modern, production-grade Enrollment Kiosk UI."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>SafeBus — Edge Biometric Enrollment Kiosk</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090e1a;
      --card-bg: #111827;
      --card-elevated: #1a2234;
      --border: #1f293d;
      --border-focus: #4f46e5;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-subtle: #64748b;
      --primary: #4f46e5;
      --primary-hover: #4338ca;
      --primary-glow: rgba(79, 70, 229, 0.35);
      --accent: #06b6d4;
      --emerald: #10b981;
      --rose: #ef4444;
      --amber: #f59e0b;
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
      background: var(--bg);
      color: var(--text-main);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      background-image: 
        radial-gradient(circle at 10% 20%, rgba(79, 70, 229, 0.15) 0%, transparent 40%),
        radial-gradient(circle at 90% 80%, rgba(6, 182, 212, 0.1) 0%, transparent 40%);
      background-attachment: fixed;
    }

    /* Kiosk Header */
    .header {
      background: rgba(17, 24, 39, 0.85);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
      padding: 16px 36px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 50;
    }
    .header-brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-icon {
      width: 42px;
      height: 42px;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--primary), var(--accent));
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 22px;
      box-shadow: 0 4px 14px var(--primary-glow);
    }
    .brand-title h1 {
      font-size: 1.15rem;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .brand-title p {
      font-size: 0.75rem;
      color: var(--text-muted);
    }

    .header-status {
      display: flex;
      align-items: center;
      gap: 14px;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      padding: 6px 14px;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 700;
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: #34d399;
    }
    .status-badge.error {
      background: rgba(239, 68, 68, 0.12);
      border-color: rgba(239, 68, 68, 0.3);
      color: #f87171;
    }
    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: currentColor;
    }

    /* Main Container */
    .container {
      max-width: 1200px;
      width: 100%;
      margin: 0 auto;
      padding: 36px 24px 64px;
      flex: 1;
    }

    .kiosk-grid {
      display: grid;
      grid-template-columns: 1.1fr 1fr;
      gap: 32px;
      align-items: start;
    }
    @media (max-width: 900px) {
      .kiosk-grid { grid-template-columns: 1fr; }
    }

    /* Card Panels */
    .panel {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 32px;
      box-shadow: 0 10px 30px -10px rgba(0,0,0,0.5);
    }
    .panel-header {
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    .panel-header h2 {
      font-size: 1.35rem;
      font-weight: 800;
      color: #fff;
    }
    .panel-header p {
      font-size: 0.82rem;
      color: var(--text-muted);
      margin-top: 4px;
    }

    /* Form Styles */
    .form-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 16px;
    }
    .form-group {
      margin-bottom: 18px;
    }
    .form-group label {
      display: block;
      font-size: 0.78rem;
      font-weight: 700;
      color: var(--text-muted);
      margin-bottom: 8px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .form-input {
      width: 100%;
      padding: 12px 16px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      font-size: 0.92rem;
      font-family: inherit;
      color: #fff;
      transition: all 0.2s;
    }
    .form-input:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-glow);
    }

    /* Studio Tabs */
    .studio-mode-tabs {
      display: flex;
      gap: 10px;
      margin-bottom: 20px;
    }
    .mode-tab-btn {
      flex: 1;
      padding: 11px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      color: var(--text-muted);
      font-weight: 700;
      font-size: 0.85rem;
      cursor: pointer;
      transition: all 0.2s;
    }
    .mode-tab-btn.active {
      background: var(--primary);
      color: #fff;
      border-color: var(--primary);
      box-shadow: 0 4px 12px var(--primary-glow);
    }

    /* Drag & Drop */
    .dropzone {
      border: 2px dashed rgba(255,255,255,0.15);
      border-radius: 16px;
      padding: 36px 20px;
      text-align: center;
      background: rgba(255,255,255,0.02);
      cursor: pointer;
      transition: all 0.2s;
    }
    .dropzone:hover {
      border-color: var(--primary);
      background: rgba(79, 70, 229, 0.06);
    }
    .dropzone-icon {
      font-size: 40px;
      margin-bottom: 12px;
    }
    .dropzone-title {
      font-size: 0.95rem;
      font-weight: 700;
      color: #fff;
    }
    .dropzone-hint {
      font-size: 0.8rem;
      color: var(--text-subtle);
      margin-top: 6px;
    }

    /* Live Webcam Box */
    .camera-frame {
      display: none;
      position: relative;
      background: #000;
      border-radius: 16px;
      overflow: hidden;
      aspect-ratio: 4/3;
      max-height: 280px;
      margin-bottom: 16px;
      box-shadow: inset 0 0 40px rgba(0,0,0,0.8);
    }
    .camera-frame video {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .camera-target-reticle {
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: 150px;
      height: 190px;
      border: 2px dashed rgba(6, 182, 212, 0.8);
      border-radius: 50%;
      box-shadow: 0 0 15px rgba(6, 182, 212, 0.4);
      pointer-events: none;
    }
    .camera-ctrls {
      display: flex;
      gap: 12px;
      justify-content: center;
    }

    /* Action Buttons */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 12px 20px;
      border-radius: 10px;
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
      border: none;
    }
    .btn-primary {
      background: linear-gradient(135deg, var(--primary), #6366f1);
      color: #fff;
      box-shadow: 0 4px 16px var(--primary-glow);
    }
    .btn-primary:hover {
      transform: translateY(-1px);
      box-shadow: 0 8px 24px var(--primary-glow);
    }
    .btn-primary:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }
    .btn-secondary {
      background: var(--card-elevated);
      color: var(--text-main);
      border: 1px solid var(--border);
    }
    .btn-secondary:hover {
      background: rgba(255,255,255,0.08);
    }

    /* Thumbnail Previews */
    .photo-tray {
      margin-top: 24px;
    }
    .photo-tray-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 12px;
    }
    .tray-title {
      font-size: 0.8rem;
      font-weight: 700;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .photo-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(75px, 1fr));
      gap: 10px;
    }
    .photo-card {
      position: relative;
      aspect-ratio: 1;
      border-radius: 12px;
      overflow: hidden;
      border: 2px solid var(--border);
      background: #000;
    }
    .photo-card img {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }
    .photo-remove-btn {
      position: absolute;
      top: 4px;
      right: 4px;
      width: 22px;
      height: 22px;
      border-radius: 50%;
      background: rgba(0,0,0,0.7);
      color: #fff;
      border: none;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 11px;
    }
    .photo-remove-btn:hover {
      background: var(--rose);
    }

    /* Toast */
    #toastBox {
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 1000;
    }
    .toast-msg {
      background: var(--card-elevated);
      border: 1px solid var(--border);
      color: #fff;
      padding: 14px 22px;
      border-radius: 12px;
      font-size: 0.9rem;
      font-weight: 600;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      margin-top: 10px;
      animation: popIn 0.25s ease-out;
    }
    .toast-msg.success { border-left: 4px solid var(--emerald); }
    .toast-msg.error { border-left: 4px solid var(--rose); }
    .toast-msg.info { border-left: 4px solid var(--accent); }
    @keyframes popIn {
      from { transform: translateY(10px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }
  </style>
</head>
<body>
  <!-- Kiosk Header -->
  <header class="header">
    <div class="header-brand">
      <div class="brand-icon">📸</div>
      <div class="brand-title">
        <h1>SafeBus Edge Biometric Station</h1>
        <p>High-Fidelity Student Photo Enrollment Kiosk</p>
      </div>
    </div>
    <div class="header-status">
      <div class="status-badge" id="backendStatusPill">
        <span class="status-dot"></span>
        <span id="backendStatusText">Testing Backend...</span>
      </div>
    </div>
  </header>

  <div class="container">
    <form id="enrollmentForm" onsubmit="handleFormSubmit(event)">
      <div class="kiosk-grid">
        <!-- Student Information Panel -->
        <div class="panel">
          <div class="panel-header">
            <h2>Student Roster Details</h2>
            <p>Ensure details match the fleet transportation roster</p>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>Child ID *</label>
              <input type="text" id="childId" class="form-input" placeholder="e.g. child_014" required>
            </div>
            <div class="form-group">
              <label>Full Legal Name *</label>
              <input type="text" id="name" class="form-input" placeholder="e.g. Aarav Patel" required>
            </div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>Assigned Bus *</label>
              <input type="text" id="busId" class="form-input" placeholder="e.g. bus_14" required>
            </div>
            <div class="form-group">
              <label>Twin Group (Optional)</label>
              <input type="text" id="twinGroup" class="form-input" placeholder="e.g. twin_A">
            </div>
          </div>

          <div class="form-row">
            <div class="form-group">
              <label>Pickup Stop ID *</label>
              <input type="text" id="pickupStop" class="form-input" placeholder="e.g. stop_palm_grove" required>
            </div>
            <div class="form-group">
              <label>Drop Stop ID *</label>
              <input type="text" id="dropStop" class="form-input" placeholder="e.g. stop_high_school" required>
            </div>
          </div>

          <div style="margin-top: 32px;">
            <button type="submit" id="submitBtn" class="btn btn-primary" style="width: 100%; padding: 15px;">
              <span>Submit & Encode Biometrics</span>
            </button>
          </div>
        </div>

        <!-- Biometric Photo Capture Studio -->
        <div class="panel">
          <div class="panel-header">
            <h2>Face Capture Studio</h2>
            <p>Capture 3-5 distinct angles under balanced lighting</p>
          </div>

          <div class="studio-mode-tabs">
            <button type="button" id="tabUpload" class="mode-tab-btn active" onclick="switchMode('upload')">📁 File Upload</button>
            <button type="button" id="tabCamera" class="mode-tab-btn" onclick="switchMode('camera')">📸 Live Camera</button>
          </div>

          <!-- File Upload Mode -->
          <div id="uploadPane">
            <div class="dropzone" onclick="document.getElementById('hiddenFileInput').click()">
              <div class="dropzone-icon">🖼️</div>
              <div class="dropzone-title">Drop student face photos here</div>
              <div class="dropzone-hint">Supports JPEG, PNG (Minimum 100x100 resolution)</div>
              <input type="file" id="hiddenFileInput" multiple accept="image/*" style="display: none;" onchange="handleFileSelect(event)">
            </div>
          </div>

          <!-- Live Camera Mode -->
          <div id="cameraPane" style="display: none;">
            <div class="camera-frame">
              <video id="webcamVideo" autoplay playsinline muted></video>
              <div class="camera-target-reticle"></div>
            </div>
            <div class="camera-ctrls">
              <button type="button" class="btn btn-primary" onclick="snapPhoto()">📸 Capture Frame</button>
              <button type="button" class="btn btn-secondary" onclick="stopCamera()">Stop Camera</button>
            </div>
          </div>

          <!-- Photos Tray -->
          <div class="photo-tray">
            <div class="photo-tray-header">
              <span class="tray-title">Captured Profile Angles (<span id="trayCount">0</span>/5)</span>
              <button type="button" onclick="clearPhotos()" style="background: none; border: none; color: var(--rose); font-size: 0.78rem; font-weight: 700; cursor: pointer;">Clear All</button>
            </div>
            <div class="photo-grid" id="photoGrid"></div>
          </div>
        </div>
      </div>
    </form>
  </div>

  <div id="toastBox"></div>

  <script>
    let photoList = [];
    let mediaStream = null;

    function toast(text, type = 'info') {
      const box = document.getElementById('toastBox');
      const msg = document.createElement('div');
      msg.className = `toast-msg ${type}`;
      msg.textContent = text;
      box.appendChild(msg);
      setTimeout(() => {
        msg.style.opacity = '0';
        msg.style.transition = 'opacity 0.3s';
        setTimeout(() => msg.remove(), 300);
      }, 3500);
    }

    async function checkBackend() {
      try {
        const resp = await fetch('/health');
        if (resp.ok) {
          const data = await resp.json();
          const pill = document.getElementById('backendStatusPill');
          const text = document.getElementById('backendStatusText');
          if (data.backend_online) {
            pill.className = 'status-badge';
            text.textContent = 'Backend Linked (8000)';
          } else {
            pill.className = 'status-badge error';
            text.textContent = 'Backend Disconnected';
          }
        }
      } catch (e) {
        document.getElementById('backendStatusPill').className = 'status-badge error';
        document.getElementById('backendStatusText').textContent = 'Kiosk Offline';
      }
    }

    function switchMode(mode) {
      document.getElementById('tabUpload').classList.toggle('active', mode === 'upload');
      document.getElementById('tabCamera').classList.toggle('active', mode === 'camera');
      document.getElementById('uploadPane').style.display = mode === 'upload' ? 'block' : 'none';
      document.getElementById('cameraPane').style.display = mode === 'camera' ? 'block' : 'none';

      if (mode === 'camera') {
        startCamera();
      } else {
        stopCamera();
      }
    }

    async function startCamera() {
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 } });
        const video = document.getElementById('webcamVideo');
        video.srcObject = mediaStream;
        document.querySelector('.camera-frame').style.display = 'block';
      } catch (err) {
        toast("Camera access denied or unavailable: " + err.message, "error");
        switchMode('upload');
      }
    }

    function stopCamera() {
      if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;
      }
      const frame = document.querySelector('.camera-frame');
      if (frame) frame.style.display = 'none';
    }

    function snapPhoto() {
      if (photoList.length >= 5) {
        toast("Maximum 5 photos allowed", "info");
        return;
      }

      const video = document.getElementById('webcamVideo');
      const canvas = document.createElement('canvas');
      canvas.width = video.videoWidth || 640;
      canvas.height = video.videoHeight || 480;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

      const b64 = canvas.toDataURL('image/jpeg', 0.9).split(',')[1];
      photoList.push(b64);
      updatePhotoGrid();
      toast(`Captured shot #${photoList.length}`, "success");
    }

    function handleFileSelect(evt) {
      const files = Array.from(evt.target.files);
      files.forEach(file => {
        if (photoList.length >= 5) return;
        const reader = new FileReader();
        reader.onload = (e) => {
          const b64 = e.target.result.split(',')[1];
          photoList.push(b64);
          updatePhotoGrid();
        };
        reader.readAsDataURL(file);
      });
      evt.target.value = '';
    }

    function updatePhotoGrid() {
      const grid = document.getElementById('photoGrid');
      document.getElementById('trayCount').textContent = photoList.length;

      grid.innerHTML = photoList.map((b64, idx) => `
        <div class="photo-card">
          <img src="data:image/jpeg;base64,${b64}">
          <button type="button" class="photo-remove-btn" onclick="removePhoto(${idx})">✕</button>
        </div>
      `).join('');
    }

    function removePhoto(idx) {
      photoList.splice(idx, 1);
      updatePhotoGrid();
    }

    function clearPhotos() {
      photoList = [];
      updatePhotoGrid();
    }

    async function handleFormSubmit(evt) {
      evt.preventDefault();
      if (photoList.length === 0) {
        toast("Please capture or upload at least 1 photo", "error");
        return;
      }

      const btn = document.getElementById('submitBtn');
      btn.disabled = true;
      btn.innerHTML = '<span>Encoding Biometric Features...</span>';

      const reqBody = {
        child_id: document.getElementById('childId').value.trim(),
        name: document.getElementById('name').value.trim(),
        bus_id: document.getElementById('busId').value.trim(),
        pickup_stop_id: document.getElementById('pickupStop').value.trim(),
        drop_stop_id: document.getElementById('dropStop').value.trim(),
        twin_group: document.getElementById('twinGroup').value.trim() || null,
        photos: photoList
      };

      try {
        const resp = await fetch('/enroll', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(reqBody)
        });
        const data = await resp.json();

        if (resp.ok && data.status === 'success') {
          toast(`✓ ${data.name || reqBody.name} enrolled with ${data.encodings_count || 1} face vector(s)!`, "success");
          document.getElementById('enrollmentForm').reset();
          clearPhotos();
        } else {
          toast(data.message || data.detail || 'Enrollment rejected by biometric quality check', "error");
        }
      } catch (err) {
        toast("Network error: " + err.message, "error");
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>Submit & Encode Biometrics</span>';
      }
    }

    checkBackend();
    setInterval(checkBackend, 6000);
  </script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default=os.environ.get("BACKEND_URL", "http://localhost:8000"), help="Backend URL")
    parser.add_argument("--port", type=int, default=int(os.environ.get("ENROLLMENT_DASHBOARD_PORT", "8090")), help="Port to run on (default 8090)")
    args = parser.parse_args()
    
    app.backend_url = args.backend_url
    
    print("[STARTUP] SafeBus Edge Enrollment Kiosk running on port " + str(args.port))
    print("[STARTUP] Target Central Backend: " + str(args.backend_url))
    print("[STARTUP] Open http://localhost:" + str(args.port) + " in your browser")
    
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
