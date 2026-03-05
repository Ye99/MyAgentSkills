# AI Itinerary Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Harden `rename_folder_by_ai_itinerary.py` for safer filesystem behavior, resume correctness, and low-risk performance improvements while keeping behavior simple and predictable.

**Architecture:** Keep the existing single-script flow, then add small targeted guards and helpers: strict source-path validation, resume-input fingerprint checks, explicit leftover-file reporting in split mode, ratio CLI validation, and incremental centroid math plus less expensive day-folder discovery. Avoid concurrency and avoid introducing new dependencies.

**Tech Stack:** Python 3, argparse, pathlib, hashlib, json, unittest.

---

### Task 1: Add failing tests for source-file safety and ratio validation

**Files:**
- Modify: `AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
- Modify: `AI-folder-poi-itinerary-rename/scripts/rename_folder_by_ai_itinerary.py`

**Step 1: Write the failing test**

```python
def test_extract_media_points_skips_missing_sourcefile(self) -> None:
    # EXIF row with GPS but missing SourceFile should not yield a MediaPoint.

def test_build_parser_rejects_invalid_ratio_values(self) -> None:
    # --ratio 0 and --ratio 1.1 should exit with argparse error.
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest -v AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
Expected: FAIL because current implementation accepts invalid ratio and trusts SourceFile.

**Step 3: Write minimal implementation**

- Add parser type validator for ratio: `0 < ratio <= 1`.
- Skip EXIF entries with missing/blank `SourceFile`.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

### Task 2: Add failing tests for resume fingerprint invalidation

**Files:**
- Modify: `AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
- Modify: `AI-folder-poi-itinerary-rename/scripts/rename_folder_by_ai_itinerary.py`

**Step 1: Write the failing test**

```python
def test_resume_ignores_completed_clusters_when_input_fingerprint_changes(self) -> None:
    # State has completed_cluster_infos from old file set.
    # New run with changed file list must recompute cluster infos.
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest -v AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
Expected: FAIL because stale completed clusters are reused by index.

**Step 3: Write minimal implementation**

- Compute deterministic input fingerprint from relevant media source paths.
- Persist fingerprint in state.
- On resume, reuse completed clusters only when fingerprint matches.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

### Task 3: Add failing tests for split leftovers reporting and move safety

**Files:**
- Modify: `AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
- Modify: `AI-folder-poi-itinerary-rename/scripts/rename_folder_by_ai_itinerary.py`

**Step 1: Write the failing test**

```python
def test_split_reports_unassigned_non_gps_files(self) -> None:
    # Split result should include leftover count and names for files not moved.

def test_apply_split_skips_invalid_source_paths(self) -> None:
    # Non-file or out-of-folder source paths are rejected safely.
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest -v AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
Expected: FAIL because leftovers/validation are not fully enforced.

**Step 3: Write minimal implementation**

- Add `leftover_media_count` and `leftover_media_examples` to split and single outputs.
- Add `is_safe_source_for_move` guard before move operations.
- Surface skipped invalid sources in report/state.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

### Task 4: Add low-risk performance improvements with tests intact

**Files:**
- Modify: `AI-folder-poi-itinerary-rename/scripts/rename_folder_by_ai_itinerary.py`
- Modify: `AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`

**Step 1: Implement incremental centroid math**

- Store `lat_sum`/`lon_sum` in `LocationCluster`; update on append.
- Keep centroid behavior unchanged.

**Step 2: Optimize day-folder discovery**

- Stream traversal and sort only matched day folders.

**Step 3: Verify with tests**

Run: `python3 -m unittest -v AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
Expected: PASS with no behavior regressions.

### Task 5: Final verification and docs sync

**Files:**
- Modify: `AI-folder-poi-itinerary-rename/SKILL.md`

**Step 1: Update docs for validated ratio behavior and leftovers reporting**

- Document ratio validation and leftover reporting fields.

**Step 2: Run final tests**

Run: `python3 -m unittest -v AI-folder-poi-itinerary-rename/scripts/tests/test_rename_folder_by_ai_itinerary.py`
Expected: PASS.
