#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit_indexed_media.py"


def run_tool(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(directory), "--media-check", "none", *args],
        text=True,
        capture_output=True,
        check=False,
    )


def test_apply_renames_unique_subtitle_and_deletes_hash_duplicate(tmp_path: Path) -> None:
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk (1).mp4").write_bytes(b"video")
    (tmp_path / "Talk.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nHi\n"
    )

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "001. Talk.mp4").exists()
    assert (tmp_path / "001. Talk.en.srt").exists()
    assert not (tmp_path / "001. Talk (1).mp4").exists()
    assert not (tmp_path / "Talk.en.srt").exists()
    assert "PAIRS: 1 complete, 0 missing SRT, 0 missing MP4" in result.stdout

    second = run_tool(tmp_path, "--apply")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "ACTIONS: 0" in second.stdout


def test_conflicting_destination_blocks_all_changes(tmp_path: Path) -> None:
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk.en.srt").write_bytes(b"old subtitle")
    (tmp_path / "Talk.en.srt").write_bytes(b"different subtitle")

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "CONFLICT" in result.stdout
    assert (tmp_path / "001. Talk.en.srt").read_bytes() == b"old subtitle"
    assert (tmp_path / "Talk.en.srt").read_bytes() == b"different subtitle"


def test_reports_index_gap_and_missing_pair(tmp_path: Path) -> None:
    (tmp_path / "001. First.mp4").write_bytes(b"video one")
    (tmp_path / "001. First.en.srt").write_bytes(b"subtitle")
    (tmp_path / "003. Third.mp4").write_bytes(b"video three")

    result = run_tool(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "MISSING_INDEXES: 002" in result.stdout
    assert "MP4_WITHOUT_SRT: 003. Third" in result.stdout


def test_ambiguous_title_does_not_guess_prefix(tmp_path: Path) -> None:
    (tmp_path / "001. Talk.mp4").write_bytes(b"video one")
    (tmp_path / "002. Talk.mp4").write_bytes(b"video two")
    (tmp_path / "Talk.en.srt").write_bytes(b"subtitle")

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "ambiguous title" in result.stdout
    assert (tmp_path / "Talk.en.srt").exists()


def test_unindexed_hash_duplicate_keeps_indexed_canonical_name(tmp_path: Path) -> None:
    (tmp_path / "006. Session.mp4").write_bytes(b"same video")
    (tmp_path / "Session.mp4").write_bytes(b"same video")

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 1, result.stdout + result.stderr
    assert (tmp_path / "006. Session.mp4").exists()
    assert not (tmp_path / "Session.mp4").exists()
    assert "DELETE duplicate: Session.mp4" in result.stdout


def test_five_digit_indexes_pair_without_conflict(tmp_path: Path) -> None:
    (tmp_path / "00001. Chapter 1 - Setup.mp4").write_bytes(b"video one")
    (tmp_path / "00001. Chapter 1 - Setup.en.srt").write_bytes(b"subtitle one")
    (tmp_path / "00002. Chapter 2 - Prompts.mp4").write_bytes(b"video two")
    (tmp_path / "00002. Chapter 2 - Prompts.en.srt").write_bytes(b"subtitle two")

    result = run_tool(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFLICT" not in result.stdout
    assert "PAIRS: 2 complete, 0 missing SRT, 0 missing MP4" in result.stdout
    assert "UNINDEXED_MEDIA: none" in result.stdout


def test_index_gap_preserves_observed_index_width(tmp_path: Path) -> None:
    (tmp_path / "00001. First.mp4").write_bytes(b"video one")
    (tmp_path / "00001. First.en.srt").write_bytes(b"subtitle")
    (tmp_path / "00003. Third.mp4").write_bytes(b"video three")

    result = run_tool(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "MISSING_INDEXES: 00002" in result.stdout
    assert "MP4_WITHOUT_SRT: 00003. Third" in result.stdout


def test_unindexed_subtitle_renames_to_five_digit_video_prefix(tmp_path: Path) -> None:
    (tmp_path / "00007. Deep Dive.mp4").write_bytes(b"video")
    (tmp_path / "Deep Dive.en.srt").write_bytes(b"subtitle")

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "00007. Deep Dive.en.srt").exists()
    assert not (tmp_path / "Deep Dive.en.srt").exists()


def test_mixed_index_widths_pair_by_number(tmp_path: Path) -> None:
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "00001. Talk.en.srt").write_bytes(b"subtitle")

    result = run_tool(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PAIRS: 1 complete, 0 missing SRT, 0 missing MP4" in result.stdout


def test_trailing_year_parenthetical_is_part_of_title(tmp_path: Path) -> None:
    (tmp_path / "00120. AlexNet (2012).mp4").write_bytes(b"alexnet video")
    (tmp_path / "00120. AlexNet (2012).en.srt").write_bytes(b"alexnet subtitle")

    result = run_tool(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFLICT" not in result.stdout
    assert "NUMBERED_COPIES: none" in result.stdout
    assert "PAIRS: 1 complete, 0 missing SRT, 0 missing MP4" in result.stdout


def test_trailing_small_number_parenthetical_without_base_is_title(tmp_path: Path) -> None:
    (tmp_path / "00005. Foundational Concepts (20).mp4").write_bytes(b"video a")
    (tmp_path / "00005. Foundational Concepts (20).en.srt").write_bytes(b"sub a")
    (tmp_path / "00006. Prompt Engineering (15).mp4").write_bytes(b"video b")
    (tmp_path / "00006. Prompt Engineering (15).en.srt").write_bytes(b"sub b")
    (tmp_path / "00007. Lesson 3.2 - FAQ (3).mp4").write_bytes(b"video c")
    (tmp_path / "00007. Lesson 3.2 - FAQ (3).en.srt").write_bytes(b"sub c")

    result = run_tool(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFLICT" not in result.stdout
    assert "NUMBERED_COPIES: none" in result.stdout
    assert "PAIRS: 3 complete, 0 missing SRT, 0 missing MP4" in result.stdout


def test_copy_suffix_with_base_sibling_is_still_deleted(tmp_path: Path) -> None:
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk.en.srt").write_bytes(b"subtitle")
    (tmp_path / "001. Talk (1).en.srt").write_bytes(b"subtitle")

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "001. Talk.en.srt").exists()
    assert not (tmp_path / "001. Talk (1).en.srt").exists()
    assert "DELETE duplicate: 001. Talk (1).en.srt" in result.stdout


def test_copy_suffix_before_language_with_base_sibling_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk.en.srt").write_bytes(b"subtitle")
    (tmp_path / "001. Talk.en (1).srt").write_bytes(b"subtitle")

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "001. Talk.en.srt").exists()
    assert not (tmp_path / "001. Talk.en (1).srt").exists()


def test_copy_suffix_with_base_sibling_but_different_content_still_conflicts(
    tmp_path: Path,
) -> None:
    (tmp_path / "001. Talk.mp4").write_bytes(b"video")
    (tmp_path / "001. Talk (1).mp4").write_bytes(b"a different video")

    result = run_tool(tmp_path, "--apply")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "copy-suffixed file is not a hash duplicate: 001. Talk (1).mp4" in result.stdout
    assert (tmp_path / "001. Talk (1).mp4").exists()
