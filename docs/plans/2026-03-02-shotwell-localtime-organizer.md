# Shotwell Localtime Organizer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a new skill that organizes large source trees into `%Y/%Y_%m_%d` folders using local capture date derived from EXIF/GPS-aware time conversion, with no media loss and audit reporting.

**Architecture:** Batch-read metadata via `exiftool`, classify files as media/non-media using MIME/FileType and signature cache, resolve local capture datetimes using offline timezone polygons plus IANA rules, copy media with collision-safe naming, and verify completion with content-hash checks using the find-missing-files implementation.

**Tech Stack:** Python 3 standard library, `exiftool`, `timezonefinder`, `zoneinfo`, `unittest`.

---

### Task 1: Add failing tests for classification, time conversion, and collision handling

**Files:**
- Create: `organize-photos-and-videos-by-day/scripts/tests/test_organize_media_by_local_date.py`

### Task 2: Implement organizer script minimally to satisfy tests

**Files:**
- Create: `organize-photos-and-videos-by-day/scripts/organize_media_by_local_date.py`

### Task 3: Add JSON report and no-loss verification behavior

**Files:**
- Modify: `organize-photos-and-videos-by-day/scripts/organize_media_by_local_date.py`

### Task 4: Write skill documentation and usage guidance

**Files:**
- Create: `organize-photos-and-videos-by-day/SKILL.md`
- Modify: `README.md`

### Task 5: Run tests and smoke checks

**Files:**
- Test: `organize-photos-and-videos-by-day/scripts/tests/test_organize_media_by_local_date.py`
