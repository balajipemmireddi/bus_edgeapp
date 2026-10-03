"""
Run with: python3 -m pytest test_core_logic.py -v
(or plain: python3 test_core_logic.py)

Covers the specific edge cases we designed around:
  - repeat sightings must not re-fire an event
  - wrong-direction / wrong-leg detections are no-ops
  - a "reboot" mid-sequence must not duplicate an already-fired event
  - geofence blocks a false pickup/drop away from the registered stop
  - twin/sibling ambiguity routes to review, not auto-match
  - daily absence flag suppresses no-show/event firing
"""

import shutil
import numpy as np
import pytest

import db
from state_machine import Detection, Leg, Direction, Outcome, process_detection
from matcher import MockBackend, match_against_roster, MatchResult

PICKUP_STOP = (17.4000, 78.4800)
DROP_STOP = (17.4000, 78.4800)   # same stop for this test's child
FAR_AWAY = (17.5000, 78.6000)    # ~15km off - outside any geofence

STOP_COORDS = {"stop_pickup_1": PICKUP_STOP, "stop_drop_1": DROP_STOP}

STUDENT = {
    "child_id": "child_001",
    "name": "Aarav",
    "encodings": [[0.1, 0.2, 0.3]],
    "assigned_bus_id": "bus_14",
    "pickup_stop_id": "stop_pickup_1",
    "drop_stop_id": "stop_drop_1",
    "twin_group": None,
}


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point db at a throwaway sqlite file per test so tests don't leak state."""
    test_db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_db()
    yield
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_morning_pickup_fires_once():
    d = Detection("child_001", 0.9, Direction.ENTERING, PICKUP_STOP)
    outcome = process_detection(d, Leg.AM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.FIRE_PICKED_UP
    assert db.get_status("child_001") == "ON_BUS_TO_SCHOOL"


def test_repeat_sighting_after_pickup_is_discarded():
    """The core cost-control requirement: same kid, same morning, no re-fire."""
    d = Detection("child_001", 0.9, Direction.ENTERING, PICKUP_STOP)
    process_detection(d, Leg.AM, STUDENT, STOP_COORDS)  # first sighting -> fires

    # Kid roams past the camera 2 more times
    for _ in range(2):
        outcome = process_detection(d, Leg.AM, STUDENT, STOP_COORDS)
        assert outcome == Outcome.DISCARD
    assert db.get_status("child_001") == "ON_BUS_TO_SCHOOL"  # unchanged


def test_full_day_sequence():
    # AM pickup
    process_detection(Detection("child_001", 0.9, Direction.ENTERING, PICKUP_STOP), Leg.AM, STUDENT, STOP_COORDS)
    # AM exit at school (silent)
    outcome = process_detection(Detection("child_001", 0.9, Direction.EXITING, None), Leg.AM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.SILENT_UPDATE
    assert db.get_status("child_001") == "AT_SCHOOL"

    # PM boarding at school (silent) - this is the exact "3pm boarding" case discussed
    outcome = process_detection(Detection("child_001", 0.9, Direction.ENTERING, None), Leg.PM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.SILENT_UPDATE
    assert db.get_status("child_001") == "ON_BUS_TO_HOME"

    # PM drop at registered stop -> fires
    outcome = process_detection(Detection("child_001", 0.9, Direction.EXITING, DROP_STOP), Leg.PM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.FIRE_DROPPED
    assert db.get_status("child_001") == "DROPPED"


def test_reboot_mid_sequence_does_not_duplicate():
    """Simulates a Pi power-loss: state must have been persisted, so re-running
    the same detection after 'reboot' (i.e. a fresh process, same db file) does
    not re-fire the already-completed transition."""
    process_detection(Detection("child_001", 0.9, Direction.ENTERING, PICKUP_STOP), Leg.AM, STUDENT, STOP_COORDS)
    assert db.get_status("child_001") == "ON_BUS_TO_SCHOOL"

    # "Reboot": nothing in memory carries over, but db.get_status() reads
    # from disk fresh, so this replays as a discard, not a duplicate fire.
    outcome = process_detection(Detection("child_001", 0.9, Direction.ENTERING, PICKUP_STOP), Leg.AM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.DISCARD


def test_wrong_direction_at_pickup_is_ignored():
    """An EXITING detection at the pickup stop before any pickup shouldn't fire drop."""
    d = Detection("child_001", 0.9, Direction.EXITING, PICKUP_STOP)
    outcome = process_detection(d, Leg.AM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.DISCARD
    assert db.get_status("child_001") == "NOT_PICKED_UP"


def test_geofence_blocks_pickup_away_from_registered_stop():
    d = Detection("child_001", 0.9, Direction.ENTERING, FAR_AWAY)
    outcome = process_detection(d, Leg.AM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.DISCARD
    assert db.get_status("child_001") == "NOT_PICKED_UP"


def test_unexpected_exit_location_is_flagged_not_silent():
    """Child grabbed early mid-route, PM leg, not at their registered drop stop."""
    # Get them to ON_BUS_TO_HOME first
    process_detection(Detection("child_001", 0.9, Direction.ENTERING, PICKUP_STOP), Leg.AM, STUDENT, STOP_COORDS)
    process_detection(Detection("child_001", 0.9, Direction.EXITING, None), Leg.AM, STUDENT, STOP_COORDS)
    process_detection(Detection("child_001", 0.9, Direction.ENTERING, None), Leg.PM, STUDENT, STOP_COORDS)

    d = Detection("child_001", 0.9, Direction.EXITING, FAR_AWAY)
    outcome = process_detection(d, Leg.PM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.FIRE_EXIT_UNEXPECTED


def test_absent_today_suppresses_events():
    db.mark_absent_today("child_001")
    d = Detection("child_001", 0.9, Direction.ENTERING, PICKUP_STOP)
    outcome = process_detection(d, Leg.AM, STUDENT, STOP_COORDS)
    assert outcome == Outcome.DISCARD
    assert db.get_status("child_001") == "NOT_PICKED_UP"


# ---- Matcher tests (using MockBackend in place of dlib) ----

def test_matcher_finds_correct_child():
    backend = MockBackend()
    roster = [
        {"child_id": "a", "encodings": [[0.0, 0.0, 0.0]], "twin_group": None},
        {"child_id": "b", "encodings": [[5.0, 5.0, 5.0]], "twin_group": None},
    ]
    result = match_against_roster([0.01, 0.0, 0.0], roster, backend)
    assert result.child_id == "a"
    assert result.confidence > matcher_threshold()


def test_matcher_flags_twin_ambiguity():
    backend = MockBackend()
    roster = [
        {"child_id": "twin_a", "encodings": [[0.0, 0.0, 0.0]], "twin_group": "twins_1"},
        {"child_id": "twin_b", "encodings": [[0.02, 0.0, 0.0]], "twin_group": "twins_1"},
    ]
    result = match_against_roster([0.01, 0.0, 0.0], roster, backend)
    assert result.is_ambiguous is True


def test_matcher_low_confidence_returns_no_match():
    backend = MockBackend()
    roster = [{"child_id": "a", "encodings": [[10.0, 10.0, 10.0]], "twin_group": None}]
    result = match_against_roster([0.0, 0.0, 0.0], roster, backend)
    assert result.child_id is None


def matcher_threshold():
    from matcher import CONFIDENCE_THRESHOLD
    return CONFIDENCE_THRESHOLD


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
