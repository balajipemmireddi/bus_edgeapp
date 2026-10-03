"""
Per-child state machine — the heart of the dedup/cost-control logic (spec §7).

States: NOT_PICKED_UP -> ON_BUS_TO_SCHOOL -> AT_SCHOOL -> ON_BUS_TO_HOME -> DROPPED

Only two transitions ever produce a cloud event / WhatsApp send:
    NOT_PICKED_UP -> ON_BUS_TO_SCHOOL   (fires PICKED_UP)
    ON_BUS_TO_HOME -> DROPPED           (fires DROPPED)

Everything else is either a silent local state update or a discarded no-op.
This is what keeps repeat sightings of the same child free of cost (§7.4).
"""

from dataclasses import dataclass
from enum import Enum
import math

import db


class Leg(str, Enum):
    AM = "AM"
    PM = "PM"


class Direction(str, Enum):
    ENTERING = "ENTERING"
    EXITING = "EXITING"


class Outcome(str, Enum):
    FIRE_PICKED_UP = "FIRE_PICKED_UP"
    FIRE_DROPPED = "FIRE_DROPPED"
    FIRE_EXIT_UNEXPECTED = "FIRE_EXIT_UNEXPECTED"
    SILENT_UPDATE = "SILENT_UPDATE"
    DISCARD = "DISCARD"


@dataclass
class Detection:
    child_id: str
    confidence: float
    direction: Direction
    gps: tuple[float, float] | None


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    """Distance in metres between two lat/lng points."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def within_geofence(gps, stop_coords, radius_m=125) -> bool:
    if gps is None or stop_coords is None:
        # No GPS fix yet, or stop has no coordinates configured:
        # fail open on presence, i.e. don't block the event purely on
        # missing location data - direction + state are still required.
        return True
    return haversine_m(gps[0], gps[1], stop_coords[0], stop_coords[1]) <= radius_m


def process_detection(
    detection: Detection,
    leg: Leg,
    student: dict,
    stop_coords_lookup: dict[str, tuple[float, float]],
    bus_id: str | None = None,
) -> Outcome:
    """
    Implements the trigger table from spec §7.4.
    `student` is a roster row (see db.load_roster) - carries pickup_stop_id / drop_stop_id.
    `stop_coords_lookup` maps stop_id -> (lat, lng), synced down with the roster.
    """
    child_id = detection.child_id
    status = db.get_status(child_id)

    if not db.is_expected_today(child_id):
        # Daily attendance flag - parent/staff marked this child absent today.
        # Don't escalate or fire anything even if somehow detected (e.g. a sibling's
        # bag was scanned, or a mistaken match) - review queue handles the mismatch.
        return Outcome.DISCARD

    pickup_stop = stop_coords_lookup.get(student.get("pickup_stop_id"))
    drop_stop = stop_coords_lookup.get(student.get("drop_stop_id"))

    if (
        leg == Leg.AM
        and detection.direction == Direction.ENTERING
        and status == "NOT_PICKED_UP"
        and within_geofence(detection.gps, pickup_stop)
    ):
        if bus_id:
            db.record_transition_and_event(
                child_id, "ON_BUS_TO_SCHOOL", "PICKED_UP", detection.confidence,
                "", detection.gps, bus_id,
            )
        else:
            db.set_status(child_id, "ON_BUS_TO_SCHOOL")
        return Outcome.FIRE_PICKED_UP

    if (
        leg == Leg.AM
        and detection.direction == Direction.EXITING
        and status == "ON_BUS_TO_SCHOOL"
    ):
        db.set_status(child_id, "AT_SCHOOL")
        return Outcome.SILENT_UPDATE

    if (
        leg == Leg.PM
        and detection.direction == Direction.ENTERING
        and status == "AT_SCHOOL"
    ):
        db.set_status(child_id, "ON_BUS_TO_HOME")
        return Outcome.SILENT_UPDATE

    if (
        leg == Leg.PM
        and detection.direction == Direction.EXITING
        and status == "ON_BUS_TO_HOME"
        and within_geofence(detection.gps, drop_stop)
    ):
        if bus_id:
            db.record_transition_and_event(
                child_id, "DROPPED", "DROPPED", detection.confidence,
                "", detection.gps, bus_id,
            )
        else:
            db.set_status(child_id, "DROPPED")
        return Outcome.FIRE_DROPPED

    # Exit at a location that isn't the registered stop - don't discard silently,
    # this is the "grabbed early / unexpected exit" case from §11.
    if detection.direction == Direction.EXITING and detection.gps is not None:
        expected_stop = drop_stop if leg == Leg.PM else None
        if expected_stop and not within_geofence(detection.gps, expected_stop):
            if bus_id:
                db.queue_event(
                    child_id, "EXIT_UNEXPECTED_LOCATION", detection.confidence,
                    "", detection.gps, bus_id,
                )
            return Outcome.FIRE_EXIT_UNEXPECTED

    return Outcome.DISCARD
