"""gmaps_times.py: URL building, merge, parsing and report, with no browser or network."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gmaps_times.py"
spec = importlib.util.spec_from_file_location("gmaps_times", SCRIPT)
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)

A = {"query": "Taipei 101", "id": "0x1:0x2", "lat": 25.03, "lng": 121.56}
B = {"query": "Longshan Temple", "lat": 25.04, "lng": 121.50}


def cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_local_epoch_treats_wall_clock_as_utc():
    assert gm.local_epoch("2026-01-02T03:04:05") == 1767323045


def test_leg_url_counts_nested_elements():
    url = gm.leg_url({"from": "a", "to": "b", "mode": "transit", "depart": "2026-01-02T08:00"},
                     {"a": A, "b": B})
    assert url.startswith("https://www.google.com/maps/dir/25.03,121.56/25.04,121.5/data=")
    inner = url.split("data=", 1)[1]
    outer, rest = inner.split("!", 2)[1:]
    n = int(rest.split("!", 1)[0][2:])
    assert outer == f"4m{n + 1}"
    assert ("!" + rest.split("!", 1)[1]).count("!") == n
    assert "!1s0x1:0x2" in url and "!6e0" in url and url.endswith("!3e3")


def test_arrive_by_uses_arrival_flag():
    url = gm.leg_url({"from": "a", "to": "b", "mode": "walk", "arrive": "2026-01-02T08:00"},
                     {"a": A, "b": B})
    assert "!6e1" in url and url.endswith("!3e2")


def test_parse_drive_range_and_distance():
    assert gm.parse("drive", "via Hwy 1 | typically 14–35 min | 12.5 km") == ("14-35 min", "12.5 km", "")
    assert gm.parse("drive", "typically 28 min to 1 hr 5 min | 40 km")[0] == "28 min to 1 hr 5 min"
    assert gm.parse("walk", "22 min | 1.6 km") == ("22 min", "1.6 km", "")


def test_parse_transit_schedule_and_fare():
    body = "1 hr 5 min | 8:10 AM—9:15 AM | NT$30"
    assert gm.parse("transit", body) == ("1 hr 5 min", "8:10 AM→9:15 AM", "NT$30")


def test_merge_drops_a_stale_feature_id_when_the_new_result_has_none(tmp_path):
    legs = tmp_path / "legs.json"
    legs.write_text(json.dumps({"places": {"a": dict(A), "b": dict(B)}, "legs": []}))
    places = tmp_path / "places.json"
    # Re-resolved with --all: Maps now gives only map-centre coordinates for "a".
    places.write_text(json.dumps({"a": {"name": "Other", "lat": 1.0, "lng": 2.0, "approx": True}}))
    r = cli("merge-places", legs, places)
    assert r.returncode == 0, r.stderr
    a = json.loads(legs.read_text())["places"]["a"]
    assert (a["lat"], a["lng"]) == (1.0, 2.0)
    assert "id" not in a


def test_resolve_actions_skip_resolved_places(tmp_path):
    legs = tmp_path / "legs.json"
    legs.write_text(json.dumps({"places": {"a": A, "c": {"query": "Somewhere"}}, "legs": []}))
    r = cli("resolve-actions", legs, tmp_path / "out")
    assert r.returncode == 0, r.stderr
    actions = json.loads((tmp_path / "out" / "resolve00.json").read_text())
    assert len(actions) == 2 and "Somewhere" in actions[0]["input"]["url"]
    assert (tmp_path / "out" / "setup.json").exists()


def test_leg_actions_refuse_unresolved_place(tmp_path):
    legs = tmp_path / "legs.json"
    legs.write_text(json.dumps({"places": {"a": A, "c": {"query": "x"}},
                                "legs": [{"label": "L1", "from": "a", "to": "c", "mode": "drive",
                                          "depart": "2026-01-02T08:00"}]}))
    r = cli("leg-actions", legs, tmp_path / "out")
    assert r.returncode != 0 and "not resolved" in r.stderr


def test_report_marks_unmeasured_legs(tmp_path):
    legs = tmp_path / "legs.json"
    legs.write_text(json.dumps({"places": {}, "legs": [
        {"label": "L1", "mode": "drive", "depart": "2026-01-02T08:00"},
        {"label": "L2", "mode": "walk", "arrive": "2026-01-02T09:00"}]}))
    res = tmp_path / "res.json"
    res.write_text(json.dumps({"L1": {"title": "A to B", "body": "typically 10 min | 3 km"}}))
    r = cli("report", legs, res)
    assert r.returncode == 0, r.stderr
    assert "| L1 | drive | 2026-01-02T08:00 | 10 min | 3 km |  | A to B |" in r.stdout
    assert "| L2 | walk | arrive 2026-01-02T09:00 | **not measured** |" in r.stdout
