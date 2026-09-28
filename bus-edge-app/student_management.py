"""
Student Management Dashboard - CRUD operations for enrolled students.

View all students, edit details, delete students, re-enroll, export/import.

Run: python3 student_management.py --port 8091 --backend-url http://192.168.1.72:8000

Open: http://<pi-ip>:8091
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import argparse
import json
import requests
from datetime import datetime

import db

app = FastAPI(title="Student Management")

# Enable CORS for cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/students")
def get_students():
    """Get all enrolled students."""
    roster = db.load_roster()
    return {
        "total": len(roster),
        "students": [
            {
                "child_id": s["child_id"],
                "name": s["name"],
                "assigned_bus_id": s.get("assigned_bus_id"),
                "pickup_stop_id": s.get("pickup_stop_id"),
                "drop_stop_id": s.get("drop_stop_id"),
                "twin_group": s.get("twin_group"),
                "num_encodings": len(s.get("encodings", [])),
            }
            for s in roster
        ]
    }


@app.get("/api/students/{child_id}")
def get_student(child_id: str):
    """Get details for one student."""
    roster = db.load_roster()
    student = next((s for s in roster if s["child_id"] == child_id), None)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    return {
        "child_id": student["child_id"],
        "name": student["name"],
        "assigned_bus_id": student.get("assigned_bus_id"),
        "pickup_stop_id": student.get("pickup_stop_id"),
        "drop_stop_id": student.get("drop_stop_id"),
        "twin_group": student.get("twin_group"),
        "num_encodings": len(student.get("encodings", [])),
        "status": db.get_status(child_id),
    }


@app.post("/api/students/{child_id}")
def update_student(child_id: str, data: dict):
    """Update student info (name, stops, etc. - NOT encodings)."""
    roster = db.load_roster()
    student = next((s for s in roster if s["child_id"] == child_id), None)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    
    # Update allowed fields only
    if "name" in data:
        student["name"] = data["name"]
    if "assigned_bus_id" in data:
        student["assigned_bus_id"] = data["assigned_bus_id"]
    if "pickup_stop_id" in data:
        student["pickup_stop_id"] = data["pickup_stop_id"]
    if "drop_stop_id" in data:
        student["drop_stop_id"] = data["drop_stop_id"]
    if "twin_group" in data:
        student["twin_group"] = data["twin_group"]
    
    db.replace_roster(roster)
    return {"status": "updated", "child_id": child_id}


@app.delete("/api/students/{child_id}")
def delete_student(child_id: str):
    """Delete a student from roster."""
    roster = db.load_roster()
    original_count = len(roster)
    roster = [s for s in roster if s["child_id"] != child_id]
    
    if len(roster) == original_count:
        raise HTTPException(status_code=404, detail="Student not found")
    
    db.replace_roster(roster)
    return {"status": "deleted", "child_id": child_id}


@app.get("/api/export")
def export_roster():
    """Export all students as JSON."""
    roster = db.load_roster()
    return {
        "exported_at": datetime.now().isoformat(),
        "total_students": len(roster),
        "students": roster
    }


@app.get("/")
def dashboard():
    """Student management dashboard UI."""
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Student Management</title>
        <style>
            * { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
            body { background: #f5f5f5; margin: 0; padding: 20px; }
            .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            h1 { color: #333; margin-top: 0; }
            .controls { margin-bottom: 20px; display: flex; gap: 10px; }
            button { padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
            button:hover { background: #0056b3; }
            button.danger { background: #dc3545; }
            button.danger:hover { background: #c82333; }
            button.success { background: #28a745; }
            button.success:hover { background: #218838; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
            th { background: #f9f9f9; font-weight: 600; }
            tr:hover { background: #f9f9f9; }
            .action-buttons { display: flex; gap: 5px; }
            .action-buttons button { padding: 5px 10px; font-size: 12px; }
            .status-badge { display: inline-block; padding: 4px 8px; background: #e2e3e5; border-radius: 3px; font-size: 12px; }
            .status-active { background: #d4edda; color: #155724; }
            .empty-state { text-align: center; padding: 40px; color: #999; }
            .modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; }
            .modal.show { display: flex; align-items: center; justify-content: center; }
            .modal-content { background: white; padding: 30px; border-radius: 8px; width: 90%; max-width: 500px; }
            .modal-content h2 { margin-top: 0; }
            .form-group { margin-bottom: 15px; }
            .form-group label { display: block; margin-bottom: 5px; font-weight: 600; }
            .form-group input { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
            .form-buttons { display: flex; gap: 10px; margin-top: 20px; }
            .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
            .stat-card { background: #f9f9f9; padding: 15px; border-radius: 4px; border-left: 4px solid #007bff; }
            .stat-card h3 { margin: 0 0 5px 0; color: #666; font-size: 14px; }
            .stat-card .value { font-size: 24px; font-weight: bold; color: #007bff; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>👥 Student Management</h1>
            
            <div class="stats">
                <div class="stat-card">
                    <h3>Total Students</h3>
                    <div class="value" id="total-students">0</div>
                </div>
            </div>
            
            <div class="controls">
                <button class="success" onclick="exportData()">📥 Export Students</button>
                <button onclick="reloadTable()">🔄 Refresh</button>
            </div>
            
            <table id="students-table">
                <thead>
                    <tr>
                        <th>Student ID</th>
                        <th>Name</th>
                        <th>Bus</th>
                        <th>Pickup Stop</th>
                        <th>Drop Stop</th>
                        <th>Photos</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="students-body">
                    <tr><td colspan="8" class="empty-state">Loading...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- Edit Modal -->
        <div class="modal" id="edit-modal">
            <div class="modal-content">
                <h2>Edit Student</h2>
                <div class="form-group">
                    <label>Student ID</label>
                    <input type="text" id="edit-id" disabled>
                </div>
                <div class="form-group">
                    <label>Name</label>
                    <input type="text" id="edit-name">
                </div>
                <div class="form-group">
                    <label>Bus ID</label>
                    <input type="text" id="edit-bus">
                </div>
                <div class="form-group">
                    <label>Pickup Stop</label>
                    <input type="text" id="edit-pickup">
                </div>
                <div class="form-group">
                    <label>Drop Stop</label>
                    <input type="text" id="edit-drop">
                </div>
                <div class="form-buttons">
                    <button class="success" onclick="saveEdit()">Save</button>
                    <button onclick="closeModal()">Cancel</button>
                </div>
            </div>
        </div>

        <script>
            async function loadStudents() {
                try {
                    const res = await fetch('/api/students');
                    const data = await res.json();
                    document.getElementById('total-students').textContent = data.total;
                    
                    const body = document.getElementById('students-body');
                    if (data.students.length === 0) {
                        body.innerHTML = '<tr><td colspan="8" class="empty-state">No students enrolled yet</td></tr>';
                        return;
                    }
                    
                    body.innerHTML = data.students.map(s => `
                        <tr>
                            <td><code>${s.child_id}</code></td>
                            <td>${s.name}</td>
                            <td>${s.assigned_bus_id || '—'}</td>
                            <td>${s.pickup_stop_id || '—'}</td>
                            <td>${s.drop_stop_id || '—'}</td>
                            <td><span class="status-badge">${s.num_encodings} photos</span></td>
                            <td><span class="status-badge status-active">Active</span></td>
                            <td>
                                <div class="action-buttons">
                                    <button onclick="editStudent('${s.child_id}')">Edit</button>
                                    <button class="danger" onclick="deleteStudent('${s.child_id}')">Delete</button>
                                </div>
                            </td>
                        </tr>
                    `).join('');
                } catch (e) {
                    console.error('Error loading students:', e);
                }
            }

            async function editStudent(child_id) {
                try {
                    const res = await fetch(`/api/students/${child_id}`);
                    const s = await res.json();
                    
                    document.getElementById('edit-id').value = s.child_id;
                    document.getElementById('edit-name').value = s.name;
                    document.getElementById('edit-bus').value = s.assigned_bus_id || '';
                    document.getElementById('edit-pickup').value = s.pickup_stop_id || '';
                    document.getElementById('edit-drop').value = s.drop_stop_id || '';
                    
                    window.current_edit_id = child_id;
                    document.getElementById('edit-modal').classList.add('show');
                } catch (e) {
                    console.error('Error loading student:', e);
                    alert('Could not load student details');
                }
            }

            async function saveEdit() {
                const child_id = window.current_edit_id;
                const data = {
                    name: document.getElementById('edit-name').value,
                    assigned_bus_id: document.getElementById('edit-bus').value,
                    pickup_stop_id: document.getElementById('edit-pickup').value,
                    drop_stop_id: document.getElementById('edit-drop').value,
                };
                
                try {
                    const res = await fetch(`/api/students/${child_id}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(data)
                    });
                    if (res.ok) {
                        alert('Student updated');
                        closeModal();
                        loadStudents();
                    } else {
                        alert('Error updating student');
                    }
                } catch (e) {
                    console.error('Error:', e);
                    alert('Error updating student');
                }
            }

            async function deleteStudent(child_id) {
                if (!confirm(`Delete ${child_id}? This cannot be undone.`)) return;
                
                try {
                    const res = await fetch(`/api/students/${child_id}`, { method: 'DELETE' });
                    if (res.ok) {
                        alert('Student deleted');
                        loadStudents();
                    } else {
                        alert('Error deleting student');
                    }
                } catch (e) {
                    console.error('Error:', e);
                    alert('Error deleting student');
                }
            }

            async function exportData() {
                try {
                    const res = await fetch('/api/export');
                    const data = await res.json();
                    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `students_${new Date().toISOString().split('T')[0]}.json`;
                    a.click();
                } catch (e) {
                    console.error('Error:', e);
                    alert('Error exporting data');
                }
            }

            function closeModal() {
                document.getElementById('edit-modal').classList.remove('show');
            }

            function reloadTable() {
                loadStudents();
            }

            // Load on page load
            loadStudents();
            setInterval(loadStudents, 5000); // Auto-refresh every 5 seconds
        </script>
    </body>
    </html>
    """)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--backend-url", default=None)
    args = parser.parse_args()

    db.init_db()
    
    print(f"""
    ╔════════════════════════════════════════════╗
    ║     Student Management Dashboard           ║
    ╚════════════════════════════════════════════╝
    
    🌐 Open: http://<pi-ip>:{args.port}
    
    Features:
    ✓ View all enrolled students
    ✓ Edit student info (name, stops, bus ID)
    ✓ Delete students
    ✓ Export all students as JSON
    ✓ Real-time updates
    
    Press Ctrl+C to stop
    """)
    
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
