"""
Face matching against the full-school roster cached on this device (spec §5
full-roster-per-device decision: ~6MB of encodings, trivially fast to compare
against even at 700-1200 students).

Two backends:
  - RealBackend: uses `face_recognition` (dlib). Install on the Pi via piwheels:
        pip install face_recognition --extra-index-url https://www.piwheels.org/simple
    This is what actually runs in production.
  - MockBackend: deterministic fake encodings for testing the surrounding logic
    (state machine, dedup, db) without needing dlib compiled in this sandbox.

CONFIDENCE THRESHOLD: per spec §9, stricter than the original SecureFace project's
0.8 tolerance. face_recognition returns a *distance* (lower = better match), so
we treat "confidence" as 1 - distance for readability, and require it above
CONFIDENCE_THRESHOLD before considering it a real match; anything lower goes to
the manual review queue instead of auto-firing.
"""

from __future__ import annotations
import numpy as np

CONFIDENCE_THRESHOLD = float(__import__("os").environ.get("MATCH_CONFIDENCE_THRESHOLD", 0.45))


class MatchResult:
    def __init__(self, child_id: str | None, confidence: float, is_ambiguous: bool = False):
        self.child_id = child_id
        self.confidence = confidence
        self.is_ambiguous = is_ambiguous  # true if top-2 matches are too close to call (twins)


class RealBackend:
    """Production backend - requires `face_recognition` installed (Pi, via piwheels)."""

    def __init__(self):
        import face_recognition  # deferred import - only needed in production
        self._fr = face_recognition

    def get_encoding(self, image):
        locations = self._fr.face_locations(image)
        if not locations:
            return None, None
        encodings = self._fr.face_encodings(image, known_face_locations=locations)
        if not encodings:
            return None, None
        return encodings[0], locations[0]

    def get_all_encodings(self, image):
        """
        Batch version for continuous live recognition: detects ALL faces in one
        frame and computes their encodings in a single efficient call, instead
        of looping calls to get_encoding() per face. Returns a list of
        (encoding, location) tuples, one per detected face - empty list if none.
        """
        locations = self._fr.face_locations(image)
        if not locations:
            return []
        encodings = self._fr.face_encodings(image, known_face_locations=locations)
        return list(zip(encodings, locations))

    def compare(self, unknown_encoding, known_encoding) -> float:
        distance = self._fr.face_distance([known_encoding], unknown_encoding)[0]
        return 1.0 - distance


class MockBackend:
    """
    Deterministic stand-in for testing without dlib. Generates fake 128-d face
    encodings for testing the state machine and sync logic without needing dlib
    installed. Returns empty list if called (no faces detected) for deterministic
    testing.
    """

    def get_encoding(self, image):
        # Simulate: no face detected
        return None, None

    def get_all_encodings(self, image):
        # Return empty list - no faces detected (deterministic for testing)
        return []

    def compare(self, unknown_encoding, known_encoding) -> float:
        # Simulate: deterministic distance
        distance = 0.1
        return 1.0 - distance


def match_against_roster(unknown_encoding, roster: list[dict], backend) -> MatchResult:
    """
    roster: list of dicts from db.load_roster() - each with 'child_id',
    'encodings' (list of vectors, since we store multiple per child, §6 step 4),
    and 'twin_group'.

    Returns the best match above threshold, flags ambiguity if a twin-group
    sibling scores nearly as well (spec §9 - twins/siblings route to review).
    """
    best_child_id = None
    best_confidence = -1.0
    second_best_confidence = -1.0
    second_best_child_id = None
    best_twin_group = None

    for student in roster:
        # Compare against every stored encoding for this child, keep their best.
        child_best = max(
            backend.compare(unknown_encoding, enc) for enc in student["encodings"]
        )
        if child_best > best_confidence:
            second_best_confidence = best_confidence
            second_best_child_id = best_child_id
            best_confidence = child_best
            best_child_id = student["child_id"]
            best_twin_group = student.get("twin_group")
        elif child_best > second_best_confidence:
            second_best_confidence = child_best
            second_best_child_id = student["child_id"]

    if best_confidence < CONFIDENCE_THRESHOLD:
        return MatchResult(child_id=None, confidence=best_confidence)

    # Ambiguous ONLY if a DIFFERENT STUDENT is too close (twins/siblings, spec §9).
    # Don't flag as ambiguous if best and second-best are the same student
    # (multi-photo enrollment naturally has variance).
    is_ambiguous = (
        best_child_id != second_best_child_id and
        (best_confidence - second_best_confidence) < 0.15
    )

    return MatchResult(
        child_id=best_child_id,
        confidence=best_confidence,
        is_ambiguous=is_ambiguous,
    )
