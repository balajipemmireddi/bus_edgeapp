"""
Sync client (Phase 3): pushes locally queued events to the cloud backend, and
pulls the full roster down to keep this Pi's local cache current.

Run this continuously on the edge device for automatic sync.
Integrated into main.py via a background thread for continuous updates.

Usage:
    python3 sync_client.py --backend-url http://<BACKEND_IP>:8000 --bus-id bus_14 --loop

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
    def __init__(self, backend_url: str, bus_id: str):
        self.backend_url = backend_url.rstrip("/")
        self.bus_id = bus_id
        self.running = False
        self.current_stops = {}  # Map stop_id -> (lat, lng)
    
    def push_queued_events(self) -> tuple[int, int]:
        """Returns (succeeded, failed) counts."""
        events = db.get_unsynced_events()
        ok, failed = 0, 0
        for e in events:
            payload = {
                "event_uuid": e["event_uuid"],
                "child_id": e["child_id"],
                "event_type": e["event_type"],
                "confidence": e["confidence"],
                "photo_path": e["photo_path"],
                "gps_lat": e["gps_lat"],
                "gps_lng": e["gps_lng"],
                "bus_id": e["bus_id"],
                "timestamp": e["timestamp"],
            }
            try:
                resp = requests.post(f"{self.backend_url}/api/events", json=payload, timeout=10)
                if resp.status_code == 200:
                    db.mark_synced(e["id"])
                    ok += 1
                else:
                    print(f"[SYNC] event {e['id']} rejected: {resp.status_code}")
                    failed += 1
            except requests.exceptions.RequestException as ex:
                print(f"[SYNC] no connection ({ex}) - event {e['id']} stays queued")
                failed += 1
                break
        return ok, failed
    
    def pull_roster(self) -> bool:
        try:
            url = f"{self.backend_url}/api/bus/{self.bus_id}/roster"
            print(f"[SYNC] Fetching roster from: {url}")
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            roster = resp.json()
            db.replace_roster(roster)
            print(f"[SYNC] roster updated: {len(roster)} students")
            return True
        except requests.exceptions.RequestException as ex:
            print(f"[SYNC] roster pull failed: {type(ex).__name__}: {ex}")
            return False
        except Exception as ex:
            print(f"[SYNC] unexpected error fetching roster: {type(ex).__name__}: {ex}")
            return False
    
    def pull_stops(self) -> dict:
        """
        Fetch stop coordinates for geofence validation.
        Returns dict mapping stop_id -> (lat, lng) for state machine.
        Updates self.current_stops so main.py can access it.
        """
        try:
            url = f"{self.backend_url}/api/stops"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            stops = resp.json()
            result = {}
            for stop in stops:
                result[stop["stop_id"]] = (stop["latitude"], stop["longitude"])
            self.current_stops = result
            print(f"[SYNC] stops updated: {len(result)} stops with coordinates")
            return result
        except requests.exceptions.RequestException as ex:
            print(f"[SYNC] stops pull failed: {type(ex).__name__}: {ex} - geofence disabled")
            return {}
        except Exception as ex:
            print(f"[SYNC] unexpected error fetching stops: {type(ex).__name__}: {ex}")
            return {}
    
    def send_heartbeat(self) -> bool:
        try:
            url = f"{self.backend_url}/api/devices/{self.bus_id}/heartbeat"
            resp = requests.post(url, timeout=5)
            if resp.status_code == 200:
                print(f"[HEARTBEAT] {self.bus_id} online")
                return True
            else:
                print(f"[HEARTBEAT] failed with status {resp.status_code}")
                return False
        except requests.exceptions.RequestException as ex:
            print(f"[HEARTBEAT] failed: {type(ex).__name__}: {ex}")
            return False
    
    def one_pass(self):
        """Single sync iteration: heartbeat → events → roster → stops."""
        self.send_heartbeat()
        ok, failed = self.push_queued_events()
        if ok or failed:
            print(f"[SYNC] events: {ok} pushed, {failed} queued")
        self.pull_roster()
        self.pull_stops()
    
    def run_loop(self, interval: int = 30):
        """Run continuous sync loop."""
        self.running = True
        print(f"Sync client running every {interval}s. Press Ctrl+C to stop.")
        while self.running:
            try:
                self.one_pass()
                time.sleep(interval)
            except KeyboardInterrupt:
                self.running = False
                print("[SYNC] stopped")
                break
            except Exception as e:
                print(f"[SYNC] error: {e}")
                time.sleep(interval)
    
    def start_background(self, interval: int = 30):
        """Start sync in a background thread."""
        thread = threading.Thread(target=lambda: self.run_loop(interval), daemon=True)
        thread.start()
        return thread


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", default=os.environ.get("BACKEND_URL"), help="Backend URL (from .env or CLI)")
    parser.add_argument("--bus-id", default=os.environ.get("BUS_ID"), help="Bus ID (from .env or CLI)")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    
    # Validate required args
    if not args.backend_url:
        print("[ERROR] --backend-url is required or set BACKEND_URL in .env")
        exit(1)
    if not args.bus_id:
        print("[ERROR] --bus-id is required or set BUS_ID in .env")
        exit(1)
    
    db.init_db()
    client = SyncClient(args.backend_url, args.bus_id)
    
    if args.loop:
        client.run_loop(args.interval)
    else:
        client.one_pass()


if __name__ == "__main__":
    main()
