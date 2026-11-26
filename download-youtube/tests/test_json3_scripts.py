"""json3_to_srt.py and json3_to_transcript.py on small hand-made caption files."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


srt = load("json3_to_srt")
transcript = load("json3_to_transcript")

PAYLOAD = {"events": [
    {"tStartMs": 0, "dDurationMs": 5000, "segs": [{"utf8": "hello"}, {"utf8": " world"}]},
    {"tStartMs": 1000, "dDurationMs": 1, "aAppend": 1, "segs": [{"utf8": "\n"}]},
    {"tStartMs": 1200, "dDurationMs": 900, "segs": [{"utf8": "second\nline"}]},
    {"tStartMs": 4000, "segs": [{"utf8": "after a pause"}]},
    {"tStartMs": 3_723_004, "dDurationMs": 0, "segs": [{"utf8": "late"}]},
]}


def test_srt_time_format():
    assert srt.srt_time(3_723_004) == "01:02:03,004"
    assert srt.srt_time(-5) == "00:00:00,000"


def test_srt_cues_drop_newline_events_and_end_at_next_cue_or_duration():
    cues = srt.cues_from_payload(PAYLOAD)
    assert [c[2] for c in cues] == ["hello world", "second line", "after a pause", "late"]
    assert cues[0][:2] == (0, 1200)          # cut at the next cue
    assert cues[1][:2] == (1200, 2100)       # its own duration ends first
    assert cues[2][:2] == (4000, 3_723_004)  # no duration: ends at the next cue
    assert cues[3][:2] == (3_723_004, 3_725_004)  # last cue, no duration: 2 s


def test_srt_cli_writes_numbered_blocks(tmp_path):
    src = tmp_path / "a.json3"
    dst = tmp_path / "a.srt"
    src.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "json3_to_srt.py"), str(src), str(dst)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = dst.read_text(encoding="utf-8")
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,200\nhello world\n\n2\n")
    assert "4\n01:02:03,004 --> 01:02:05,004\nlate\n" in text


def test_srt_cli_usage():
    r = subprocess.run([sys.executable, str(SCRIPTS / "json3_to_srt.py")], capture_output=True, text=True)
    assert r.returncode == 2


def test_transcript_breaks_paragraphs_on_pauses():
    paras = transcript.paragraphs_from_cues(transcript.cues_from_payload(PAYLOAD))
    assert paras == [(0, "hello world second line"), (4000, "after a pause"), (3_723_004, "late")]


def test_transcript_render_and_timestamps():
    assert transcript.format_timestamp(65_000) == "[01:05]"
    assert transcript.format_timestamp(3_723_004) == "[01:02:03]"
    assert transcript.render([(0, "a"), (65_000, "b")]) == "[00:00] a\n\n[01:05] b\n"
    assert transcript.render([]) == ""


def test_transcript_cli(tmp_path):
    src = tmp_path / "a.json3"
    src.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPTS / "json3_to_transcript.py"), str(src)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("[00:00] hello world second line\n\n[00:04] after a pause")
