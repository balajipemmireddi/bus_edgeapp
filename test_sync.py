#!/usr/bin/env python3
"""
Quick test to verify sync_client can reach the backend.
Run this on the Pi to diagnose connectivity issues.

Usage:
    python3 test_sync.py --backend-url http://192.168.29.83:8000 --bus-id bus_14
"""

import argparse
import sys
import db
from sync_client import SyncClient

def test_sync(backend_url: str, bus_id: str):
    print(f"\n🔍 Testing sync to backend: {backend_url}")
    print(f"   Bus ID: {bus_id}\n")
    
    db.init_db()
    client = SyncClient(backend_url, bus_id)
    
    # Test 1: Heartbeat
    print("1️⃣  Testing heartbeat...")
    if client.send_heartbeat():
        print("   ✓ Heartbeat successful\n")
    else:
        print("   ✗ Heartbeat failed - backend unreachable\n")
        return False
    
    # Test 2: Pull roster
    print("2️⃣  Pulling roster...")
    if client.pull_roster():
        roster = db.load_roster()
        print(f"   ✓ Roster synced: {len(roster)} students\n")
    else:
        print("   ✗ Roster pull failed\n")
        return False
    
    # Test 3: Check for queued events
    print("3️⃣  Checking local event queue...")
    events = db.get_unsynced_events()
    if events:
        print(f"   Found {len(events)} queued events:")
        for e in events[:5]:  # Show first 5
            print(f"     - {e['child_id']}: {e['event_type']} @ {e['timestamp']}")
        
        # Test 4: Push events
        print("\n4️⃣  Pushing queued events...")
        ok, failed = client.push_queued_events()
        print(f"   ✓ Pushed {ok}, failed {failed}\n")
    else:
        print("   No queued events (this is normal if no detections yet)\n")
    
    print("✅ Sync test complete!")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--bus-id", required=True)
    args = parser.parse_args()
    
    success = test_sync(args.backend_url, args.bus_id)
    sys.exit(0 if success else 1)
