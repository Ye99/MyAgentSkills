import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import photo_cull as pc


@pytest.fixture(autouse=True)
def _feedback_stays_in_the_test_folder(tmp_path, monkeypatch):
    # apply records review feedback under the home folder by default; never from tests.
    monkeypatch.setenv("PHOTO_CULL_FEEDBACK", str(tmp_path / "feedback"))


def test_sidecars_pair_with_their_media_including_numbered_duplicates():
    media = ["IMG_1.HEIC", "IMG_1(1).HEIC", "IMG_1.MP4", "a_very_long_name.jpg"]
    jsons = [
        "IMG_1.HEIC.supplemental-metadata.json",
        "IMG_1.HEIC.supplemental-metadata(1).json",
        "IMG_1.MP4.supplemental-metadata.json",
        "a_very_long_name.jpg.supplemental-me.json",  # Takeout truncates long names
        "print-subscriptions.json",
    ]
    by_media, orphans = pc.match_sidecars(media, jsons)
    assert by_media["IMG_1.HEIC"] == ["IMG_1.HEIC.supplemental-metadata.json"]
    assert by_media["IMG_1(1).HEIC"] == ["IMG_1.HEIC.supplemental-metadata(1).json"]
    assert by_media["IMG_1.MP4"] == ["IMG_1.MP4.supplemental-metadata.json"]
    assert by_media["a_very_long_name.jpg"] == ["a_very_long_name.jpg.supplemental-me.json"]
    assert orphans == ["print-subscriptions.json"]


def test_sidecar_of_longer_name_is_not_claimed_by_a_prefix_name():
    by_media, _ = pc.match_sidecars(
        ["IMG_1.HEIC", "IMG_1.HEIC.jpg"], ["IMG_1.HEIC.jpg.supplemental-metadata.json"])
    assert by_media["IMG_1.HEIC"] == []
    assert by_media["IMG_1.HEIC.jpg"] == ["IMG_1.HEIC.jpg.supplemental-metadata.json"]


def test_capture_time_uses_exif_offset_then_falls_back_to_sidecar():
    exif = {"DateTimeOriginal": "2026:04:20 18:18:00", "OffsetTimeOriginal": "+08:00",
            "SubSecTimeOriginal": 500}
    assert pc.capture_epoch(exif, {}) == pytest.approx(1776680280.5)
    assert pc.capture_epoch({}, {"photoTakenTime": {"timestamp": "1776770549"}}) == 1776770549.0
    assert pc.capture_epoch({}, {}) is None


@pytest.mark.parametrize("name,meta,sidecar,expected", [
    ("IMG_1.PNG", {"UserComment": "Screenshot"}, {}, True),
    ("IMG_1.PNG", {}, {"description": "Screenshot"}, True),
    ("Screenshot_2026-01-01.jpg", {}, {}, True),
    ("IMG_1.PNG", {}, {}, False),  # a PNG alone is not proof
    ("IMG_1.HEIC", {"UserComment": "note about a screenshot"}, {}, False),
])
def test_screenshot_detection(name, meta, sidecar, expected):
    assert pc.is_screenshot(name, meta, sidecar) is expected


def test_sharp_subject_on_soft_background_scores_higher_than_uniform_blur():
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(0)
    sharp = (rng.random((800, 800)) * 255).astype(np.float32)
    soft = cv2.GaussianBlur(sharp, (0, 0), 6)
    mixed = soft.copy()
    mixed[300:500, 300:500] = sharp[300:500, 300:500]
    assert pc.tile_sharpness(mixed)[0] > 20 * pc.tile_sharpness(soft)[0]
    assert pc.tile_sharpness(mixed)[1] < pc.tile_sharpness(mixed)[0]


def _regions(*codes):
    """Photos as per-region unit features: 'aaaa' is four regions that all look like a."""
    np = pytest.importorskip("numpy")
    look = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    return np.array([[look[c] for c in code] for code in codes])


def test_bursts_need_both_closeness_in_time_and_similarity():
    np = pytest.importorskip("numpy")
    times = np.array([0.0, 2.0, 4.0, 100.0, 101.0])
    emb = _regions("aaaa", "aaaa", "bbbb", "aaaa", "aaaa")
    assert pc.group_bursts(times, emb, max_gap=30, min_sim=0.9) == [[0, 1], [3, 4]]


def test_same_spot_with_a_different_person_is_not_a_burst():
    # Three of four regions match (the scenery); one differs (who is standing there).
    np = pytest.importorskip("numpy")
    emb = _regions("aaaa", "aaba")
    assert pc.group_bursts(np.array([0.0, 5.0]), emb, max_gap=30, min_sim=0.5) == []


def _item(name, sharp, t=0.0, screenshot=False):
    return {"name": name, "kind": "image", "screenshot": screenshot, "sharp_p90": sharp, "t": t}


def test_plan_keeps_every_frame_of_a_burst_and_rejects_screenshots_and_blur():
    items = [_item("a", 100), _item("b", 300), _item("c", 5), _item("shot", 500, screenshot=True),
             _item("solo", 80)]
    plan = pc.build_plan(items, {}, [[0, 1, 2]], blur_below=20, rot_conf=0.9)
    assert plan["a"]["keep"] and plan["b"]["keep"] and plan["solo"]["keep"]  # repeats are wanted
    assert plan["a"]["group"] == plan["b"]["group"] == 0
    assert (plan["c"]["keep"], plan["c"]["reason"]) == (False, "blurry")
    assert (plan["shot"]["keep"], plan["shot"]["reason"]) == (False, "screenshot")


def test_a_blurry_frame_of_a_burst_is_still_rejected_as_blurry():
    items = [_item("a", 100), _item("b", 5)]
    plan = pc.build_plan(items, {}, [[0, 1]], blur_below=20, rot_conf=0.9)
    assert plan["a"]["keep"] and plan["b"]["reason"] == "blurry"


def test_plan_rotates_only_above_confidence():
    items = [_item("sure", 100), _item("unsure", 100), _item("upright", 100)]
    probs = {0: [0.02, 0.96, 0.01, 0.01], 1: [0.3, 0.6, 0.05, 0.05], 2: [0.99, 0.01, 0, 0]}
    plan = pc.build_plan(items, probs, [], blur_below=20, rot_conf=0.9)
    assert [plan[n]["rotate"] for n in ("sure", "unsure", "upright")] == [1, 0, 0]


def test_rotation_arithmetic_matches_exif_convention():
    # EXIF 6 = "rotate 90 CW to display" = 3 quarter turns CCW.
    assert pc.EXIF_TO_CCW[6] == 3 and pc.CCW_TO_EXIF[3] == 6
    assert pc.new_ccw(0, 1) == 3  # unrotated, turn it clockwise once -> EXIF 6
    assert pc.new_ccw(3, 3) == 0  # portrait flag was wrong; three more CW turns undo it
    assert pc.new_ccw(2, 2) == 0


def _box(typ, payload):
    return struct.pack(">I4s", 8 + len(payload), typ) + payload


def _heic(primary_id, assoc, props):
    """Minimal HEIF meta box. assoc = {item_id: [1-based property index]}."""
    pitm = _box(b"pitm", b"\0\0\0\0" + struct.pack(">H", primary_id))
    ipma = b"\0\0\0\0" + struct.pack(">I", len(assoc))
    for item, idxs in assoc.items():
        ipma += struct.pack(">HB", item, len(idxs)) + bytes(idxs)
    iprp = _box(b"iprp", _box(b"ipco", b"".join(props)) + _box(b"ipma", ipma))
    return _box(b"ftyp", b"heic\0\0\0\0") + _box(b"meta", b"\0\0\0\0" + pitm + iprp) + _box(b"mdat", b"irot")


def test_irot_lookup_follows_the_primary_item_not_the_first_irot_in_the_file():
    props = [_box(b"irot", b"\x00"), _box(b"ispe", b"\0" * 12), _box(b"irot", b"\x03")]
    buf = _heic(primary_id=7, assoc={3: [1], 7: [2, 3]}, props=props)
    off = pc.find_primary_irot(buf)
    assert buf[off] == 3 and buf[off - 4:off] == b"irot"


def test_irot_lookup_returns_none_when_primary_has_no_rotation():
    buf = _heic(primary_id=7, assoc={3: [1], 7: [2]},
                props=[_box(b"irot", b"\x01"), _box(b"ispe", b"\0" * 12)])
    assert pc.find_primary_irot(buf) is None
    assert pc.find_primary_irot(b"not a heic file at all") is None


def test_rotate_copy_changes_exactly_one_byte_of_a_heic(tmp_path):
    buf = _heic(primary_id=1, assoc={1: [1]}, props=[_box(b"irot", b"\x03")])
    f = tmp_path / "a.HEIC"
    f.write_bytes(buf)
    assert pc.rotate_copy(f, 1) is None
    after = f.read_bytes()
    diff = [i for i in range(len(buf)) if buf[i] != after[i]]
    assert len(after) == len(buf) and len(diff) == 1 and after[diff[0]] == 2


def _scan():
    return {"orphan_json": ["stray.json"], "items": [
        {"name": "a.HEIC", "kind": "image", "sidecars": ["a.HEIC.json"]},
        {"name": "a.MP4", "kind": "video", "sidecars": ["a.MP4.json"], "live_of": "a.HEIC"},
        {"name": "b.HEIC", "kind": "image", "sidecars": []},
        {"name": "clip.MOV", "kind": "video", "sidecars": []},
        {"name": "notes.txt", "kind": "other", "sidecars": []},
    ]}


def test_resolve_accounts_for_every_file_and_live_video_follows_its_photo():
    plan = {"a.HEIC": {"keep": False, "reason": "blurry", "rotate": 0},
            "b.HEIC": {"keep": True, "reason": None, "rotate": 1}}
    final = pc.resolve(_scan(), plan, {})
    assert set(final) == {"a.HEIC", "a.HEIC.json", "a.MP4", "a.MP4.json", "b.HEIC",
                          "clip.MOV", "notes.txt", "stray.json"}
    assert not final["a.MP4"]["keep"] and not final["a.MP4.json"]["keep"]
    assert final["clip.MOV"]["keep"] and final["notes.txt"]["keep"] and final["stray.json"]["keep"]
    assert final["b.HEIC"]["rotate"] == 1


def test_overrides_can_rescue_a_reject_and_reject_a_keeper():
    plan = {"a.HEIC": {"keep": False, "reason": "blurry", "rotate": 0},
            "b.HEIC": {"keep": True, "reason": None, "rotate": 1}}
    final = pc.resolve(_scan(), plan, {"a.HEIC": {"keep": True, "rotate": 0},
                                       "b.HEIC": {"keep": False, "rotate": 1}})
    assert final["a.HEIC"] == {"keep": True, "reason": None, "rotate": 0}
    assert final["a.MP4"]["keep"]
    assert final["b.HEIC"] == {"keep": False, "reason": "manual", "rotate": 0}


# ------------------------------------------------------------ end to end
# These build a tiny real folder: JPEG/PNG files with EXIF written by exiftool.

import hashlib
import json
import shutil
import subprocess


def _exiftool(path, *tags):
    if shutil.which("exiftool") is None:
        pytest.skip("exiftool not installed")
    subprocess.run(["exiftool", "-q", "-q", "-n", "-overwrite_original", *tags, str(path)], check=True)


def _photo(path, seed=0, blur=0, orientation=None, when="2026:01:01 10:00:00", corner=False):
    np = pytest.importorskip("numpy")
    from PIL import Image, ImageFilter
    a = (np.random.default_rng(seed).random((240, 320, 3)) * 255).astype(np.uint8)
    if blur:  # PIL, not cv2: making a test photo should not need OpenCV
        a = np.asarray(Image.fromarray(a).filter(ImageFilter.GaussianBlur(blur)))
    if corner:
        a[:, :, 1] |= 0xE0  # textured but always strongly green ...
        a[:80, :80] = (255, 0, 0)  # ... except a red block in the top-left corner
    Image.fromarray(a).save(path, quality=95)
    tags = ["-Make=Apple", f"-DateTimeOriginal={when}"]
    if orientation:
        tags.append(f"-Orientation#={orientation}")
    _exiftool(path, *tags)


def _folder(tmp_path):
    pytest.importorskip("cv2")  # the scan measures sharpness with OpenCV
    src = tmp_path / "src"
    src.mkdir()
    _photo(src / "sharp.jpg", seed=1)
    _photo(src / "soft.jpg", seed=2, blur=8)
    _photo(src / "turn.jpg", corner=True)
    from PIL import Image
    Image.new("RGB", (100, 200), "white").save(src / "shot.png")
    _exiftool(src / "shot.png", "-UserComment=Screenshot")
    (src / "sharp.jpg.supplemental-metadata.json").write_text('{"title": "sharp.jpg"}')
    (src / "soft.jpg.supplemental-metadata.json").write_text('{"title": "soft.jpg"}')
    (src / "notes.txt").write_text("not media")
    return src


def _hashes(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.iterdir()}


def _log(work):
    return [json.loads(l) for l in (work / "operations.jsonl").read_text().splitlines()]


def test_log_event_appends_one_json_line_with_time_stage_and_fields(tmp_path):
    pc.log_event(tmp_path, "scan", "start", files=3)
    pc.log_event(tmp_path, "apply", "copy", file="a.jpg")
    first, second = _log(tmp_path)
    assert (first["stage"], first["event"], first["files"]) == ("scan", "start", 3)
    assert second["file"] == "a.jpg" and "ts" in second


def test_scan_records_every_file_and_measures_images(tmp_path):
    src, work = _folder(tmp_path), tmp_path / "work"
    pc.main(["scan", str(src), str(work), "--jobs", "2"])
    scan = json.loads((work / "scan.json").read_text())
    items = {it["name"]: it for it in scan["items"]}
    assert set(items) == {"sharp.jpg", "soft.jpg", "turn.jpg", "shot.png", "notes.txt"}
    assert items["shot.png"]["screenshot"] and not items["sharp.jpg"]["screenshot"]
    assert items["notes.txt"]["kind"] == "other"
    assert items["sharp.jpg"]["sidecars"] == ["sharp.jpg.supplemental-metadata.json"]
    assert items["sharp.jpg"]["sharp_p90"] > 20 * items["soft.jpg"]["sharp_p90"]
    assert (work / items["sharp.jpg"]["thumb"]).is_file()
    assert items["sharp.jpg"]["t"] is not None


def test_scan_does_not_touch_the_source_and_logs_a_summary(tmp_path):
    src, work = _folder(tmp_path), tmp_path / "work"
    before = _hashes(src)
    pc.main(["scan", str(src), str(work), "--jobs", "2"])
    assert _hashes(src) == before
    done = [e for e in _log(work) if e["stage"] == "scan" and e["event"] == "done"]
    assert done and done[0]["images"] == 4 and done[0]["json_sidecars"] == 2


def _scanned(tmp_path):
    src, work = _folder(tmp_path), tmp_path / "work"
    pc.main(["scan", str(src), str(work), "--jobs", "2"])
    items = json.loads((work / "scan.json").read_text())["items"]
    plan = pc.build_plan(items, {[i["name"] for i in items].index("turn.jpg"): [0, 0.99, 0, 0.01]},
                         [], blur_below=500, rot_conf=0.9)
    pc.write_outputs(work, items, plan, {"blur_below": 500})
    ov = tmp_path / "overrides.json"
    ov.write_text(json.dumps(_reviewed(work)))
    return src, work, tmp_path / "out", ov


def _reviewed(work, **changes):
    """What review.html downloads: the reviewed plan's id plus the user's changes."""
    return {"_plan": json.loads((work / "plan.json").read_text())["id"], **changes}


def test_analysis_outputs_put_all_four_kinds_of_change_on_the_review_page(tmp_path):
    items = [dict(_item(n, s), thumb=f"thumbs/{n}.jpg") for n, s in
             [("solo", 100), ("soft", 5), ("b1", 100), ("b2", 300), ("turned", 100)]]
    items.append(dict(_item("shot", 100, screenshot=True), thumb="thumbs/shot.jpg"))
    plan = pc.build_plan(items, {4: [0.01, 0.97, 0.01, 0.01]}, [[2, 3]], blur_below=20, rot_conf=0.9,
                         faces={2: [_face(blink=0.9)], 3: [_face()]})
    pc.write_outputs(tmp_path, items, plan, {})
    page = (tmp_path / "review.html").read_text()
    data = {d["name"]: d for d in json.loads(page.split("const D = ")[1].split(";\n")[0])}
    assert data["shot"]["reason"] == "screenshot" and data["soft"]["reason"] == "blurry"
    assert data["b1"]["reason"] == "facial" and data["b2"]["keep"]  # the better frame shown beside it
    assert data["turned"]["rotate"] == 1
    assert "solo" not in data  # untouched photos are not review noise


def test_analysis_logs_one_decision_per_image_with_its_evidence(tmp_path):
    items = [dict(_item("soft", 5), thumb="t"), dict(_item("fine", 100), thumb="t")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {"blur_below": 20})
    dec = {e["file"]: e for e in _log(tmp_path) if e["event"] == "decision"}
    assert set(dec) == {"soft", "fine"}
    assert (dec["soft"]["keep"], dec["soft"]["reason"], dec["soft"]["sharpness"]) == (False, "blurry", 5)
    assert json.loads((tmp_path / "plan.json").read_text())["params"] == {"blur_below": 20}


def test_apply_refuses_to_copy_before_the_review_page_was_used(tmp_path):
    src, work, out, _ = _scanned(tmp_path)
    with pytest.raises(SystemExit) as e:
        pc.main(["apply", str(src), str(work), str(out), "--apply"])
    assert "review" in str(e.value).lower() and not out.exists()


def test_apply_dry_run_copies_nothing(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov)])
    assert not out.exists()


def test_apply_copies_keepers_with_sidecars_and_leaves_source_untouched(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    before = _hashes(src)
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert _hashes(src) == before
    assert {p.name for p in out.iterdir()} == {
        "sharp.jpg", "sharp.jpg.supplemental-metadata.json", "turn.jpg", "notes.txt"}
    assert _hashes(out)["sharp.jpg"] == before["sharp.jpg"]


def test_apply_turns_a_photo_clockwise_without_changing_a_pixel(tmp_path):
    np = pytest.importorskip("numpy")
    src, work, out, ov = _scanned(tmp_path)
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    a, b = pc.pixels(src / "turn.jpg"), pc.pixels(out / "turn.jpg")
    assert np.array_equal(np.rot90(a, -1), b)
    assert b.shape[:2] == (320, 240)
    assert tuple(b[10, -10]) [0] > 200 and b[10, -10][1] < 80  # red block is now top-right
    assert b[10, 10][1] > 200  # and no longer top-left


def test_apply_logs_what_happened_to_every_source_file(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    ops = {e["file"]: e for e in _log(work) if e["stage"] == "apply" and "file" in e}
    assert set(ops) == {p.name for p in src.iterdir()}
    assert ops["sharp.jpg"]["event"] == "kept" and ops["sharp.jpg"]["verified"] == "sha256"
    assert ops["turn.jpg"]["rotate"] == 1 and "pixels" in ops["turn.jpg"]["verified"]
    assert (ops["soft.jpg"]["event"], ops["soft.jpg"]["reason"]) == ("left_behind", "blurry")
    assert ops["soft.jpg.supplemental-metadata.json"]["event"] == "left_behind"
    assert ops["shot.png"]["reason"] == "screenshot"
    report = json.loads((work / "apply_report.json").read_text())
    assert report["problems"] == [] and len(report["kept"]) + len(report["left_behind"]) == 7


def test_apply_honours_overrides_from_the_review_page(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    ov.write_text(json.dumps(_reviewed(work, **{"shot.png": {"keep": True, "rotate": 0},
                              "turn.jpg": {"keep": True, "rotate": 0}})))
    before = _hashes(src)
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert _hashes(out)["shot.png"] == before["shot.png"]
    assert _hashes(out)["turn.jpg"] == before["turn.jpg"]  # rotation declined


def test_apply_stops_when_the_source_changed_after_the_scan(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    (src / "late.jpg").write_bytes(b"x")
    with pytest.raises(SystemExit) as e:
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert "changed" in str(e.value) and not out.exists()


def test_apply_reports_a_problem_instead_of_overwriting_a_different_file(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    out.mkdir()
    (out / "sharp.jpg").write_bytes(b"someone else's file")
    with pytest.raises(SystemExit):
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert (out / "sharp.jpg").read_bytes() == b"someone else's file"
    assert any("sharp.jpg" in p for p in json.loads((work / "apply_report.json").read_text())["problems"])


# ------------------------------------------------------------ rotation check

def _rotation_features(state, rng, burst_signature=0.0, burst=10, d=32):
    """Fake embeddings: view k of a photo in `state` looks like prototype
    (state + k) % 4. Photos in the same burst also share a strong signature."""
    np = pytest.importorskip("numpy")
    n = len(state)
    proto = rng.normal(size=(4, d))
    sig = rng.normal(size=(n // burst + 1, 4, d)) * burst_signature
    feats = np.empty((n, 4, d), dtype=np.float32)
    for i in range(n):
        for k in range(4):
            feats[i, k] = proto[(state[i] + k) % 4] + sig[i // burst, k] + 0.4 * rng.normal(size=d)
    return feats.astype(np.float16)


def test_rotation_check_finds_turned_photos_among_upright_ones():
    np = pytest.importorskip("numpy")
    pytest.importorskip("torch")
    rng = np.random.default_rng(0)
    state = np.zeros(400, dtype=int)
    state[::25] = 1 + np.arange(16) % 3
    probs = pc.predict_rotation(_rotation_features(state, rng), np.arange(400))
    guess = probs.argmax(1)
    assert probs.shape == (400, 4) and np.allclose(probs.sum(1), 1, atol=1e-4)
    assert (guess[state > 0] == state[state > 0]).all()
    assert (guess[state == 0] == 0).mean() > 0.98


def test_rotation_check_is_not_fooled_by_a_whole_burst_shot_sideways():
    # If a photo were judged by a model that trained on its burst siblings
    # (all labelled "upright"), the burst's shared look would vouch for it.
    np = pytest.importorskip("numpy")
    pytest.importorskip("torch")
    rng = np.random.default_rng(1)
    state = np.zeros(400, dtype=int)
    state[70:80] = 1
    probs = pc.predict_rotation(_rotation_features(state, rng, burst_signature=3.0), np.arange(400))
    assert (probs.argmax(1)[70:80] == 1).all()


def test_analyze_is_a_subcommand_with_tunable_thresholds():
    with pytest.raises(SystemExit) as e:
        pc.main(["analyze", "--help"])
    assert e.value.code == 0


def test_embedding_view_k_is_the_photo_turned_k_times_counter_clockwise(tmp_path):
    # Runs the real model on the real loader path (worker processes included).
    np = pytest.importorskip("numpy")
    pytest.importorskip("torch")
    from PIL import Image
    rng = np.random.default_rng(0)
    a = np.kron(rng.integers(0, 255, (8, 8, 3)), np.ones((40, 40, 1))).astype(np.uint8)  # 320x320 blocks
    (tmp_path / "thumbs").mkdir()
    Image.fromarray(a).save(tmp_path / "thumbs/a.png")
    Image.fromarray(np.rot90(a, 1)).save(tmp_path / "thumbs/b.png")
    items = [{"thumb": "thumbs/a.png"}, {"thumb": "thumbs/b.png"}]
    try:
        f, regions = pc.embed(tmp_path, items, "facebook/dinov2-small", batch=2)
    except OSError as e:  # model not cached and no network
        pytest.skip(str(e))
    f = f.astype(np.float32)
    assert f.shape == (2, 4, 768)
    assert regions.shape == (2, 16, 384)  # 4x4 grid of regions per photo
    assert np.allclose(np.linalg.norm(regions.astype(np.float32), axis=2), 1, atol=1e-2)
    cos = lambda x, y: float(x @ y / (np.linalg.norm(x) * np.linalg.norm(y)))
    same = cos(f[0, 1], f[1, 0])  # a turned once CCW vs b as stored: the same picture
    assert same > 0.9999
    for k in (0, 2, 3):  # every other view of a is a different picture from b
        assert 1 - cos(f[0, k], f[1, 0]) > 20 * (1 - same)


def test_embedding_is_the_same_on_one_gpu_or_several(tmp_path):
    np = pytest.importorskip("numpy")
    torch = pytest.importorskip("torch")
    if torch.cuda.device_count() < 2:
        pytest.skip("needs two GPUs")
    from PIL import Image
    rng = np.random.default_rng(3)
    (tmp_path / "thumbs").mkdir()
    items = []
    for k in range(5):  # odd count: the split across GPUs is uneven
        a = np.kron(rng.integers(0, 255, (8, 8, 3)), np.ones((40, 40, 1))).astype(np.uint8)
        Image.fromarray(a).save(tmp_path / f"thumbs/{k}.png")
        items.append({"thumb": f"thumbs/{k}.png"})
    try:
        f1, r1 = pc.embed(tmp_path, items, "facebook/dinov2-small", batch=2, devices=["cuda:0"])
        f2, r2 = pc.embed(tmp_path, items, "facebook/dinov2-small", batch=2, devices=["cuda:0", "cuda:1"])
    except OSError as e:
        pytest.skip(str(e))
    assert f1.shape == f2.shape == (5, 4, 768)
    assert np.abs(f1.astype(np.float32) - f2.astype(np.float32)).max() < 0.05  # same photos, same order
    assert np.abs(r1.astype(np.float32) - r2.astype(np.float32)).max() < 0.05
    assert np.abs(f1[0].astype(np.float32) - f1[4].astype(np.float32)).max() > 0.5  # photos do differ


def test_devices_default_to_every_gpu_or_the_cpu():
    torch = pytest.importorskip("torch")
    n = torch.cuda.device_count()
    assert pc.default_devices() == ([f"cuda:{i}" for i in range(n)] or ["cpu"])


# ------------------------------------------------------------ text direction

def test_text_verdict_names_the_turn_that_makes_the_text_readable():
    # scores[k]: how much text reads cleanly after k clockwise quarter turns.
    assert pc.text_verdict([0, 4.2, 0, 87.4]) == 3
    assert pc.text_verdict([836.2, 15.1, 4.6, 4.9]) == 0


def test_text_verdict_is_withheld_when_there_is_little_text_or_no_clear_winner():
    assert pc.text_verdict([0, 0, 0, 12.0]) is None        # a word or two proves nothing
    assert pc.text_verdict([0, 19.2, 0, 28.7]) is None     # text running round a circle
    assert pc.text_verdict([31.7, 0.6, 87.3, 2.4]) is None # a folded leaflet reads both ways
    assert pc.text_verdict(None) is None


def test_plan_turns_a_sign_the_scene_model_was_unsure_about():
    items = [_item("sign", 100)]
    plan = pc.build_plan(items, {0: [0.55, 0.2, 0.05, 0.2]}, [], 20, 0.95, text={0: [0, 4, 0, 87]})
    assert plan["sign"]["rotate"] == 3 and plan["sign"]["rotate_by"] == "text"
    assert plan["sign"]["text_scores"] == [0, 4, 0, 87]


def test_plan_lets_upright_text_cancel_a_scene_rotation():
    # Signs shot from above were the scene model's wrong 180-degree flips.
    items = [_item("sign", 100)]
    plan = pc.build_plan(items, {0: [0.01, 0.01, 0.97, 0.01]}, [], 20, 0.95, text={0: [300, 2, 9, 1]})
    assert plan["sign"]["rotate"] == 0 and plan["sign"]["rotate_by"] == "text"


def test_plan_ignores_sideways_text_in_a_scene_that_is_clearly_upright():
    # Book spines and vertical banners carry sideways text in an upright photo.
    items = [_item("shelf", 100)]
    plan = pc.build_plan(items, {0: [0.97, 0.01, 0.01, 0.01]}, [], 20, 0.95, text={0: [0, 90, 0, 3]})
    assert plan["shelf"]["rotate"] == 0 and "rotate_by" not in plan["shelf"]


def test_plan_without_text_scores_follows_the_scene_model():
    items = [_item("turned", 100)]
    plan = pc.build_plan(items, {0: [0.01, 0.97, 0.01, 0.01]}, [], 20, 0.95)
    assert plan["turned"]["rotate"] == 1 and plan["turned"]["rotate_by"] == "scene"


def _text_image(np):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (640, 480), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=34)
    for row, line in enumerate(["MUSEUM OF THE NORTH", "Opening hours today", "Tickets and guided tours",
                                "Please do not touch", "The collection was founded"]):
        d.text((30, 30 + row * 80), line, fill="black", font=font)
    return im


def test_text_scores_find_the_reading_direction_of_a_rendered_sign(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("easyocr")
    (tmp_path / "thumbs").mkdir()
    upright = _text_image(np)
    upright.save(tmp_path / "thumbs/up.png")
    upright.rotate(-90, expand=True).save(tmp_path / "thumbs/cw.png")     # turned clockwise: needs 3 more
    upright.rotate(180).save(tmp_path / "thumbs/flip.png")
    from PIL import Image
    Image.new("RGB", (640, 480), (90, 140, 200)).save(tmp_path / "thumbs/sky.png")
    items = [{"name": n, "thumb": f"thumbs/{n}.png"} for n in ("up", "cw", "flip", "sky")]
    try:
        scores = pc.text_scores(tmp_path, items, ["en"], devices=[pc.default_devices()[0]])
    except OSError as e:
        pytest.skip(str(e))
    assert [pc.text_verdict(scores[n]) for n in ("up", "cw", "flip")] == [0, 3, 2]
    assert scores["sky"] is None  # no text found: nothing to go on
    assert json.loads((tmp_path / "text_scores.json").read_text())["scores"]["cw"] == scores["cw"]


def test_text_scores_use_every_gpu_and_agree_with_one_gpu(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("easyocr")
    torch = pytest.importorskip("torch")
    if torch.cuda.device_count() < 2:
        pytest.skip("needs two GPUs")
    (tmp_path / "thumbs").mkdir()
    upright = _text_image(np)
    items = []
    for k in range(4):
        upright.rotate(-90 * k, expand=True).save(tmp_path / f"thumbs/{k}.png")
        items.append({"name": str(k), "thumb": f"thumbs/{k}.png"})
    try:
        two = pc.text_scores(tmp_path, items, ["en"], devices=["cuda:0", "cuda:1"])
        (tmp_path / "text_scores.json").unlink()
        one = pc.text_scores(tmp_path, items, ["en"], devices=["cuda:0"])
    except OSError as e:
        pytest.skip(str(e))
    assert [pc.text_verdict(two[str(k)]) for k in range(4)] == [0, 3, 2, 1]
    assert [pc.text_verdict(one[str(k)]) for k in range(4)] == [0, 3, 2, 1]


def test_text_scores_already_read_are_saved_when_one_device_fails(tmp_path, monkeypatch):
    # A failure on one GPU must not throw away minutes of reading on the other.
    def fake(dev, paths, languages):
        if dev == "cuda:1":
            raise RuntimeError("device fell over")
        return [[9, 0, 0, 0] for _ in paths]
    monkeypatch.setattr(pc, "_text_on", fake)
    monkeypatch.setattr(pc, "_text_models", lambda languages: None)  # no OCR library needed
    monkeypatch.setattr(pc, "ISOLATE_DEVICES", False)  # the fake cannot cross a process boundary
    items = [{"name": n, "thumb": f"thumbs/{n}.png"} for n in "abcd"]
    with pytest.raises(RuntimeError):
        pc.text_scores(tmp_path, items, ["en"], devices=["cuda:0", "cuda:1"])
    assert json.loads((tmp_path / "text_scores.json").read_text())["scores"] == {"a": [9, 0, 0, 0], "c": [9, 0, 0, 0]}


def test_text_scores_are_reused_from_the_cache(tmp_path, monkeypatch):
    (tmp_path / "text_scores.json").write_text(json.dumps(
        {"languages": ["en"], "scores": {"a": [1, 2, 3, 90], "b": None}}))
    monkeypatch.setattr(pc, "_text_on", lambda *a: pytest.fail("cached photos were read again"))
    items = [{"name": "a", "thumb": "thumbs/a.png"}, {"name": "b", "thumb": "thumbs/b.png"}]
    assert pc.text_scores(tmp_path, items, ["en"], devices=["cpu"]) == {"a": [1, 2, 3, 90], "b": None}


def test_review_page_shows_large_previews_with_a_size_slider(tmp_path):
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    page = (tmp_path / "review.html").read_text()
    assert 'type="range"' in page and 'id="size"' in page
    import re
    widths = [int(w) for w in re.findall(r"'(\d+)px'\)", page)]
    assert len(widths) == 6 and min(widths) >= 200 and max(widths) >= 420  # one per tab


def test_review_page_previews_stretch_to_fill_the_window_width(tmp_path):
    # Fixed-width cards left a blank strip wherever one more did not fit. Rows
    # are grids whose columns share the whole width, and the image scales with
    # its card instead of stopping at the thumbnail's own size.
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    css = (tmp_path / "review.html").read_text().split("</style>")[0]
    assert "grid-template-columns:repeat(auto-fill,minmax(" in css and ",1fr))" in css
    assert "object-fit:contain" in css


def test_review_page_puts_each_section_in_its_own_tab(tmp_path):
    # One long page meant hunting for "Screenshots"; each section is a tab,
    # only the chosen one is shown, and the choice survives a reload.
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    page = (tmp_path / "review.html").read_text()
    css = page.split("</style>")[0]
    assert ".tab{display:none}" in css and ".tab.on{display:block}" in css
    assert 'id="tabs"' in page
    section = page.split("function section(")[1].split("\n}")[0]
    assert "className = 'tab'" in section and "createElement('button')" in section
    assert "':tab'" in page.split("function pick(")[1].split("\n}")[0]  # remembered in localStorage


def test_top_bar_text_is_as_large_as_the_viewer_caption(tmp_path):
    import re
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    css = (tmp_path / "review.html").read_text().split("</style>")[0]
    size = lambda sel: int(re.search(re.escape(sel) + r"\{[^}]*font-size:(\d+)px", css).group(1))
    assert size("#bar") == size("#cap")


def test_every_preview_on_the_review_page_can_be_excluded(tmp_path):
    # Rotation cards toggle the rotation on click, so excluding the photo
    # itself needs its own control, present on every card.
    items = [dict(_item("turned", 100), thumb="thumbs/t.jpg")]
    plan = pc.build_plan(items, {0: [0.01, 0.97, 0.01, 0.01]}, [], 20, 0.9)
    pc.write_outputs(tmp_path, items, plan, {})
    page = (tmp_path / "review.html").read_text()
    assert '<button class="x"' in page and "Exclude" in page
    assert "st.keep = !st.keep" in page.split('.x\').onclick')[1].split("};")[0]


# ------------------------------------------------------------ full-size previews

def test_only_photos_the_plan_changes_are_up_for_review():
    items = [_item("solo", 100), _item("soft", 5), _item("b1", 100), _item("b2", 300),
             _item("g1", 100), _item("g2", 5), _item("shot", 100, screenshot=True)]
    plan = pc.build_plan(items, {}, [[2, 3], [4, 5]], blur_below=20, rot_conf=0.9,
                         faces={2: [_face(blink=0.9)], 3: [_face()]})
    # b1/b2: a burst with a facial reject. g1/g2: a group whose only other
    # member is blurry, so nothing is compared and g1 needs no review.
    assert pc.review_names(plan) == ["soft", "b1", "b2", "g2", "shot"]


def test_full_size_previews_are_made_at_original_resolution_and_upright(tmp_path):
    src, work = tmp_path / "src", tmp_path / "work"
    src.mkdir()
    _photo(src / "a.jpg", seed=1, orientation=6)  # stored 320x240, displayed 240x320
    _photo(src / "b.jpg", seed=2)
    items = [{"name": "a.jpg", "thumb": "thumbs/000000.jpg"}, {"name": "b.jpg", "thumb": "thumbs/000001.jpg"}]
    full = pc.make_previews(src, work, items, ["a.jpg"], jobs=1)
    assert full == {"a.jpg": "full/000000.jpg"}
    from PIL import Image
    assert Image.open(work / "full/000000.jpg").size == (240, 320)
    assert not (work / "full/000001.jpg").exists()


def test_full_size_previews_are_not_remade_on_a_second_run(tmp_path):
    src, work = tmp_path / "src", tmp_path / "work"
    src.mkdir()
    _photo(src / "a.jpg", seed=1)
    items = [{"name": "a.jpg", "thumb": "thumbs/000000.jpg"}]
    pc.make_previews(src, work, items, ["a.jpg"], jobs=1)
    (work / "full/000000.jpg").write_bytes(b"marker")
    assert pc.make_previews(src, work, items, ["a.jpg"], jobs=1) == {"a.jpg": "full/000000.jpg"}
    assert (work / "full/000000.jpg").read_bytes() == b"marker"


def test_review_page_opens_the_full_size_preview_in_a_built_in_viewer(tmp_path):
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg"), dict(_item("shot", 9, screenshot=True), thumb="thumbs/s.jpg")]
    plan = pc.build_plan(items, {}, [], 20, 0.9)
    pc.write_outputs(tmp_path, items, plan, {}, full={"soft": "full/soft.jpg"})
    page = (tmp_path / "review.html").read_text()
    data = {d["name"]: d for d in json.loads(page.split("const D = ")[1].split(";\n")[0])}
    assert data["soft"]["full"] == "full/soft.jpg"
    assert data["shot"]["full"] is None  # falls back to the thumbnail
    assert 'id="view"' in page and "ArrowRight" in page and "Escape" in page


def test_review_page_lists_every_photo_in_capture_order_for_context(tmp_path):
    # The viewer steps to the shots taken before and after the one under
    # review, so the page needs all photos, not only the ones it proposes to change.
    items = [dict(_item("late", 100, t=30.0), thumb="thumbs/l.jpg"),
             dict(_item("soft", 5, t=20.0), thumb="thumbs/s.jpg"),
             dict(_item("early", 100, t=10.0), thumb="thumbs/e.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    page = (tmp_path / "review.html").read_text()
    assert [d["name"] for d in json.loads(page.split("const D = ")[1].split(";\n")[0])] == ["soft"]
    timeline = json.loads(page.split("const T = ")[1].split(";\n")[0])
    assert timeline == [["early", "thumbs/e.jpg"], ["soft", "thumbs/s.jpg"], ["late", "thumbs/l.jpg"]]


def test_viewer_arrows_walk_the_timeline_and_up_down_walk_the_review_row(tmp_path):
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    keys = (tmp_path / "review.html").read_text().split("document.onkeydown")[1].split("\n};")[0]
    side = keys.split("ArrowLeft")[1].split("ArrowUp")[0]
    assert "T.length" in side and "cur.row" not in side
    assert "cur.row.length" in keys.split("ArrowUp")[1]


def test_zoomed_viewer_photo_can_be_dragged_around_with_the_mouse(tmp_path):
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    page = (tmp_path / "review.html").read_text()
    down = page.split("big.onmousedown")[1].split("};")[0]
    assert "zoom" in down and "scrollLeft" in down  # only when zoomed; remembers where the drag began
    move = page.split("onmousemove")[1].split("};")[0]
    assert "view.scrollLeft" in move and "view.scrollTop" in move
    # Releasing after a drag must not count as the click that zooms back out.
    assert "dragged" in page.split("big.onclick")[1].split("};")[0]


def test_viewer_caption_is_large_enough_to_read_at_a_glance(tmp_path):
    import re
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    cap = re.search(r"#cap\{[^}]*font-size:(\d+)px", (tmp_path / "review.html").read_text())
    assert int(cap.group(1)) >= 18


def test_every_preview_on_the_review_page_can_be_turned_by_hand(tmp_path):
    items = [dict(_item("turned", 100), thumb="thumbs/t.jpg")]
    plan = pc.build_plan(items, {0: [0.01, 0.97, 0.01, 0.01]}, [], 20, 0.9)
    pc.write_outputs(tmp_path, items, plan, {})
    page = (tmp_path / "review.html").read_text()
    assert '<button class="r"' in page
    assert "(st.rotate + 1) % 4" in page.split(".r').onclick")[1].split("};")[0]


def test_apply_uses_the_rotation_chosen_on_the_review_page_over_the_proposed_one(tmp_path):
    np = pytest.importorskip("numpy")
    src, work, out, ov = _scanned(tmp_path)  # the plan proposes one clockwise turn for turn.jpg
    ov.write_text(json.dumps(_reviewed(work, **{"turn.jpg": {"keep": True, "rotate": 3}})))
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert np.array_equal(np.rot90(pc.pixels(src / "turn.jpg"), -3), pc.pixels(out / "turn.jpg"))
    ops = {e["file"]: e for e in _log(work) if e["stage"] == "apply" and "file" in e}
    assert ops["turn.jpg"]["rotate"] == 3


def test_review_page_saves_progress_in_the_browser_and_can_reset_it(tmp_path):
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    page = (tmp_path / "review.html").read_text()
    assert "localStorage.setItem(KEY" in page and "localStorage.getItem(KEY" in page
    assert "location.pathname" in page.split("const KEY =")[1].split(";")[0]  # one page, one saved review
    assert 'id="reset"' in page and 'id="saved"' in page


def test_review_page_summary_separates_copied_rotated_and_excluded(tmp_path):
    # A rotated photo is shown on the page but is still copied, so the summary
    # must not count everything shown as "left behind".
    items = [dict(_item("soft", 5), thumb="thumbs/soft.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    counter = (tmp_path / "review.html").read_text().split("function count() {")[1].split("\n}")[0]
    assert "will be copied" in counter and "rotated" in counter and "excluded" in counter
    assert "left behind" not in counter


def _face(w=0.2, blink=0.1, smile=0.0, sharp=100.0, x=0.5, y=0.4):
    return {"w": w, "blink": blink, "smile": smile, "sharp": sharp, "x": x, "y": y}


def _scores(*frames):
    return [f and f["score"] for f in pc.burst_faces([list(f) for f in frames])]


def test_burst_faces_count_closed_eyes_and_smiles_of_the_main_faces():
    frame = [_face(smile=0.8), _face(blink=0.9, x=0.8), _face(w=0.03, blink=0.9, x=0.1)]
    s = pc.burst_faces([frame, frame])[0]
    assert (s["faces"], s["eyes_closed"], s["smile"]) == (2, 1, 0.4)  # the small bystander is ignored


def test_burst_faces_are_none_for_a_burst_without_faces():
    assert pc.burst_faces([[], None]) == [None, None]


def test_open_eyes_beat_closed_eyes_and_a_smile_beats_none():
    shut, plain, happy, both = _scores([_face(blink=0.9)], [_face()], [_face(smile=0.8)],
                                       [_face(blink=0.9, smile=0.9)])
    assert happy > plain > shut and both < plain  # a smile does not excuse closed eyes


def test_a_face_that_cannot_be_read_is_no_evidence_either_way():
    # The landmark model gives up on profiles, tiny faces, pets and statues alike.
    frames = [[_face(blink=None, smile=None)], [_face()], [_face()]]
    assert len(set(_scores(*frames))) == 1 and _drops(*frames) == [None] * 3


def test_a_person_the_detector_missed_in_one_frame_neither_helps_nor_hurts_it():
    # Detection of small faces flickers between near-identical frames; that is not evidence.
    two = [_face(), _face(x=0.8)]
    assert len(set(_scores(two, two, [_face()]))) == 1


def test_the_same_person_is_followed_across_frames_despite_small_shifts():
    a, b = pc.burst_faces([[_face(x=0.50, smile=0.9)], [_face(x=0.55, blink=0.9)]])
    assert (a["faces"], b["faces"]) == (1, 1) and a["score"] > b["score"]


def test_a_face_found_in_a_minority_of_frames_is_not_trusted():
    # A "face" seen once in a pattern or an object must not decide the keeper.
    assert pc.burst_faces([[_face()], [], [], []]) == [None] * 4


def _drops(*frames):
    return [f["drop"] for f in pc.burst_faces([list(f) for f in frames])]


def test_a_frame_is_dropped_for_closed_eyes_when_another_frame_has_them_open():
    assert _drops([_face(blink=0.9)], [_face()], [_face()]) == ["eyes closed", None, None]


def test_a_frame_is_dropped_for_a_much_weaker_expression_than_the_best_frame():
    assert _drops([_face(smile=0.05)], [_face(smile=0.8)]) == ["bad facial expression", None]
    assert _drops([_face(smile=0.6)], [_face(smile=0.8)]) == [None, None]  # a small difference is noise


def test_one_standout_frame_does_not_condemn_the_rest_of_a_long_burst():
    # Eight ordinary frames and one with a big grin: the eight are normal, not bad.
    frames = [[_face(smile=0.1)]] * 8 + [[_face(smile=0.9)]]
    assert _drops(*frames) == [None] * 9


def test_the_odd_frame_out_of_a_long_burst_is_dropped():
    frames = [[_face(smile=0.8)]] * 4 + [[_face(smile=0.1)]] + [[_face(blink=0.9, smile=0.8)]]
    assert _drops(*frames) == [None] * 4 + ["bad facial expression", "eyes closed"]


def test_frames_with_different_people_at_fault_are_both_kept():
    # Each frame has one person with closed eyes: neither is clearly the better one.
    a = [_face(blink=0.9), _face(x=0.8)]
    b = [_face(), _face(x=0.8, blink=0.9)]
    assert _drops(a, b) == [None, None]


def test_many_small_differences_in_a_crowd_do_not_add_up_to_a_drop():
    crowd = lambda smile: [_face(x=0.05 * k, w=0.03, smile=smile) for k in range(12)]
    assert _drops(crowd(0.5), crowd(0.6)) == [None, None]


def test_plan_drops_frames_with_bad_faces_and_keeps_the_rest_of_the_burst():
    items = [_item("blink", 900), _item("smile", 300), _item("also", 500)]
    faces = {0: [_face(blink=0.9, smile=0.8)], 1: [_face(smile=0.8)], 2: [_face(smile=0.7)]}
    plan = pc.build_plan(items, {}, [[0, 1, 2]], blur_below=20, rot_conf=0.9, faces=faces)
    assert plan["smile"]["keep"] and plan["also"]["keep"]
    assert (plan["blink"]["keep"], plan["blink"]["reason"], plan["blink"]["why"]) == (False, "facial", "eyes closed")
    assert plan["blink"]["kept_instead"] in ("smile", "also")
    assert plan["blink"]["eyes_closed"] == 1 and plan["smile"]["smile"] == 0.8


def test_plan_keeps_a_whole_burst_whose_faces_look_alike_or_that_has_none():
    items = [_item("a", 900), _item("b", 300), _item("c", 100), _item("d", 300)]
    faces = {0: [_face(sharp=40)], 1: [_face(sharp=200)], 2: [], 3: []}
    plan = pc.build_plan(items, {}, [[0, 1], [2, 3]], blur_below=20, rot_conf=0.9, faces=faces)
    assert all(plan[n]["keep"] for n in "abcd")


def test_face_scores_are_reused_from_the_cache(tmp_path, monkeypatch):
    (tmp_path / "face_scores.json").write_text(json.dumps({"version": pc.FACE_CACHE, "faces": {"a.jpg": [_face()], "b.jpg": []}}))
    monkeypatch.setattr(pc, "_faces_in", lambda *a: pytest.fail("cached photos were measured again"))
    assert pc.face_scores(tmp_path, {"a.jpg": "x", "b.jpg": "y"}) == {"a.jpg": [_face()], "b.jpg": []}


def test_face_scores_measure_only_new_photos_and_save_them(tmp_path, monkeypatch):
    (tmp_path / "face_scores.json").write_text(json.dumps({"version": pc.FACE_CACHE, "faces": {"a.jpg": []}}))
    monkeypatch.setattr(pc, "_faces_in", lambda path: [_face(w=0.5)] if path == "y" else pytest.fail(path))
    got = pc.face_scores(tmp_path, {"a.jpg": "x", "b.jpg": "y"}, jobs=0)
    assert got == {"a.jpg": [], "b.jpg": [_face(w=0.5)]}
    assert json.loads((tmp_path / "face_scores.json").read_text()) == {"version": pc.FACE_CACHE, "faces": got}


def test_face_measurement_finds_no_face_in_a_photo_without_one(tmp_path):
    pytest.importorskip("mediapipe")
    np = pytest.importorskip("numpy")
    from PIL import Image
    rng = np.random.default_rng(0)
    Image.fromarray(rng.integers(0, 255, (600, 800, 3), dtype=np.uint8)).save(tmp_path / "noise.jpg")
    assert pc._faces_in(str(tmp_path / "noise.jpg")) == []


def test_facial_cards_say_why_a_frame_is_dropped(tmp_path):
    items = [dict(_item(n, s), thumb=f"thumbs/{n}.jpg") for n, s in [("blink", 900), ("smile", 300)]]
    faces = {0: [_face(blink=0.9)], 1: [_face(smile=0.8)]}
    plan = pc.build_plan(items, {}, [[0, 1]], blur_below=20, rot_conf=0.9, faces=faces)
    pc.write_outputs(tmp_path, items, plan, {})
    page = (tmp_path / "review.html").read_text()
    data = {d["name"]: d for d in json.loads(page.split("const D = ")[1].split(";\n")[0])}
    assert "smile=0.8" in data["smile"]["note"]
    assert "eyes closed" not in data["blink"]["note"]  # said once, by the label
    assert data["blink"]["reason"] == "facial"
    # The reason is a label on the photo itself, not only in the small print, and in the viewer caption.
    assert data["blink"]["why"] == "eyes closed" and data["smile"]["why"] is None
    assert 'class="why"' in page.split("function card(")[1].split("\n}")[0]
    assert ".why{" in page.split("</style>")[0]
    assert "it.why" in page.split("function show(")[1].split("\n}")[0]
    assert "section('Facial (" in page and "Bursts" not in page
    summary = [e for e in _log(tmp_path) if e["event"] == "done"][-1]
    assert summary["facial"] == 1 and "burst_duplicates" not in summary


def test_faces_can_be_switched_off():
    seen = {}
    orig = pc.cmd_analyze
    try:
        pc.cmd_analyze = lambda a: seen.update(vars(a))
        pc.main(["analyze", "w", "--no-faces"])
        assert seen["faces"] is False
        pc.main(["analyze", "w"])
        assert seen["faces"] is True
    finally:
        pc.cmd_analyze = orig


def test_a_slightly_bigger_smile_does_not_outweigh_sharper_faces():
    items = [_item("soft", 300), _item("crisp", 300)]
    faces = {0: [_face(smile=0.83, sharp=40)], 1: [_face(smile=0.80, sharp=200)]}
    plan = pc.build_plan(items, {}, [[0, 1]], blur_below=20, rot_conf=0.9, faces=faces)
    assert plan["crisp"]["keep"]


def test_a_clearly_better_expression_outweighs_sharper_faces():
    items = [_item("dull", 300), _item("happy", 300)]
    faces = {0: [_face(smile=0.05, sharp=200)], 1: [_face(smile=0.85, sharp=150)]}
    plan = pc.build_plan(items, {}, [[0, 1]], blur_below=20, rot_conf=0.9, faces=faces)
    assert plan["happy"]["keep"]


def test_face_cache_from_an_older_version_is_measured_again(tmp_path, monkeypatch):
    (tmp_path / "face_scores.json").write_text(json.dumps({"a.jpg": [{"w": 0.2}]}))  # no version: old layout
    monkeypatch.setattr(pc, "_faces_in", lambda path: [])
    assert pc.face_scores(tmp_path, {"a.jpg": "x"}, jobs=0) == {"a.jpg": []}


def _who(person, rng, **kw):
    """A face of `person` (an index): their own direction plus a little noise."""
    import numpy as np
    e = np.zeros(16)
    e[person] = 1
    return dict(_face(**kw), emb=(e + 0.05 * rng.normal(size=16)).tolist())


def test_people_seen_on_several_days_are_known_and_passers_by_are_not():
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(0)
    # Person 0 is in all four bursts, each on its own day; person 1 fills several frames of one burst.
    bursts = [[[_who(0, rng), _who(1, rng)], [_who(0, rng), _who(1, rng)], [_who(1, rng)]]]
    bursts += [[[_who(0, rng)], [_who(0, rng)]] for _ in range(3)]
    assert pc.mark_familiar(bursts, days=[1, 2, 3, 4], min_days=3) == 1
    known = [[[f["known"] for f in frame] for frame in burst] for burst in bursts]
    assert known[0] == [[True, False], [True, False], [False]] and known[1] == [[True], [True]]


def test_someone_photographed_many_times_on_one_day_is_still_a_stranger():
    # A statue, a tour guide or a face on a screen fills many bursts of one visit.
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(0)
    bursts = [[[_who(0, rng)]] for _ in range(6)]
    assert pc.mark_familiar(bursts, days=[1, 2, 3, 4, 5, 5], min_days=3) == 1
    assert pc.mark_familiar(bursts, days=[7] * 6, min_days=3) == 0


def test_everyone_is_judged_when_the_folder_is_too_small_to_know_anyone():
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(0)
    bursts = [[[_who(0, rng)], [_who(0, rng)]]]
    assert pc.mark_familiar(bursts, days=[1], min_days=3) == 0
    assert all(f["known"] for burst in bursts for frame in burst for f in frame)


def test_a_stranger_with_closed_eyes_does_not_get_a_frame_dropped():
    me = lambda **kw: dict(_face(**kw), known=True)
    stranger = lambda **kw: dict(_face(x=0.8, **kw), known=False)
    frames = [[me(), stranger(blink=0.9)], [me(), stranger()], [me(), stranger()]]
    out = pc.burst_faces(frames)
    assert [o["drop"] for o in out] == [None] * 3 and out[0]["faces"] == 1
    assert pc.burst_faces([[stranger(blink=0.9)], [stranger()]]) == [None, None]  # only strangers: not judged


# ------------------------------------------------------------ review feedback

def _feedback(tmp_path):
    files = sorted((tmp_path / "feedback").glob("*.jsonl"))
    lines = [json.loads(l) for f in files for l in f.read_text().splitlines()]
    return files, [l for l in lines if "run" in l], {l["file"]: l for l in lines if "file" in l}


def test_feedback_records_say_whether_the_user_agreed_with_each_proposal():
    items = [_item("soft", 5), _item("soft2", 6), _item("turned", 100), _item("blink", 100), _item("ok", 100),
             _item("fine", 100)]
    plan = pc.build_plan(items, {2: [0.01, 0.97, 0.01, 0.01]}, [[3, 4]], blur_below=20, rot_conf=0.9,
                         faces={3: [_face(blink=0.9)], 4: [_face()]})
    recs = {r["file"]: r for r in pc.feedback_records(plan, {
        "soft2": {"keep": True, "rotate": 0},   # not blurry after all
        "turned": {"keep": True, "rotate": 3},  # turned, but the wrong way
        "ok": {"keep": False, "rotate": 0}})}   # the tool proposed nothing; the user excluded it
    assert "fine" not in recs  # never shown for review, so the user said nothing about it
    assert (recs["soft"]["category"], recs["soft"]["agreed"]) == ("blurry", True)
    assert (recs["soft2"]["category"], recs["soft2"]["agreed"], recs["soft2"]["final"]["keep"]) == ("blurry", False, True)
    assert recs["soft2"]["sharpness"] == 6  # the evidence the decision was made on
    assert (recs["turned"]["category"], recs["turned"]["agreed"]) == ("rotate", False)
    assert recs["turned"]["proposed"]["rotate"] == 1 and recs["turned"]["final"]["rotate"] == 3
    assert recs["turned"]["rotate_conf"] == 0.97
    assert (recs["blink"]["category"], recs["blink"]["agreed"], recs["blink"]["why"]) == ("facial", True, "eyes closed")
    assert (recs["ok"]["category"], recs["ok"]["agreed"]) == ("none", False)  # something the tool missed


def test_apply_saves_the_review_as_feedback_outside_the_work_folder(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    ov.write_text(json.dumps(_reviewed(work, **{"turn.jpg": {"keep": True, "rotate": 0}})))
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    files, runs, recs = _feedback(tmp_path)
    assert len(files) == 1 and files[0].name.startswith(src.name + "-")
    assert runs[0]["run"]["src"] == str(src) and runs[0]["run"]["params"] == {"blur_below": 500}
    assert recs["turn.jpg"]["agreed"] is False and recs["shot.png"]["agreed"] is True
    assert runs[0]["run"]["reviewed"] == len(recs) and runs[0]["run"]["overridden"] == 1


def test_applying_the_same_folder_again_replaces_its_feedback(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    for _ in range(2):
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    files, runs, _ = _feedback(tmp_path)
    assert len(files) == 1 and len(runs) == 1


def test_a_dry_run_saves_no_feedback(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov)])
    assert _feedback(tmp_path)[0] == []


def test_feedback_report_shows_how_often_each_kind_of_proposal_was_accepted(tmp_path, capsys):
    src, work, out, ov = _scanned(tmp_path)
    ov.write_text(json.dumps(_reviewed(work, **{"turn.jpg": {"keep": True, "rotate": 0}})))
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    capsys.readouterr()
    pc.main(["feedback"])
    report = capsys.readouterr().out
    rows = {l.split()[0]: l.split() for l in report.splitlines() if l.strip()}
    assert rows["rotate"][1:4] == ["1", "0", "0%"]  # proposed, accepted, share accepted
    assert rows["screenshot"][1:4] == ["1", "1", "100%"]
    assert "1 folder" in report


def test_feedback_report_says_so_when_there_is_nothing_yet(capsys):
    pc.main(["feedback"])
    assert "no feedback" in capsys.readouterr().out.lower()


# ------------------------------------------------------------ composition (accidental shots)

def test_confident_accidents_are_left_behind_and_records_and_normal_photos_kept():
    items = [_item("wall", 100), _item("ticket", 100), _item("view", 100), _item("maybe", 100), _item("soft", 5)]
    plan = pc.build_plan(items, {}, [], blur_below=20, rot_conf=0.9)
    pc.apply_composition(plan, {"wall": [0.97, 0.01, 0.02, 0], "ticket": [0.02, 0.95, 0.03, 0.1], "view": [0.0, 0.0, 1.0, 0],
                                "maybe": [0.4, 0.1, 0.5, 0], "soft": [0.99, 0.0, 0.01, 0]}, min_accident=0.5)
    assert (plan["wall"]["keep"], plan["wall"]["reason"], plan["wall"]["why"]) == (False, "composition", "looks accidental")
    assert plan["ticket"]["keep"] and plan["view"]["keep"] and plan["maybe"]["keep"]
    assert plan["soft"]["reason"] == "blurry"  # an earlier reason stands
    assert plan["wall"]["accident"] == 0.97 and plan["maybe"]["accident"] == 0.4  # evidence is logged either way


def test_composition_answers_are_cached_and_asked_again_only_if_the_rotation_changed(tmp_path, monkeypatch):
    asked = []

    def fake(dev, jobs, model):
        asked.extend(jobs)
        return [[0.9, 0.05, 0.05, 0.0]] * len(jobs)
    monkeypatch.setattr(pc, "_composition_on", fake)
    items = [{"name": "a", "thumb": "thumbs/a.jpg"}, {"name": "b", "thumb": "thumbs/b.jpg"}]
    got = pc.composition_scores(tmp_path, items, {"a": 0, "b": 1}, "m", devices=["cpu"])
    assert got == {"a": [0.9, 0.05, 0.05, 0.0], "b": [0.9, 0.05, 0.05, 0.0]}
    assert asked == [(str(tmp_path / "thumbs/a.jpg"), 0), (str(tmp_path / "thumbs/b.jpg"), 1)]  # judged as it will be turned
    del asked[:]
    pc.composition_scores(tmp_path, items, {"a": 0, "b": 2}, "m", devices=["cpu"])
    assert asked == [(str(tmp_path / "thumbs/b.jpg"), 2)]
    del asked[:]
    pc.composition_scores(tmp_path, items, {"a": 0, "b": 2}, "another-model", devices=["cpu"])
    assert len(asked) == 2


def test_composition_work_is_split_across_devices(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(pc, "ISOLATE_DEVICES", False)  # the fake cannot cross a process boundary
    monkeypatch.setattr(pc, "_composition_on",
                        lambda dev, jobs, model: seen.setdefault(dev, jobs) and [[0, 0, 1, 0]] * len(jobs))
    items = [{"name": str(k), "thumb": f"t/{k}.jpg"} for k in range(5)]
    got = pc.composition_scores(tmp_path, items, {}, "m", devices=["cuda:0", "cuda:1"])
    assert set(seen) == {"cuda:0", "cuda:1"} and len(got) == 5
    assert sorted(len(j) for j in seen.values()) == [2, 3]


def test_accidental_shots_get_their_own_tab_and_a_label(tmp_path):
    items = [dict(_item(n, 100), thumb=f"thumbs/{n}.jpg") for n in ("wall", "view")]
    plan = pc.build_plan(items, {}, [], blur_below=20, rot_conf=0.9)
    pc.apply_composition(plan, {"wall": [0.97, 0.01, 0.02, 0], "view": [0.0, 0.0, 1.0, 0]}, 0.5)
    summary = pc.write_outputs(tmp_path, items, plan, {})
    page = (tmp_path / "review.html").read_text()
    data = {d["name"]: d for d in json.loads(page.split("const D = ")[1].split(";\n")[0])}
    assert set(data) == {"wall"} and data["wall"]["why"] == "looks accidental" and "accident=0.97" in data["wall"]["note"]
    assert "section('Composition (' + by('composition').length" in page
    assert summary["composition"] == 1


def test_composition_check_can_be_switched_off_and_tuned():
    seen = {}
    orig = pc.cmd_analyze
    try:
        pc.cmd_analyze = lambda a: seen.update(vars(a))
        pc.main(["analyze", "w"])
        assert seen["composition"] is True and seen["accident_confidence"] == pc.ACCIDENT_CONFIDENCE
        pc.main(["analyze", "w", "--no-composition", "--accident-confidence", "0.9"])
        assert seen["composition"] is False and seen["accident_confidence"] == 0.9
    finally:
        pc.cmd_analyze = orig


def test_the_vision_model_calls_a_blank_frame_an_accident_and_a_sign_a_record(tmp_path):
    torch = pytest.importorskip("torch")
    hub = pytest.importorskip("huggingface_hub")
    if not torch.cuda.is_available() or not isinstance(
            hub.try_to_load_from_cache(pc.COMPOSITION_MODEL, "config.json"), str):
        pytest.skip("needs a GPU and the vision-language model already downloaded")
    np = pytest.importorskip("numpy")
    from PIL import Image
    rng = np.random.default_rng(0)
    blank = np.full((480, 640, 3), 150, np.uint8) + rng.integers(0, 6, (480, 640, 3), dtype=np.uint8)
    Image.fromarray(blank).save(tmp_path / "blank.jpg")
    _text_image(np).save(tmp_path / "sign.jpg")
    a, gone, b = pc._composition_on("cuda:0", [(str(tmp_path / "blank.jpg"), 0), (str(tmp_path / "never-written.jpg"), 0),
                                               (str(tmp_path / "sign.jpg"), 0)], pc.COMPOSITION_MODEL)
    assert gone is None  # a photo without a thumbnail is skipped, and the answers stay with their photos
    assert abs(sum(a[:3]) - 1) < 0.01 and a[0] > 0.5  # accident
    assert b[1] > b[0] and b[3] < 0.5                 # a record, but not a receipt


# ------------------------------------------------------------ receipts

def test_receipts_are_left_behind_unless_switched_off():
    items = [_item("bill", 100), _item("ticket", 100), _item("wall", 100)]
    scores = {"bill": [0.0, 0.99, 0.01, 0.97], "ticket": [0.0, 0.95, 0.05, 0.2], "wall": [0.9, 0.05, 0.05, 0.8]}
    plan = pc.apply_composition(pc.build_plan(items, {}, [], 20, 0.9), scores, min_accident=0.5, min_receipt=0.5)
    assert (plan["bill"]["keep"], plan["bill"]["reason"], plan["bill"]["receipt"]) == (False, "receipt", 0.97)
    assert plan["ticket"]["keep"]
    assert plan["wall"]["reason"] == "composition"  # an accident stays an accident
    plan = pc.apply_composition(pc.build_plan(items, {}, [], 20, 0.9), scores, min_accident=None, min_receipt=None)
    assert all(plan[n]["keep"] for n in ("bill", "ticket", "wall"))


def test_receipts_get_their_own_tab(tmp_path):
    items = [dict(_item(n, 100), thumb=f"thumbs/{n}.jpg") for n in ("bill", "view")]
    plan = pc.apply_composition(pc.build_plan(items, {}, [], 20, 0.9),
                                {"bill": [0.0, 0.99, 0.01, 0.97], "view": [0.0, 0.0, 1.0, 0.0]})
    summary = pc.write_outputs(tmp_path, items, plan, {})
    page = (tmp_path / "review.html").read_text()
    data = {d["name"]: d for d in json.loads(page.split("const D = ")[1].split(";\n")[0])}
    assert set(data) == {"bill"} and data["bill"]["reason"] == "receipt" and "receipt=0.97" in data["bill"]["note"]
    assert "section('Receipts (' + by('receipt').length" in page
    assert summary["receipts"] == 1


def test_receipt_check_can_be_switched_off_and_tuned():
    seen = {}
    orig = pc.cmd_analyze
    try:
        pc.cmd_analyze = lambda a: seen.update(vars(a))
        pc.main(["analyze", "w"])
        assert seen["receipts"] is True and seen["receipt_confidence"] == pc.RECEIPT_CONFIDENCE
        pc.main(["analyze", "w", "--no-receipts", "--receipt-confidence", "0.9"])
        assert seen["receipts"] is False and seen["receipt_confidence"] == 0.9
    finally:
        pc.cmd_analyze = orig


def test_an_older_composition_cache_without_receipt_answers_is_asked_again(tmp_path, monkeypatch):
    (tmp_path / "composition.json").write_text(json.dumps({"model": "m", "scores": {"a": {"rotate": 0, "p": [0, 0, 1]}}}))
    monkeypatch.setattr(pc, "_composition_on", lambda dev, jobs, model: [[0, 1, 0, 0.9]] * len(jobs))
    got = pc.composition_scores(tmp_path, [{"name": "a", "thumb": "t/a.jpg"}], {}, "m", devices=["cpu"])
    assert got == {"a": [0, 1, 0, 0.9]}


def _receipt_image():
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (420, 640), "white")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=22)
    lines = ["CORNER CAFE", "123 Example Street", "", "Receipt #1042", "2 x Coffee        7.00", "1 x Sandwich      8.50",
             "Subtotal         15.50", "Tax               1.55", "TOTAL            17.05", "", "VISA **** 0000", "Thank you!"]
    for row, line in enumerate(lines):
        d.text((40, 30 + row * 46), line, fill="black", font=font)
    return im


def test_the_vision_model_recognises_a_receipt(tmp_path):
    torch = pytest.importorskip("torch")
    hub = pytest.importorskip("huggingface_hub")
    if not torch.cuda.is_available() or not isinstance(
            hub.try_to_load_from_cache(pc.COMPOSITION_MODEL, "config.json"), str):
        pytest.skip("needs a GPU and the vision-language model already downloaded")
    _receipt_image().save(tmp_path / "receipt.jpg")
    (r,) = pc._composition_on("cuda:0", [(str(tmp_path / "receipt.jpg"), 0)], pc.COMPOSITION_MODEL)
    assert r[1] > r[0] and r[3] > 0.5


# ------------------------------------------------------------ review findings

def test_overrides_reviewed_against_an_older_plan_are_refused(tmp_path):
    # Re-running analyze can change what is proposed; the user's review then no longer covers the plan.
    src, work, out, ov = _scanned(tmp_path)
    scan = json.loads((work / "scan.json").read_text())["items"]
    pc.write_outputs(work, scan, pc.build_plan(scan, {}, [], blur_below=1, rot_conf=0.9), {})
    with pytest.raises(SystemExit) as e:
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert "review" in str(e.value).lower() and not out.exists()


def test_overrides_without_a_plan_id_are_refused(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    ov.write_text("{}")
    with pytest.raises(SystemExit):
        pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert not out.exists()


def test_the_same_proposals_keep_the_same_plan_id(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    before = json.loads((work / "plan.json").read_text())
    scan = json.loads((work / "scan.json").read_text())["items"]
    pc.write_outputs(work, scan, before["plan"], {"other": "params"})
    assert json.loads((work / "plan.json").read_text())["id"] == before["id"]
    page = (work / "review.html").read_text()
    assert before["id"] in page and "_plan" in page.split("function save(")[1].split("\n}")[0]


def test_apply_recovers_from_a_copy_that_was_cut_short(tmp_path):
    src, work, out, ov = _scanned(tmp_path)
    out.mkdir()
    (out / (pc.PART_PREFIX + "notes.txt")).write_bytes(b"half a fi")  # what an interrupted run leaves behind
    pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert _hashes(out)["notes.txt"] == _hashes(src)["notes.txt"]
    assert not list(out.glob(pc.PART_PREFIX + "*"))


def test_scanning_again_discards_full_size_previews_of_the_old_scan(tmp_path):
    # Previews are named by scan position; after the folder changed they would show the wrong photo.
    src, work, out, ov = _scanned(tmp_path)
    (work / "full").mkdir(exist_ok=True)
    (work / "full" / "000000.jpg").write_bytes(b"stale")
    pc.main(["scan", str(src), str(work), "--jobs", "2"])
    assert not (work / "full" / "000000.jpg").exists()


def test_review_page_escapes_file_names(tmp_path):
    items = [dict(_item('a"<b>.jpg', 5), thumb="thumbs/0.jpg")]
    pc.write_outputs(tmp_path, items, pc.build_plan(items, {}, [], 20, 0.9), {})
    page = (tmp_path / "review.html").read_text()
    card = page.split("function card(")[1].split("\n}")[0]
    assert "esc(it.name)" in card and "' + it.name + '" not in card
    assert "function esc(" in page or "const esc" in page


def test_the_vision_model_answers_the_same_on_two_gpus_as_on_one(tmp_path):
    # Loading the model on both GPUs from two threads of one process left one copy broken.
    torch = pytest.importorskip("torch")
    hub = pytest.importorskip("huggingface_hub")
    if torch.cuda.device_count() < 2 or not isinstance(
            hub.try_to_load_from_cache(pc.COMPOSITION_MODEL, "config.json"), str):
        pytest.skip("needs two GPUs and the vision-language model already downloaded")
    np = pytest.importorskip("numpy")
    from PIL import Image
    (tmp_path / "thumbs").mkdir()
    rng = np.random.default_rng(0)
    items = []
    for k, im in enumerate([_receipt_image(), _text_image(np),
                            Image.fromarray(np.full((480, 640, 3), 150, np.uint8) + rng.integers(0, 6, (480, 640, 3), dtype=np.uint8)),
                            _receipt_image().rotate(90, expand=True)]):
        im.save(tmp_path / f"thumbs/{k}.jpg")
        items.append({"name": str(k), "thumb": f"thumbs/{k}.jpg"})
    two = pc.composition_scores(tmp_path, items, {"3": 1}, devices=["cuda:0", "cuda:1"])
    (tmp_path / "composition.json").unlink()
    one = pc.composition_scores(tmp_path, items, {"3": 1}, devices=["cuda:0"])
    assert all(max(abs(a - b) for a, b in zip(two[n], one[n])) < 0.05 for n in one)
    assert one["0"][3] > 0.5 and one["3"][3] > 0.5 and one["2"][0] > 0.5


def test_each_stage_logs_how_long_its_steps_took(tmp_path):
    src, work = _folder(tmp_path), tmp_path / "work"
    pc.main(["scan", str(src), str(work), "--jobs", "2"])
    timing = [e for e in _log(work) if e["event"] == "timing"]
    assert timing and timing[-1]["stage"] == "scan"
    assert {"metadata", "images"} <= set(timing[-1]["seconds"]) and all(v >= 0 for v in timing[-1]["seconds"].values())
    clock = pc.Timer(work, "analyze")
    with clock.step("a"):
        pass
    with clock.step("a"):  # a step entered twice adds up
        pass
    clock.report()
    assert list(_log(work)[-1]["seconds"]) == ["a"]


# ------------------------------------------------------------ one pass: scan and analysis overlap

def test_photos_with_a_neighbour_in_time_are_the_only_possible_burst_members():
    assert pc.close_in_time([0, 10, 100, None, 200], 30) == [True, True, False, False, False]
    assert pc.close_in_time([50, 0, 25], 30) == [True, True, True]  # order of the list does not matter


def test_the_scan_reads_faces_from_the_pixels_it_already_decoded(tmp_path, monkeypatch):
    pytest.importorskip("cv2")  # _scan_image measures sharpness with OpenCV
    _photo(tmp_path / "a.jpg")
    monkeypatch.setattr(pc, "_faces_of", lambda rgb: [{"w": rgb.shape[1]}])
    with_faces = pc._scan_image((str(tmp_path / "a.jpg"), str(tmp_path / "t.jpg"), True))
    without = pc._scan_image((str(tmp_path / "a.jpg"), str(tmp_path / "u.jpg"), False))
    assert with_faces["faces"] == [{"w": 320}] and "faces" not in without
    assert with_faces["sharp_p90"] == without["sharp_p90"]
    assert sorted(p.name for p in tmp_path.glob("*.jpg*")) == ["a.jpg", "t.jpg", "u.jpg"]  # no half-written thumbnail


def test_scan_saves_the_faces_of_photos_taken_close_together(tmp_path):
    pytest.importorskip("mediapipe")
    src, work = _folder(tmp_path), tmp_path / "work"
    _photo(src / "alone.jpg", seed=5, when="2026:02:02 10:00:00")
    pc.main(["scan", str(src), str(work), "--jobs", "2"])
    saved = json.loads((work / "face_scores.json").read_text())
    assert saved["version"] == pc.FACE_CACHE
    assert set(saved["faces"]) == {"sharp.jpg", "soft.jpg", "turn.jpg"}  # not the screenshot, not the lone photo


def test_a_worker_waits_for_a_thumbnail_only_while_the_scan_is_running(tmp_path):
    import threading
    (tmp_path / "thumbs").mkdir()
    late = tmp_path / "thumbs" / "late.jpg"
    assert not pc._await(str(late))  # no scan running: a missing thumbnail stays missing
    (tmp_path / pc.SCANNING).touch()
    threading.Timer(0.3, late.touch).start()
    assert pc._await(str(late))
    threading.Timer(0.3, (tmp_path / pc.SCANNING).unlink).start()
    assert not pc._await(str(tmp_path / "thumbs" / "never.jpg"))  # the scan ended without it: an unreadable photo


def test_a_photo_that_never_got_a_thumbnail_is_skipped_not_fatal(tmp_path):
    pytest.importorskip("numpy")
    (tmp_path / "thumbs").mkdir()
    missing = str(tmp_path / "thumbs" / "x.jpg")
    assert pc._text_scores_on(None, [missing]) == [None]
    assert pc._load_square(missing).shape == (224, 224, 3)


def test_before_rotations_are_known_only_new_photos_are_judged_and_as_stored(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(pc, "_composition_on", lambda dev, jobs, model: asked.extend(jobs) or [[0, 0, 1, 0]] * len(jobs))
    items = [{"name": "a", "thumb": "thumbs/a.jpg"}, {"name": "b", "thumb": "thumbs/b.jpg"}]
    pc.composition_scores(tmp_path, items[:1], {"a": 1}, "m", devices=["cpu"])
    del asked[:]
    pc.composition_scores(tmp_path, items, None, "m", devices=["cpu"])
    assert asked == [(str(tmp_path / "thumbs/b.jpg"), 0)]  # "a" has an answer already, whatever its rotation will be
    del asked[:]
    got = pc.composition_scores(tmp_path, items, {"a": 1, "b": 0}, "m", devices=["cpu"])
    assert asked == [] and set(got) == {"a", "b"}


def test_run_gives_the_same_plan_as_scan_then_analyze(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    src = _folder(tmp_path)
    quick = ["--jobs", "2", "--no-composition", "--no-receipts", "--no-text-direction"]
    pc.main(["scan", str(src), str(tmp_path / "two"), "--jobs", "2"])
    pc.main(["analyze", str(tmp_path / "two")] + quick)
    pc.main(["run", str(src), str(tmp_path / "one")] + quick)
    one, two = (json.loads((tmp_path / w / "plan.json").read_text())["plan"] for w in ("one", "two"))
    assert one == two and len(one) == 4
    assert (tmp_path / "one" / "review.html").is_file() and not (tmp_path / "one" / pc.SCANNING).exists()
    assert json.loads((tmp_path / "one" / "scan.json").read_text())["items"] == \
        json.loads((tmp_path / "two" / "scan.json").read_text())["items"]


def test_scan_refuses_a_work_folder_that_holds_someone_elses_thumbs(tmp_path):
    src, work = _folder(tmp_path), tmp_path / "not-ours"
    (work / "thumbs").mkdir(parents=True)
    (work / "thumbs" / "precious.jpg").write_text("mine")
    with pytest.raises(SystemExit):
        pc.main(["scan", str(src), str(work), "--jobs", "2"])
    assert (work / "thumbs" / "precious.jpg").read_text() == "mine"


# ------------------------------------------------------------ speed changes must not change results

@pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")
def test_metadata_read_by_several_exiftool_processes_is_the_same_as_by_one(tmp_path):
    from PIL import Image
    names = [f"p {k:02d} é.jpg" for k in range(45)]  # enough files for more than one process; awkward names
    for k, n in enumerate(names):
        Image.new("RGB", (8 + k, 8), "white").save(tmp_path / n)
    one, several = pc.read_metadata(tmp_path, names, 1), pc.read_metadata(tmp_path, names, 4)
    assert set(one) == set(names) and several == one
    assert several["p 07 é.jpg"]["ImageWidth"] == 15


def test_a_text_worker_hands_back_gpu_memory_after_each_photo_without_changing_scores(tmp_path, monkeypatch):
    from PIL import Image

    class Reader:
        def detect(self, im):
            return [[1, 2, 3]], [[]]

        def readtext(self, im, batch_size):
            return [(None, "EXIT", 0.9)] if im.shape[1] == 60 else []  # readable only when not turned
    (tmp_path / "thumbs").mkdir()
    paths = []
    for k in range(2):
        Image.new("RGB", (60, 40), "white").save(tmp_path / f"thumbs/{k}.jpg")
        paths.append(str(tmp_path / f"thumbs/{k}.jpg"))
    plain = pc._text_scores_on(Reader(), paths)
    freed = []
    monkeypatch.setattr(pc, "FREE_GPU_MEMORY", lambda: freed.append(1))
    assert pc._text_scores_on(Reader(), paths) == plain == [[3.6, 0.0, 3.6, 0.0]] * 2
    assert len(freed) == 2


def test_run_copes_with_an_unreadable_photo_and_plans_like_scan_then_analyze(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    src = _folder(tmp_path)
    (src / "broken.jpg").write_bytes((src / "sharp.jpg").read_bytes()[:2000])  # a JPEG cut short
    quick = ["--jobs", "2", "--no-composition", "--no-receipts", "--no-text-direction"]
    pc.main(["scan", str(src), str(tmp_path / "two"), "--jobs", "2"])
    pc.main(["analyze", str(tmp_path / "two")] + quick)
    pc.main(["run", str(src), str(tmp_path / "one")] + quick)
    one, two = (json.loads((tmp_path / w / "plan.json").read_text())["plan"] for w in ("one", "two"))
    scans = [json.loads((tmp_path / w / "scan.json").read_text())["items"] for w in ("one", "two")]
    assert one == two and scans[0] == scans[1]
    assert [it for it in scans[0] if it["name"] == "broken.jpg"][0].get("error")
    final = pc.resolve({"items": scans[0], "orphan_json": []}, one, {})
    assert final["broken.jpg"]["keep"]  # never judged, always kept


def test_analysis_judges_every_photo_as_stored_first_and_asks_again_only_about_turned_ones(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    src, work = _folder(tmp_path), tmp_path / "work"
    pc.main(["scan", str(src), str(work), "--jobs", "2"])
    asked = []

    def fake(dev, jobs, model):
        asked.extend((Path(p).name, turns) for p, turns in jobs)
        return [[0.0, 0.0, 1.0, 0.0]] * len(jobs)
    monkeypatch.setattr(pc, "ISOLATE_DEVICES", False)  # the fake cannot cross a process boundary
    monkeypatch.setattr(pc, "_composition_on", fake)
    pc.main(["analyze", str(work), "--jobs", "2", "--no-text-direction"])
    plan = json.loads((work / "plan.json").read_text())["plan"]
    thumb = {it["name"]: Path(it["thumb"]).name for it in json.loads((work / "scan.json").read_text())["items"] if it["thumb"]}
    photos = ["sharp.jpg", "soft.jpg", "turn.jpg"]
    turned = [(thumb[n], plan[n]["rotate"]) for n in photos if plan[n]["rotate"]]
    assert sorted(asked) == sorted([(thumb[n], 0) for n in photos] + turned)
    assert all(plan[n]["accident"] == 0.0 for n in photos)


def test_scan_refuses_to_use_the_photo_folder_as_its_work_folder(tmp_path):
    src = _folder(tmp_path)
    before = _hashes(src)
    for work in (src, src / "work"):
        with pytest.raises(SystemExit):
            pc.main(["scan", str(src), str(work), "--jobs", "2"])
    assert _hashes(src) == before


def test_excluded_photos_are_marked_by_colour_not_dimmed():
    css = pc.REVIEW_HTML.split("</style>")[0]
    assert "opacity" not in css and "filter" not in css  # the user still has to judge the photo
    assert ".c.rej{border-color:#c33}" in css and ".c.rej .x{" in css  # red border and a red button instead


# ------------------------------------------------------------ line-by-line review findings

def test_apply_refuses_to_copy_into_the_photo_folder(tmp_path):
    # Copying a photo onto itself is a no-op, but rotating that "copy" would rewrite the original.
    src, work, _, ov = _scanned(tmp_path)
    before = _hashes(src)
    for out in (src, src / "keepers"):
        with pytest.raises(SystemExit):
            pc.main(["apply", str(src), str(work), str(out), "--overrides", str(ov), "--apply"])
    assert _hashes(src) == before


def test_scan_refuses_a_work_folder_that_belongs_to_another_photo_folder(tmp_path):
    # Everything measured is cached by file name, and IMG_0001 exists in every year's folder.
    src, work = _folder(tmp_path), tmp_path / "work"
    pc.main(["scan", str(src), str(work), "--jobs", "2", "--no-faces"])
    other = tmp_path / "other"
    other.mkdir()
    _photo(other / "sharp.jpg", seed=9)
    with pytest.raises(SystemExit):
        pc.main(["scan", str(other), str(work), "--jobs", "2", "--no-faces"])
    assert json.loads((work / "scan.json").read_text())["src"] == str(src)  # the first scan is untouched
    pc.main(["scan", str(src), str(work), "--jobs", "2", "--no-faces"])    # the same folder again is fine


def test_a_folder_with_nothing_to_judge_still_gets_a_plan(tmp_path):
    pytest.importorskip("torch")
    from PIL import Image
    src = tmp_path / "src"
    src.mkdir()
    Image.new("RGB", (100, 200), "white").save(src / "shot.png")
    _exiftool(src / "shot.png", "-UserComment=Screenshot")
    (src / "notes.txt").write_text("not media")
    pc.main(["run", str(src), str(tmp_path / "work"), "--jobs", "2"])
    plan = json.loads((tmp_path / "work" / "plan.json").read_text())["plan"]
    assert plan["shot.png"]["reason"] == "screenshot" and len(plan) == 1


def test_a_failing_face_reader_does_not_make_the_photo_unreadable(tmp_path, monkeypatch):
    pytest.importorskip("cv2")  # _scan_image measures sharpness with OpenCV
    _photo(tmp_path / "a.jpg")

    def broken(rgb):
        raise RuntimeError("face model failed")
    monkeypatch.setattr(pc, "_faces_of", broken)
    res = pc._scan_image((str(tmp_path / "a.jpg"), str(tmp_path / "t.jpg"), True))
    assert "error" not in res and res["sharp_p90"] > 0 and "faces" not in res


def test_a_full_size_preview_cut_short_is_not_left_looking_finished(tmp_path, monkeypatch):
    _photo(tmp_path / "a.jpg")

    def interrupted(a, b):
        raise KeyboardInterrupt
    monkeypatch.setattr(pc.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        pc._full_preview((str(tmp_path / "a.jpg"), str(tmp_path / "full.jpg")))
    assert not (tmp_path / "full.jpg").exists()
