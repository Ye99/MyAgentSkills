# Folder POI Ctrl+C Resume Safety Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Ctrl+C graceful interruption so long runs can stop safely and resume without state corruption, missed folders, or media-file risk.

**Architecture:** Introduce a signal-aware shutdown controller in `main()` that marks first SIGINT as graceful-stop requested and second SIGINT as forced stop. Persist state atomically after each folder and at graceful termination. Extend report with interruption metadata and add a coverage invariant check to ensure discovered folders are either completed or pending.

**Tech Stack:** Python 3, argparse, signal, pathlib, json, datetime, unittest.

---

### Task 1: Add failing tests for graceful SIGINT shutdown behavior

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
- Modify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`

**Step 1: Write the failing test**

```python
def test_main_graceful_sigint_stops_before_next_folder(self) -> None:
    # Arrange two eligible folders and patch process_single_folder
    # to set shutdown flag after first folder.
    # Expect second folder not processed and report marks interrupted.
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
Expected: FAIL due missing graceful interrupt state handling.

**Step 3: Write minimal implementation**

- Add shutdown controller and loop guard before starting each folder.
- Add first/second SIGINT semantics.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

**Step 5: Commit**

```bash
git add folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py
git commit -m "test: add graceful SIGINT shutdown coverage"
```

### Task 2: Add interruption metadata and coverage invariant tests

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
- Modify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`

**Step 1: Write the failing test**

```python
def test_report_contains_interrupt_metadata_and_pending_ids(self) -> None:
    # Expect interrupted, interrupt_source, pending_folder_ids, last_completed_folder_id

def test_coverage_invariant_flags_missed_folder(self) -> None:
    # Simulate one discovered folder absent from terminal and pending sets.
    # Expect coverage_check_failed true and non-zero exit.
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
Expected: FAIL because report/invariant fields are missing.

**Step 3: Write minimal implementation**

- Extend report with interruption metadata and coverage fields.
- Compute discovered IDs, terminal IDs, and pending IDs.
- Fail fast if invariant does not hold.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

**Step 5: Commit**

```bash
git add folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py
git commit -m "feat: add interruption metadata and coverage invariant checks"
```

### Task 3: Add media-safety verification test and documentation

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
- Modify: `folder-poi-itinerary-rename/SKILL.md`
- Modify: `README.md`

**Step 1: Write the failing test**

```python
def test_interruption_path_does_not_mutate_media_files(self) -> None:
    # Verify only folder rename path is invoked; no file-level writes/deletes.
```

**Step 2: Run test to verify it fails**

Run: `python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
Expected: FAIL without explicit behavior/assertions.

**Step 3: Write minimal implementation and docs**

- Ensure interruption path exits loop safely and persists state/report.
- Document Ctrl+C graceful semantics and media safety statements in skill docs.

**Step 4: Run test to verify it passes**

Run: same unittest command
Expected: PASS.

**Step 5: Commit**

```bash
git add folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py folder-poi-itinerary-rename/SKILL.md README.md
git commit -m "docs: add Ctrl+C graceful resume and media safety guidance"
```

### Task 4: Validate on prior July range with interruption and resume

**Files:**
- Modify: none (execution verification)

**Step 1: Run interrupted dry-run using shared state/report paths**

Run:

```bash
python3 folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py \
  "/home/ye/Pictures/2025" \
  --report-json "/home/ye/Pictures/2025/folder_poi_itinerary_rename_report.json" \
  --state-json "/home/ye/Pictures/2025/folder_poi_itinerary_rename_state.json"
```

Interrupt once with Ctrl+C during processing.

**Step 2: Resume run and complete**

Run the same command again and let it complete.

**Step 3: Verify outcomes**

- Verify no crash, valid JSON state/report.
- Verify interruption metadata populated in first run.
- Verify resumed run does not reprocess frozen-applied folders.
- Verify target date range coverage is complete.

**Step 4: Final verification command**

Run:

```bash
python3 -m unittest folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py
```

Expected: PASS with 0 failures.
