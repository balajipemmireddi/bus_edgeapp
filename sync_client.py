"""
Sync client (Phase 3): pushes locally queued events to the cloud backend, and
pulls the full roster down to keep this Pi's local cache current.

New contract (Phase B):
- Uses DEVICE_CODE (not BUS_ID) to identify to backend
- Backend resolves device_code → bus_id → roster
- Extracts roster from response["students"] list
- Transforms central format → edge format
- Validates roster before replacing local cache

Run this continuously on the edge device for automatic sync.
Integrated into main.py via a background thread for continuous updates.

Usage:
    python3 sync_client.py --backend-url http://<BACKEND_IP>:8000 --device-code bus_14 --loop

Or configure via .env file and run:
    python3 sync_client.py --loop
"""

import argparse
import time
import requests
import threading
import os
from dotenv import load_dotenv

import db

# Load .env file
load_dotenv()


class SyncClient:
    def __init__(self, backend_url: str, device_code: str):
        self.backend_url = backend_url.rstrip("/")
        self.device_code = device_code
        self.running = False
        self.current_stops = {}  # Map stop_id -> (lat, lng)
    
    def push_queued_events(self) -> tuple[int, int]:
        """
        Push queued events to backend.
        Transforms edge format (child_id, bus_id strings) → backend format (student_id, bus_id ints).
        Returns (succeeded, failed) counts.
        """
        events = db.get_unsynced_events()
        ok, failed = 0, 0
        for e in events:
            try:
                # Transform edge format to backend format
                # Edge sends strings, backend expects ints
                student_id = None
                if e["child_id"] and e["child_id"] != "UNKNOWN":
                    try:
                        student_id = int(e["child_id"])
                    except (ValueError, TypeError):
                        # child_id is not numeric - might be old string format
                        print(f"[SYNC] Skipping event - child_id '{e['child_id']}' is not numeric (old format?)")
                        failed += 1
                        continue
                
                bus_id = None
                if e["bus_id"]:
                    try:
                        # bus_id might be "bus_14", extract just the number
                        if isinstance(e["bus_id"], str) and e["bus_id"].startswith("bus_"):
                            bus_id = int(e["bus_id"].split("_")[1])
                        else:
                            bus_id = int(e["bus_id"])
                    except (ValueError, TypeError, IndexError):
                        print(f"[SYNC] Skipping event - bus_id '{e['bus_id']}' invalid format")
                        failed += 1
                        continue
                
                payload = {
                    "event_uuid": e["event_uuid"],
                    "student_id": student_id,  # NULL for UNKNOWN detections
                    "event_type": e["event_type"],
                    "confidence": e["confidence"],
                    "photo_path": e["photo_path"],
                    "latitude": e.get("gps_lat"),
                    "longitude": e.get("gps_lng"),
                    "bus_id": bus_id,
                    "timestamp": e["timestamp"],
                    "direction": e.get("direction"),
                    "source_device": self.device_code,
                }
                
                resp = requests.post(f"{self.backend_url}/api/events", json=payload, timeout=10)
                if resp.status_code == 200:
                    db.mark_synced(e["id"])
                    ok += 1
                else:
                    print(f"[SYNC] event {e['id']} rejected: {resp.status_code} - {resp.text}")
                    failed += 1
            except requests.exceptions.RequestException as ex:
                print(f"[SYNC] no connection ({ex}) - event {e['id']} stays queued")
                failed += 1
                break
            except Exception as ex:
                print(f"[SYNC] error preparing event: {ex}")
                failed += 1
        
        return ok, failed
    
    def pull_roster(self) -> bool:
        """
        Pull roster from backend using device_code.
        Backend endpoint: /api/device/{device_code}/roster
        Extracts students list and transforms to edge format.
        Validates before replacing local cache.
        """
        try:
            url = f"{self.backend_url}/api/device/{self.device_code}/roster"
            print(f"[SYNC] Fetching roster from: {url}")
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            
            # New contract: response is an object with metadata
            data = resp.json()
            
            # Validate response structure
            if not isinstance(data, dict):
                print(f"[SYNC] ERROR: roster response is not an object: {type(data)}")
                return False
            
            if "students" not in data:
                print(f"[SYNC] ERROR: roster response missing 'students' key. Keys: {list(data.keys())}")
                return False
            
            students = data.get("students", [])
            student_count = data.get("student_count", len(students))
            bus_id = data.get("bus_id")
            
            # Safety: Don't replace with empty roster unless explicit
            if not students and db.get_roster_count() > 0:
                print(f"[SYNC] WARNING: Backend returned empty roster but we have {db.get_roster_count()} cached")
                print(f"[SYNC] Skipping replacement - keeping local cache")
                return False
            
            # Transform central format → edge format
            edge_roster = []
            for student in students:
                # Central model uses numeric IDs, we keep them
                edge_student = {
                    "child_id": str(student.get("student_id")),  # Store as string for backward compat
                    "name": f"{student.get('first_name', '')} {student.get('last_name', '')}".strip(),
                    "encodings": student.get("encodings", []),
                    "assigned_bus_id": str(student.get("bus_id")),  # Store as string
                    "pickup_stop_id": str(student.get("pickup_stop_id", "")),
                    "drop_stop_id": str(student.get("drop_stop_id", "")),
                    "admission_number": student.get("admission_number"),
                    "class_name": student.get("class_name"),
                    "section": student.get("section"),
                }
                edge_roster.append(edge_student)
            
            # Replace roster
            db.replace_roster(edge_roster)
            print(f"[SYNC] ✓ roster updated: {len(edge_roster)} students for bus {bus_id}")
            return True
        
        except requests.exceptions.RequestException as ex:
            print(f"[SYNC] roster pull failed: {type(ex).__name__}: {ex}")
            return False
        except Exception as ex:
            print(f"[SYNC] unexpected error fetching roster: {type(ex).__name__}: {ex}")
            return False
    
    def pull_deletions(self):
        """
        Fetch pending student deletions from backend and process them.
        Also sends acknowledgments back for successful deletions.
        """
        try:
            url = f"{self.backend_url}/api/deletion-queue"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            deletions = data.get("deletions", [])
            
            if not deletions:
                return  # No pending deletions
            
            print(f"[SYNC] Processing {len(deletions)} pending student deletions")
            
            for deletion in deletions:
                deletion_uuid = deletion["deletion_uuid"]
                child_id = deletion["child_id"]
                student_name = deletion["student_name"]
                
                try:
                    # Process the deletion (removes from local roster)
                    db.process_deletion(deletion_uuid, child_id)
                    print(f"[SYNC] ✓ Deleted '{student_name}' ({child_id}) - removed from local roster")
                    
                    # Acknowledge to backend that we've processed it
                    ack_url = f"{self.backend_url}/api/deletion-queue/{deletion_uuid}/ack"
                    requests.post(ack_url, params={"bus_id": str(self.device_code)}, timeout=5)
                    print(f"[SYNC] Acknowledged deletion of {child_id} to backend")
                except Exception as ex:
                    print(f"[SYNC] Failed to process deletion of {child_id}: {ex}")
        
        except requests.exceptions.RequestException as ex:
            print(f"[SYNC] deletion queue pull failed: {type(ex).__name__}: {ex}")
        except Exception as ex:
            print(f"[SYNC] unexpected error fetching deletions: {type(ex).__name__}: {ex}")
    
    def pull_stops(self) -> dict:
        """
        Fetch stop coordinates for geofence validation.
        New contract: response has {"total": N, "stops": [...]}
        Returns dict mapping stop_id -> (lat, lng) for state machine.
        Updates self.current_stops so main.py can access it.
        """
        try:
            url = f"{self.backend_url}/api/stops"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            
            # New contract: response is an object with metadata
            data = resp.json()
            
            if not isinstance(data, dict):
                print(f"[SYNC] ERROR: stops response is not an object: {type(data)}")
                return {}
            
            stops_list = data.get("stops", [])
            
            result = {}
            for stop in stops_list:
                stop_id = stop.get("id") or stop.get("stop_id")  # Support both formats
                lat = stop.get("latitude")
                lng = stop.get("longitude")
                if stop_id and lat is not None and lng is not None:
                    result[str(stop_id)] = (lat, lng)
            
            self.current_stops = result
            print(f"[SYNC] ✓ stops updated: {len(result)} stops with coordinates")
            return result
        
        except requests.exceptions.RequestException as ex:
            print(f"[SYNC] stops pull failed: {type(ex).__name__}: {ex} - geofence disabled")
            return {}
        except Exception as ex:
            print(f"[SYNC] unexpected error fetching stops: {type(ex).__name__}: {ex}")
            return {}
    
    def send_heartbeat(self) -> bool:
        """
        Send heartbeat with device_code (not bus_id).
        Backend endpoint: /api/devices/{device_code}/heartbeat
        """
        try:
            url = f"{self.backend_url}/api/devices/{self.device_code}/heartbeat"
            resp = requests.post(url, timeout=5)
            if resp.status_code == 200:
                print(f"[HEARTBEAT] {self.device_code} registered and online")
                return True
            else:
                print(f"[HEARTBEAT] failed with status {resp.status_code}: {resp.text}")
                return False
        except requests.exceptions.RequestException as ex:
            print(f"[HEARTBEAT] failed: {type(ex).__name__}: {ex}")
            return False
    
    def one_pass(self):
        """Single sync iteration: heartbeat → events → deletions → roster → stops."""
        self.send_heartbeat()
        ok, failed = self.push_queued_events()
        if ok or failed:
            print(f"[SYNC] events: {ok} pushed, {failed} queued")
        self.pull_deletions()  # Process any student deletions from backend
        self.pull_roster()
        self.pull_stops()
    
    def run_loop(self, interval: int = 30):
        """Run continuous sync loop."""
        self.running = True
        print(f"[SYNC] Client running every {interval}s. Press Ctrl+C to stop.")
        while self.running:
            try:
                self.one_pass()
                time.sleep(interval)
            except KeyboardInterrupt:
                self.running = False
                print("[SYNC] stopped")
                break
            except Exception as e:
                print(f"[SYNC] error in main loop: {e}")
                time.sleep(interval)
    
    def start_background(self, interval: int = 30):
        """Start sync in a background thread."""
        thread = threading.Thread(target=lambda: self.run_loop(interval), daemon=True)
        thread.start()
        return thread


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default=os.environ.get("BACKEND_URL"), help="Backend URL (from .env or CLI)")
    parser.add_argument("--device-code", default=os.environ.get("DEVICE_CODE"), help="Device code (from .env or CLI)")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    
    # Validate required args
    if not args.backend_url:
        print("[ERROR] --backend-url is required or set BACKEND_URL in .env")
        exit(1)
    if not args.device_code:
        print("[ERROR] --device-code is required or set DEVICE_CODE in .env")
        exit(1)
    
    db.init_db()
    client = SyncClient(args.backend_url, args.device_code)
    
    if args.loop:
        client.run_loop(args.interval)
    else:
        client.one_pass()


if __name__ == "__main__":
    main()
