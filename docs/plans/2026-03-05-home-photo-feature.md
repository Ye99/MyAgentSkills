# Home GPS Feature Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `HOME_GPS` env var / `--home-photo` CLI arg so clusters near home get landmark `"Home"` with country inferred once, and the script hard-fails if neither is set.

**Architecture:** At startup, resolve home GPS from `HOME_GPS` env var (priority 1) or `--home-photo` exiftool extraction (priority 2); hard-fail if neither is set. Thread `home_gps` through `main` → `rename_folder_from_itinerary` / `process_folder_tree` → `infer_pending_cluster_infos`. Pre-filter home clusters (centroid ≤200m) before the 3 dispatch paths; infer country once for the first home cluster, reuse for all subsequent.

**Tech Stack:** Python 3, exiftool, existing `haversine_m()` and `infer_landmark_info()`

---

### Task 1: Add `_HOME_DISTANCE_M` constant and `extract_home_gps()` function

**Files:**
- Modify: `scripts/rename_folder_by_ai_itinerary.py:35` (add constant after `UNKNOWN_LANDMARK`)
- Modify: `scripts/rename_folder_by_ai_itinerary.py:315` (add `extract_home_gps` before `extract_media_points`)
- Test: `scripts/tests/test_rename_folder_by_ai_itinerary.py`

**Step 1: Write failing tests**

```python
def test_extract_home_gps_valid(tmp_path, monkeypatch):
    """extract_home_gps returns (lat, lon) from exiftool output."""
    fake_json = json.dumps([{"SourceFile": "x.heic", "GPSLatitude": 47.694, "GPSLongitude": -122.101}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    lat, lon = M.extract_home_gps(tmp_path / "x.heic")
    assert abs(lat - 47.694) < 1e-6
    assert abs(lon - (-122.101)) < 1e-6


def test_extract_home_gps_no_gps(tmp_path, monkeypatch):
    """extract_home_gps raises SystemExit when photo has no GPS."""
    fake_json = json.dumps([{"SourceFile": "x.heic"}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    with pytest.raises(SystemExit):
        M.extract_home_gps(tmp_path / "x.heic")


def test_extract_home_gps_zero_gps(tmp_path, monkeypatch):
    """extract_home_gps rejects (0,0) GPS as invalid."""
    fake_json = json.dumps([{"SourceFile": "x.heic", "GPSLatitude": 0.0, "GPSLongitude": 0.0}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    with pytest.raises(SystemExit):
        M.extract_home_gps(tmp_path / "x.heic")
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -k "test_extract_home_gps" -v`
Expected: FAIL — `extract_home_gps` not defined

**Step 3: Implement**

After line 35 (`UNKNOWN_LANDMARK = "UnknownLandmark"`), add:

```python
_HOME_DISTANCE_M = 200.0
HOME_LANDMARK = "Home"
```

Before `extract_media_points` (before line 315), add:

```python
def extract_home_gps(photo_path: Path) -> tuple[float, float]:
    """Extract GPS lat/lon from a single photo using exiftool."""
    cmd = [
        "exiftool", "-j", "-n",
        "-GPSLatitude", "-GPSLongitude",
        str(photo_path),
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    records = json.loads(proc.stdout)
    if not records or not isinstance(records[0], dict):
        raise SystemExit(f"exiftool returned no data for {photo_path}")
    rec = records[0]
    lat = rec.get("GPSLatitude")
    lon = rec.get("GPSLongitude")
    if not isinstance(lat, (float, int)) or not isinstance(lon, (float, int)):
        raise SystemExit(f"No GPS coordinates in {photo_path}")
    if float(lat) == 0.0 and float(lon) == 0.0:
        raise SystemExit(f"GPS is (0,0) in {photo_path} — likely invalid")
    return float(lat), float(lon)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -k "test_extract_home_gps" -v`
Expected: 3 PASS

**Step 5: Commit**

```
feat: add extract_home_gps and HOME constants
```

---

### Task 2: Add `resolve_home_gps()` function

**Files:**
- Modify: `scripts/rename_folder_by_ai_itinerary.py` (after `extract_home_gps`)
- Test: `scripts/tests/test_rename_folder_by_ai_itinerary.py`

**Step 1: Write failing tests**

```python
def test_resolve_home_gps_from_env(monkeypatch):
    """HOME_GPS env var takes priority."""
    monkeypatch.setenv("HOME_GPS", "47.694,-122.101")
    lat, lon = M.resolve_home_gps(home_photo=None)
    assert abs(lat - 47.694) < 1e-6
    assert abs(lon - (-122.101)) < 1e-6


def test_resolve_home_gps_from_photo(tmp_path, monkeypatch):
    """Falls back to --home-photo when HOME_GPS not set."""
    monkeypatch.delenv("HOME_GPS", raising=False)
    fake_json = json.dumps([{"SourceFile": "x.heic", "GPSLatitude": 47.694, "GPSLongitude": -122.101}])
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"stdout": fake_json})(),
    )
    lat, lon = M.resolve_home_gps(home_photo=tmp_path / "x.heic")
    assert abs(lat - 47.694) < 1e-6


def test_resolve_home_gps_neither_set(monkeypatch):
    """Hard fail when neither HOME_GPS nor --home-photo is set."""
    monkeypatch.delenv("HOME_GPS", raising=False)
    with pytest.raises(SystemExit):
        M.resolve_home_gps(home_photo=None)


def test_resolve_home_gps_malformed_env(monkeypatch):
    """Malformed HOME_GPS env var causes SystemExit."""
    monkeypatch.setenv("HOME_GPS", "not-a-coordinate")
    with pytest.raises(SystemExit):
        M.resolve_home_gps(home_photo=None)
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -k "test_resolve_home_gps" -v`
Expected: FAIL — `resolve_home_gps` not defined

**Step 3: Implement**

```python
def resolve_home_gps(home_photo: Path | None = None) -> tuple[float, float]:
    """Resolve home GPS: HOME_GPS env var (priority), then --home-photo, else hard fail."""
    env_val = os.environ.get("HOME_GPS")
    if env_val:
        parts = env_val.split(",")
        if len(parts) != 2:
            raise SystemExit(f"HOME_GPS must be 'lat,lon', got: {env_val!r}")
        try:
            lat, lon = float(parts[0].strip()), float(parts[1].strip())
        except ValueError:
            raise SystemExit(f"HOME_GPS must be 'lat,lon' with valid floats, got: {env_val!r}")
        if lat == 0.0 and lon == 0.0:
            raise SystemExit("HOME_GPS is (0,0) — likely invalid")
        return lat, lon
    if home_photo is not None:
        return extract_home_gps(home_photo)
    raise SystemExit(
        "Error: HOME_GPS environment variable or --home-photo argument required.\n"
        "Set HOME_GPS=lat,lon or pass --home-photo <path>."
    )
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -k "test_resolve_home_gps" -v`
Expected: 4 PASS

**Step 5: Commit**

```
feat: add resolve_home_gps with env var priority and hard fail
```

---

### Task 3: Add `--home-photo` CLI arg and call `resolve_home_gps` in `main()`

**Files:**
- Modify: `scripts/rename_folder_by_ai_itinerary.py:2306` (`build_parser`) — add arg
- Modify: `scripts/rename_folder_by_ai_itinerary.py:2373` (`main`) — call `resolve_home_gps`, pass to both code paths

**Step 1: Implement CLI arg**

In `build_parser()`, before `return parser` (line 2370):

```python
    parser.add_argument(
        "--home-photo",
        help="Path to a photo taken at home; GPS extracted via exiftool. "
             "Overridden by HOME_GPS env var if set.",
    )
```

**Step 2: Call `resolve_home_gps` in `main()`**

After `folder` validation (line 2379), before `has_day_children` (line 2381):

```python
    home_photo_path = Path(args.home_photo).expanduser().resolve() if args.home_photo else None
    home_gps = resolve_home_gps(home_photo=home_photo_path)
```

Pass `home_gps=home_gps` to both `rename_folder_from_itinerary` and `process_folder_tree` calls.

**Step 3: Run full test suite**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -v`
Expected: All existing tests still pass (they'll need HOME_GPS set in env or monkeypatched)

**Step 4: Commit**

```
feat: add --home-photo CLI arg and resolve home GPS in main
```

---

### Task 4: Thread `home_gps` through `rename_folder_from_itinerary` and `process_folder_tree`

**Files:**
- Modify: `scripts/rename_folder_by_ai_itinerary.py:1895` (`rename_folder_from_itinerary`) — add param
- Modify: `scripts/rename_folder_by_ai_itinerary.py:1588` (`process_folder_tree`) — add param, pass to inner call
- Modify: `scripts/rename_folder_by_ai_itinerary.py:2015` — pass to `infer_pending_cluster_infos`

**Step 1: Add `home_gps` parameter**

To `rename_folder_from_itinerary` signature (after `resume`):

```python
    home_gps: tuple[float, float] = (0.0, 0.0),
```

To `process_folder_tree` signature (after `resume`):

```python
    home_gps: tuple[float, float] = (0.0, 0.0),
```

Pass through `process_folder_tree` → `run_folder` → `rename_folder_from_itinerary`:

```python
    home_gps=home_gps,
```

Pass through `rename_folder_from_itinerary` → `infer_pending_cluster_infos`:

```python
    home_gps=home_gps,
```

**Step 2: Run full test suite**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -v`
Expected: All pass (new param has default, no behavioral change yet)

**Step 3: Commit**

```
feat: thread home_gps param through processing chain
```

---

### Task 5: Pre-filter home clusters in `infer_pending_cluster_infos`

**Files:**
- Modify: `scripts/rename_folder_by_ai_itinerary.py:1004` (`infer_pending_cluster_infos`) — add param, add pre-filter
- Test: `scripts/tests/test_rename_folder_by_ai_itinerary.py`

**Step 1: Write failing tests**

```python
def test_home_cluster_skips_inference(monkeypatch):
    """Cluster within 200m of home gets Home landmark without inference."""
    home_gps = (47.694, -122.101)
    # Cluster centroid at same location
    cluster = M.LocationCluster(points=[
        M.MediaPoint(source_file="a.jpg", lat=47.694, lon=-122.101,
                     timestamp=datetime(2025, 7, 1, 10, 0)),
    ])
    pending = [(0, cluster)]
    # Mock infer_landmark_info to return country only
    call_count = {"n": 0}
    original_infer = M.infer_landmark_info
    def fake_infer(*a, **kw):
        call_count["n"] += 1
        return {"landmark_name": "ShouldBeOverridden", "country_name": "UnitedStates"}
    monkeypatch.setattr(M, "infer_landmark_info", fake_infer)
    completed, failure, report = M.infer_pending_cluster_infos(
        pending,
        opencode_timeout_sec=10,
        opencode_retries=1,
        opencode_backoff_sec=0.1,
        opencode_model=None,
        inference_workers=1,
        home_gps=home_gps,
    )
    assert len(completed) == 1
    assert completed[0][2]["landmark_name"] == "Home"
    assert completed[0][2]["country_name"] == "UnitedStates"
    assert call_count["n"] == 1  # country inferred once


def test_home_cluster_beyond_threshold_gets_normal_inference(monkeypatch):
    """Cluster >200m from home gets normal inference."""
    home_gps = (47.694, -122.101)
    # Cluster centroid ~5km away
    cluster = M.LocationCluster(points=[
        M.MediaPoint(source_file="a.jpg", lat=47.74, lon=-122.101,
                     timestamp=datetime(2025, 7, 1, 10, 0)),
    ])
    pending = [(0, cluster)]
    def fake_infer(*a, **kw):
        return {"landmark_name": "SomePark", "country_name": "UnitedStates"}
    monkeypatch.setattr(M, "infer_landmark_info", fake_infer)
    completed, failure, report = M.infer_pending_cluster_infos(
        pending,
        opencode_timeout_sec=10,
        opencode_retries=1,
        opencode_backoff_sec=0.1,
        opencode_model=None,
        inference_workers=1,
        home_gps=home_gps,
    )
    assert completed[0][2]["landmark_name"] == "SomePark"


def test_home_country_inferred_once_for_multiple_clusters(monkeypatch):
    """Multiple home clusters: country inferred once, reused for all."""
    home_gps = (47.694, -122.101)
    c1 = M.LocationCluster(points=[
        M.MediaPoint(source_file="a.jpg", lat=47.694, lon=-122.101,
                     timestamp=datetime(2025, 7, 1, 10, 0)),
    ])
    c2 = M.LocationCluster(points=[
        M.MediaPoint(source_file="b.jpg", lat=47.6941, lon=-122.1008,
                     timestamp=datetime(2025, 7, 1, 11, 0)),
    ])
    pending = [(0, c1), (1, c2)]
    call_count = {"n": 0}
    def fake_infer(*a, **kw):
        call_count["n"] += 1
        return {"landmark_name": "X", "country_name": "UnitedStates"}
    monkeypatch.setattr(M, "infer_landmark_info", fake_infer)
    completed, failure, report = M.infer_pending_cluster_infos(
        pending,
        opencode_timeout_sec=10,
        opencode_retries=1,
        opencode_backoff_sec=0.1,
        opencode_model=None,
        inference_workers=1,
        home_gps=home_gps,
    )
    assert len(completed) == 2
    assert all(c[2]["landmark_name"] == "Home" for c in completed)
    assert all(c[2]["country_name"] == "UnitedStates" for c in completed)
    assert call_count["n"] == 1  # only one inference call for country
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -k "test_home_cluster" -v`
Expected: FAIL — `home_gps` not accepted / no pre-filter logic

**Step 3: Implement pre-filter**

Add `home_gps` param to `infer_pending_cluster_infos` signature:

```python
    home_gps: tuple[float, float] = (0.0, 0.0),
```

After the early return for empty `pending_clusters` (line 1035), before line 1037, insert:

```python
    # Pre-filter home clusters
    home_lat, home_lon = home_gps
    if home_lat != 0.0 or home_lon != 0.0:
        home_country: str | None = None
        remaining: list[tuple[int, LocationCluster]] = []
        for idx, cluster in pending_clusters:
            c_lat, c_lon = cluster.centroid
            if haversine_m(c_lat, c_lon, home_lat, home_lon) <= _HOME_DISTANCE_M:
                if home_country is None:
                    # Infer country once for the first home cluster
                    try:
                        info = infer_landmark_info(
                            c_lat, c_lon,
                            start_time=cluster.start_time,
                            end_time=cluster.end_time,
                            sample_count=len(cluster.points),
                            opencode_timeout_sec=opencode_timeout_sec,
                            opencode_retries=opencode_retries,
                            opencode_backoff_sec=opencode_backoff_sec,
                            opencode_model=opencode_model,
                        )
                        home_country = info.get("country_name", "")
                    except InferenceExhaustedError:
                        home_country = ""
                home_info = {"landmark_name": HOME_LANDMARK, "country_name": home_country}
                completed.append((idx, cluster, home_info))
            else:
                remaining.append((idx, cluster))
        pending_clusters = remaining
        if not pending_clusters:
            return completed, None, worker_report
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -k "test_home_cluster" -v`
Expected: 3 PASS

**Step 5: Run full test suite**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -v`
Expected: All pass

**Step 6: Commit**

```
feat: pre-filter home clusters in infer_pending_cluster_infos
```

---

### Task 6: Fix existing tests to work with HOME_GPS requirement

**Files:**
- Modify: `scripts/tests/test_rename_folder_by_ai_itinerary.py`

Since `main()` now hard-fails without HOME_GPS, existing tests that call `main()` or integration-level functions need `HOME_GPS` set. Add a session-scoped or module-level fixture:

**Step 1: Add fixture**

```python
@pytest.fixture(autouse=True)
def set_home_gps_env(monkeypatch):
    """Ensure HOME_GPS is set for all tests."""
    monkeypatch.setenv("HOME_GPS", "0.001,0.001")
```

Use a benign non-zero GPS that won't match any test cluster centroids (0.001, 0.001 is in the Gulf of Guinea — no test data there).

**Step 2: Run full test suite**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -v`
Expected: All 57+ tests pass

**Step 3: Commit**

```
test: add HOME_GPS fixture for all tests
```

---

### Task 7: Add `Home` to folder name integration test

**Files:**
- Test: `scripts/tests/test_rename_folder_by_ai_itinerary.py`

**Step 1: Write integration test**

```python
def test_home_landmark_in_folder_name():
    """Home landmark appears in the final folder name."""
    cluster_infos = [
        (M.LocationCluster(points=[
            M.MediaPoint("a.jpg", 47.694, -122.101, datetime(2025, 7, 1, 10)),
        ]), {"landmark_name": "Home", "country_name": "UnitedStates"}),
        (M.LocationCluster(points=[
            M.MediaPoint("b.jpg", 47.6, -122.3, datetime(2025, 7, 1, 14)),
        ]), {"landmark_name": "PikePlaceMarket", "country_name": "UnitedStates"}),
    ]
    landmarks = M.build_folder_landmark_tokens(cluster_infos, max_landmarks=8)
    assert "Home" in landmarks
    assert "PikePlaceMarket" in landmarks
```

**Step 2: Run test**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -k "test_home_landmark_in_folder_name" -v`
Expected: PASS (Home is just a regular landmark string, not filtered like UnknownLandmark)

**Step 3: Commit**

```
test: verify Home landmark appears in folder name
```

---

### Task 8: Run full test suite, verify, commit all

**Step 1: Run full suite**

Run: `python -m pytest scripts/tests/test_rename_folder_by_ai_itinerary.py -v`
Expected: All tests pass (57 existing + ~8 new = ~65 tests)

**Step 2: Final commit and push**

```
feat: --home-photo and HOME_GPS support for home cluster detection
```
