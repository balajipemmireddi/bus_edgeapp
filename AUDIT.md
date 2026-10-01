# Bus Edge App - Code Audit & Fixes

## Issues Found

### 1. **enrollment_dashboard.py** - Missing `db` import
**File**: enrollment_dashboard.py, Line 408
**Issue**: `main()` calls `db.init_db()` but `db` is not imported
**Status**: ❌ BROKEN
**Fix**: Remove unnecessary `db.init_db()` call (dashboard doesn't use local db, just sends to backend)

### 2. **enrollment_dashboard.py** - Undefined variable in route handler
**File**: enrollment_dashboard.py, Line 73
**Issue**: `app.routes` manipulation won't work as intended due to FastAPI route mechanics
**Status**: ⚠️ WORKS BUT FRAGILE
**Fix**: Use proper route dependency injection or context

### 3. **main.py** - Missing environment variable loading
**File**: main.py, Lines 1-30
**Issue**: Does NOT load `.env` file, but should use `BACKEND_URL` and `BUS_ID` from environment
**Status**: ❌ INCONSISTENT
**Fix**: Add `from dotenv import load_dotenv; load_dotenv()` at top

### 4. **main.py** - Backend URL not being read from args/env
**File**: main.py, Line 273 `--backend-url` is optional but should default to env var
**Issue**: If `--backend-url` is not passed and env var not set, sync won't start
**Status**: ⚠️ WORKS BUT UNRELIABLE
**Fix**: Default to `os.environ.get("BACKEND_URL")`

### 5. **sync_client.py** - Already FIXED ✅
Uses `os.environ.get("BACKEND_URL")` and `.get("BUS_ID")` correctly

### 6. **main.py** - Ambiguous roster handling
**File**: main.py, Line ~300 onwards
**Issue**: `stop_coords_lookup` is passed as empty dict `{}` to state machine
**Status**: ⚠️ WORKS BUT GEO-FENCE DISABLED
**Fix**: This is intentional (no GPS yet), but comment should clarify

---

## Summary of Fixes Needed

| File | Issue | Severity | Fix |
|------|-------|----------|-----|
| enrollment_dashboard.py | Missing `db` import, unused | HIGH | Remove `db.init_db()` call |
| enrollment_dashboard.py | Route handler fragile | MEDIUM | Refactor route injection |
| main.py | No `.env` loading | HIGH | Add `load_dotenv()` |
| main.py | Backend URL default | MEDIUM | Use env var as fallback |

---

## Architecture Consistency

✅ **Correct patterns used:**
- `sync_client.py` properly loads `.env` with fallback to args
- `matcher.py` uses env var for confidence threshold
- `face_processor.py` imports `face_recognition` at startup (fail-loud not fail-silent)
- `db.py` uses context managers correctly
- `state_machine.py` is pure logic (no I/O)

❌ **Inconsistent patterns:**
- `main.py` doesn't load `.env` like other modules do
- `enrollment_dashboard.py` tries to do backend initialization with no database
