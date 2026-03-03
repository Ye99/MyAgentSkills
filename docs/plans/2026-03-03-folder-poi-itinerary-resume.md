# Folder POI Itinerary Resume Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add resumable multi-day execution with persistent state, bounded retries, and freeze-only-applied semantics so split runs behave like one continuous run.

**Architecture:** Introduce a persistent state ledger keyed by folder path and update it atomically after each folder outcome. Resume decisions use filesystem reconciliation plus state transitions: applied/renamed entries are frozen, while non-applied outcomes are retried within configured caps. Keep per-run report JSON, extended with resume/retry diagnostics.

**Tech Stack:** Python 3, argparse, pathlib, json, datetime, unittest.

---

### Task 1: Add failing tests for state, resume, and retry gates

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
- Modify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`

**Step 1: Write the failing test**

```python
def test_resume_freezes_applied_and_retries_with_caps(self) -> None:
    # state includes one renamed folder, one error folder at retry_count=1,
    # and one no-landmark folder at retry_count=0
    # expect renamed skipped, error retried once, no-landmark retried once,
    # and caps enforced on next transition
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
Expected: FAIL for missing state/retry functions.

**Step 3: Write minimal implementation**

Add helper functions signatures first:

```python
def load_resume_state(path: Path) -> dict[str, Any]: ...
def save_resume_state(path: Path, state: dict[str, Any]) -> None: ...
def should_process_folder(entry: dict[str, Any], retry_cfg: dict[str, int]) -> bool: ...
```

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS for new tests.

**Step 5: Commit**

```bash
git add folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py
git commit -m "test: add resume and retry gate coverage"
```

### Task 2: Implement state ledger read/write and atomic persistence

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`
- Modify: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`

**Step 1: Write the failing test**

```python
def test_save_resume_state_writes_atomically(self) -> None:
    # verify temp-write + rename behavior and reload consistency
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
Expected: FAIL for missing atomic write behavior.

**Step 3: Write minimal implementation**

- Add state schema with version and folder map.
- Implement load default when file absent.
- Implement fail-fast on malformed JSON.
- Implement atomic write (`.tmp` then `replace`).

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

**Step 5: Commit**

```bash
git add folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py
git commit -m "feat: add atomic resume state ledger for folder runs"
```

### Task 3: Wire resume gating and retry transitions into main flow

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`
- Modify: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`

**Step 1: Write the failing test**

```python
def test_main_resume_skips_frozen_applied_and_enforces_retry_caps(self) -> None:
    # run main twice against prepared state and assert terminal exhausted statuses
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
Expected: FAIL because resume flags/logic not fully integrated.

**Step 3: Write minimal implementation**

- Add parser args:
  - `--state-json`
  - `--error-retry-max`
  - `--no-landmark-retry-max`
- Before processing each eligible folder, check state gate.
- After each outcome, update counters/status and persist state.
- Reconcile already-renamed folders from filesystem as frozen.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

**Step 5: Commit**

```bash
git add folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py
git commit -m "feat: add resumable processing with bounded retry transitions"
```

### Task 4: Extend report JSON with resume/retry diagnostics

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`
- Modify: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`

**Step 1: Write the failing test**

```python
def test_report_includes_resume_retry_statistics(self) -> None:
    # assert processed_this_run_count, retried counts, exhausted path lists
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
Expected: FAIL due missing fields.

**Step 3: Write minimal implementation**

- Extend `build_rename_report` summary and lists with retry/resume diagnostics.
- Keep existing required fields unchanged.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

**Step 5: Commit**

```bash
git add folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py
git commit -m "feat: add resume and retry diagnostics to JSON report"
```

### Task 5: Update skill docs and run full verification

**Files:**
- Modify: `folder-poi-itinerary-rename/SKILL.md`
- Modify: `README.md`

**Step 1: Update docs**

- Add resumable multi-day workflow examples with `--state-json`.
- Document retry caps and freeze-only-applied semantics.
- Document new report fields for troubleshooting.

**Step 2: Run verification commands**

Run:

```bash
python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py
```

Expected: PASS with 0 failures.

**Step 3: Commit**

```bash
git add folder-poi-itinerary-rename/SKILL.md README.md
git commit -m "docs: describe resumable multi-day runs and retry semantics"
```
