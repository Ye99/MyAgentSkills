# Folder POI Itinerary Rename Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a reusable skill that derives itinerary-ordered POI labels from geo-tagged media and appends them to a folder name.

**Architecture:** Use `exiftool` to extract GPS and timestamps, sample only GPS-bearing media, cluster by coordinate distance, label each cluster via LocationIQ with landmark-first fallback, then build a deduplicated time-ordered suffix and optionally rename the folder.

**Tech Stack:** Python 3 standard library, `exiftool`, LocationIQ Nearby API, `unittest`.

---

### Task 1: Scaffold skill files

**Files:**
- Create: `folder-poi-itinerary-rename/SKILL.md`
- Create: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`
- Create: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`
- Modify: `README.md`

### Task 2: Add failing tests for ranking, dedupe, and itinerary ordering

**Files:**
- Test: `folder-poi-itinerary-rename/scripts/tests/test_rename_folder_with_poi_itinerary.py`

### Task 3: Implement minimal script logic to pass tests

**Files:**
- Modify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`

### Task 4: Verify script behavior on representative folders

**Files:**
- Verify: `folder-poi-itinerary-rename/scripts/rename_folder_with_poi_itinerary.py`

### Task 5: Update docs

**Files:**
- Modify: `README.md`
