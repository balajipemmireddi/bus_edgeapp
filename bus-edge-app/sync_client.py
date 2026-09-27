"""
Sync client (Phase 3): pushes locally queued events to the cloud backend, and
pulls the full roster down to keep this Pi's local cache current.

Run this periodically (e.g. every 30-60 sec via a loop, or triggered after each
event is queued) - it's resilient to no connection: failures just leave events
queued for the next attempt.

Usage:
    python3 sync_client.py --backend-url http://<BACKEND_IP>:8000 --bus-id bus_14
    python3 sync_client.py --backend-url http://<BACKEND_IP>:8000 --bus-id bus_14 --loop
"""

import argparse
import time
import requests

import db


def push_queued_events(backend_url: str) -> tuple[int, int]:
    """Returns (succeeded, failed) counts."""
    events = db.get_unsynced_events()
    ok, failed = 0, 0
    for e in events:
        payload = {
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
            resp = requests.post(f"{backend_url}/api/events", json=payload, timeout=10)
            if resp.status_code == 200:
                db.mark_synced(e["id"])
                ok += 1
            else:
                print(f"[SYNC] event {e['id']} rejected: {resp.status_code} {resp.text}")
                failed += 1
        except requests.exceptions.RequestException as ex:
            print(f"[SYNC] no connection ({ex}) - event {e['id']} stays queued for next attempt")
            failed += 1
            break  # no point hammering more requests if the connection itself is down
    return ok, failed


def pull_roster(backend_url: str, bus_id: str) -> bool:
    try:
        resp = requests.get(f"{backend_url}/api/bus/{bus_id}/roster", timeout=15)
        resp.raise_for_status()
        roster = resp.json()
        db.replace_roster(roster)
        print(f"[SYNC] roster updated: {len(roster)} students")
        return True
    except requests.exceptions.RequestException as ex:
        print(f"[SYNC] roster pull failed ({ex}) - keeping existing local roster")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", required=True, help="e.g. http://192.168.1.50:8000")
    parser.add_argument("--bus-id", required=True)
    parser.add_argument("--loop", action="store_true", help="Run continuously instead of once")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between attempts in --loop mode")
    args = parser.parse_args()

    db.init_db()
    backend_url = args.backend_url.rstrip("/")

    def one_pass():
        ok, failed = push_queued_events(backend_url)
        if ok or failed:
            print(f"[SYNC] events: {ok} pushed, {failed} still queued")
        pull_roster(backend_url, args.bus_id)

    if args.loop:
        print(f"Sync client running every {args.interval}s. Ctrl+C to stop.")
        while True:
            one_pass()
            time.sleep(args.interval)
    else:
        one_pass()


if __name__ == "__main__":
    main()
