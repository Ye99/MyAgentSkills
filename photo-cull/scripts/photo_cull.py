#!/usr/bin/env python3
"""photo_cull: propose and apply a cull of a flat folder of phone photos.

Subcommands:

  run      SRC WORK        scan and analyze in one pass, the two overlapping (fastest)
  scan     SRC WORK        read metadata, make thumbnails, measure sharpness and faces (CPU)
  analyze  WORK            find screenshots, sideways photos, blurry photos, bad faces
                           in bursts, accidental shots and receipts; write plan.json and review.html (GPU)
  apply    SRC WORK OUT    copy the keepers to OUT, rotating copies losslessly;
                           dry run unless --apply

  feedback                 how often past reviews accepted each kind of proposal

SRC is never modified. Nothing is deleted anywhere.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

THUMB_SIZE = 640
SHARP_LONG_SIDE = 2048
SHARP_GRID = 8


# ---------------------------------------------------------------- sidecars

def match_sidecars(media_names, json_names):
    """Pair Google Takeout JSON sidecars with their media files.

    `IMG_1.HEIC` owns `IMG_1.HEIC.supplemental-metadata.json`, and the duplicate
    `IMG_1(1).HEIC` owns `IMG_1.HEIC.supplemental-metadata(1).json`. Takeout
    truncates long names, so the part after the media name is only a prefix
    match. Returns ({media: [json, ...]}, [orphan json, ...]).
    """
    keys = {}
    for name in media_names:
        stem, ext = os.path.splitext(name)
        m = re.match(r"^(.*)\((\d+)\)$", stem)
        key = (m.group(1) + ext, m.group(2)) if m else (name, None)
        keys.setdefault(key, name)
    by_media = {name: [] for name in media_names}
    orphans = []
    for j in json_names:
        m = re.match(r"^(.*?)(?:\((\d+)\))?\.json$", j, re.IGNORECASE)
        core, n = m.group(1), m.group(2)
        best = None
        for (base, kn), name in keys.items():
            if kn == n and (core == base or core.startswith(base + ".")):
                if best is None or len(base) > len(best[0]):
                    best = (base, name)
        if best:
            by_media[best[1]].append(j)
        else:
            orphans.append(j)
    return by_media, orphans


# ---------------------------------------------------------------- scan

def capture_epoch(meta, sidecar):
    """Capture time as a float epoch, or None. Used only to order photos."""
    dto = meta.get("DateTimeOriginal")
    if isinstance(dto, str):
        try:
            t = datetime.strptime(dto[:19], "%Y:%m:%d %H:%M:%S")
            off = meta.get("OffsetTimeOriginal")
            tz = timezone.utc
            if isinstance(off, str) and re.match(r"^[+-]\d\d:\d\d$", off):
                sign = -1 if off[0] == "-" else 1
                tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[4:6])))
            sub = str(meta.get("SubSecTimeOriginal") or "0")
            return t.replace(tzinfo=tz).timestamp() + (float("0." + sub) if sub.isdigit() else 0.0)
        except ValueError:
            pass
    try:
        return float(sidecar["photoTakenTime"]["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None


def is_screenshot(name, meta, sidecar):
    if str(meta.get("UserComment") or "").strip().lower() == "screenshot":
        return True
    if str((sidecar or {}).get("description") or "").strip().lower() == "screenshot":
        return True
    return bool(re.match(r"^screen[ _-]?shot", name, re.IGNORECASE))


def tile_sharpness(gray):
    """Laplacian variance per tile of a float32 grayscale array.

    Returns (p90, median) over a SHARP_GRID x SHARP_GRID grid. A photo with a
    sharp subject and a soft background has a high p90; a photo that is soft
    everywhere (motion blur, missed focus) has a low p90.
    """
    import cv2
    import numpy as np
    lap = cv2.Laplacian(gray, cv2.CV_32F)
    h, w = lap.shape
    th, tw = h // SHARP_GRID, w // SHARP_GRID
    if th < 8 or tw < 8:
        v = float(lap.var())
        return v, v
    tiles = lap[: th * SHARP_GRID, : tw * SHARP_GRID].reshape(SHARP_GRID, th, SHARP_GRID, tw)
    var = tiles.var(axis=(1, 3)).ravel()
    return float(np.percentile(var, 90)), float(np.median(var))


# ---------------------------------------------------------------- decisions

def group_bursts(times, regions, max_gap, min_sim, window=6):
    """Group near-identical shots taken close together.

    `times` are capture epochs sorted ascending; `regions` is [N, R, D], one
    unit-length feature per image region, in the same order. The similarity of
    two photos is that of their *least* similar region, so the same backdrop
    with a different person in front of it does not count as the same shot.
    Two photos are linked when they are within `window` positions, at most
    `max_gap` seconds apart and at least `min_sim` similar.
    Returns a list of index lists (groups of 2 or more).
    """
    n = len(times)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, min(n, i + 1 + window)):
            if times[j] - times[i] > max_gap:
                break
            if float((regions[i] * regions[j]).sum(-1).min()) >= min_sim:
                parent[find(j)] = find(i)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def text_verdict(scores):
    """Clockwise quarter turns that make a photo's text readable, or None.

    `scores[k]` is how much text was read cleanly after k clockwise quarter
    turns. None when there is too little text, or no direction clearly wins
    (text running round a circle, a leaflet printed both ways up).
    """
    if not scores:
        return None
    ranked = sorted(range(4), key=lambda k: scores[k], reverse=True)
    best, second = scores[ranked[0]], scores[ranked[1]]
    if best < TEXT_MIN_SCORE or best < TEXT_MARGIN * (second + 1):
        return None
    return ranked[0]


FACE_REACH = 0.75         # the same person's face moves less than this many face widths between frames
FACE_MAIN = 0.5           # faces under this fraction of the largest face's width are bystanders
EYES_CLOSED = 0.6         # eye-closure score above this counts as closed eyes
EYES_PENALTY = 1.0        # a penalty, not a veto: glasses and hat shadows raise the score too
SMILE_WEIGHT = 0.5
SMILE_DROP = 0.5          # a smile this much weaker than the person's usual one in the burst is a bad expression
FACE_TIE = 0.25           # face scores this close are a tie: neither frame is the worse one


def burst_faces(frames):
    """Judge the faces in each frame of one burst.

    `frames` holds, per frame, a list of faces: {"x", "y": centre and "w":
    width, all as fractions of the photo's width, "blink" and "smile": 0..1 or
    None when the face could not be read, "sharp": sharpness of the face
    itself}. Returns one summary per frame, or all None when the burst shows
    no readable faces.

    A frame's `drop` says why it should be left behind ("eyes closed" or "bad
    facial expression"); otherwise it is None. A frame is dropped when it is
    clearly worse overall than the best frame, `better`, and some person in it
    looks worse than that person usually does in the other frames. So the odd
    frame out is dropped, while one standout frame does not condemn the rest.

    Faces marked `known: False` (strangers, see mark_familiar) are ignored.
    Each person is followed across the frames by position. Only people found
    in most frames count (one stray detection in an object is not a person),
    and only the larger faces (not bystanders). A frame's `score` adds, per
    person, 1 plus their smile, minus a penalty for closed eyes. A person who
    was not found or could not be read in a frame (profiles, pets and statues
    defeat the landmark model) gets their average from the other frames: that
    is not evidence either way.
    """
    people = []
    found = [(k, f) for k, faces in enumerate(frames) for f in faces or []
             if f["blink"] is not None and f.get("known", True)]
    for k, f in sorted(found, key=lambda kf: -kf[1]["w"]):
        near = [p for p in people if k not in p["at"]
                and max(abs(f["x"] - p["x"]), abs(f["y"] - p["y"])) < FACE_REACH * p["w"]]
        if near:
            min(near, key=lambda p: abs(f["x"] - p["x"]) + abs(f["y"] - p["y"]))["at"][k] = f
        else:
            people.append({"x": f["x"], "y": f["y"], "w": f["w"], "at": {k: f}})
    people = [p for p in people if 2 * len(p["at"]) > len(frames)]
    people = [p for p in people if p["w"] >= FACE_MAIN * max(q["w"] for q in people)]
    if not people:
        return [None] * len(frames)
    shut = lambda f: f["blink"] > EYES_CLOSED
    value = lambda f: 1 + SMILE_WEIGHT * f["smile"] - EYES_PENALTY * shut(f)
    usual = [sum(map(value, p["at"].values())) / len(p["at"]) for p in people]
    out = []
    for k in range(len(frames)):
        here = [p["at"][k] for p in people if k in p["at"]]
        out.append({
            "faces": len(here), "eyes_closed": sum(map(shut, here)),
            "smile": round(sum(f["smile"] for f in here) / len(here), 2) if here else None,
            "score": round(sum(value(p["at"][k]) if k in p["at"] else u for p, u in zip(people, usual)), 2),
            "face_sharp": round(sum(f["sharp"] for f in here) / len(here), 1) if here else 0,
            "drop": None})
    # The reference frame: the best score, or among near-equal scores (small
    # differences are noise) the one with the sharpest faces.
    top = max(o["score"] for o in out)
    best = max((k for k in range(len(out)) if out[k]["score"] >= top - FACE_TIE),
               key=lambda k: out[k]["face_sharp"])
    median = lambda v: sorted(v)[len(v) // 2] if len(v) % 2 else sum(sorted(v)[len(v) // 2 - 1:len(v) // 2 + 1]) / 2
    for k, o in enumerate(out):
        if o["score"] >= top - FACE_TIE:
            continue
        # Each person here against the same person in the other frames.
        pairs = [(p["at"][k], [f for j, f in p["at"].items() if j != k]) for p in people if k in p["at"]]
        if any(shut(f) and 2 * sum(map(shut, rest)) < len(rest) for f, rest in pairs):
            o["drop"] = "eyes closed"
        elif any(rest and f["smile"] < median([g["smile"] for g in rest]) - SMILE_DROP for f, rest in pairs):
            o["drop"] = "bad facial expression"
        if o["drop"]:
            o["better"] = best
    return out


FACE_SAME = 0.5           # cosine similarity of two face embeddings above which they are one person
FAMILIAR_DAYS = 3         # a person photographed on this many different days is someone the user knows


def mark_familiar(bursts, days, min_days=FAMILIAR_DAYS, min_sim=FACE_SAME):
    """Learn who keeps turning up in the folder, and mark everyone else a stranger.

    `bursts` is a list of bursts, each a list of frames, each a list of faces
    with an "emb" (face recognition embedding); `days` gives the day each
    burst was shot. Faces that look alike are linked into one person; a
    person seen on at least `min_days` different days is known. A passer-by,
    a statue or a tour guide may fill many frames, but of one day. Sets
    `known` on every face and returns the number of known people. When nobody
    is known (a small folder, a single outing), every face is marked known,
    so all are judged.
    """
    import numpy as np
    faces = [(day, f) for day, burst in zip(days, bursts) for frame in burst for f in frame or [] if f.get("emb")]
    everyone = [f for burst in bursts for frame in burst for f in frame or []]
    people = 0
    if faces:
        e = np.array([f["emb"] for _, f in faces], dtype=np.float32)
        e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
        parent = list(range(len(faces)))

        def root(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for a in range(0, len(e), 2048):  # in blocks, so memory stays flat however many faces there are
            for i, j in zip(*np.nonzero(e[a:a + 2048] @ e.T >= min_sim)):
                if a + i < j:
                    parent[root(a + int(i))] = root(int(j))
        seen = {}
        for i, (b, _) in enumerate(faces):
            seen.setdefault(root(i), set()).add(b)
        known = {r for r, where in seen.items() if len(where) >= min_days}
        people = len(known)
        for f in everyone:
            f["known"] = False
        for i, (_, f) in enumerate(faces):
            f["known"] = root(i) in known
    if not people:
        for f in everyone:
            f["known"] = True
    return people


def build_plan(items, rot_probs, bursts, blur_below, rot_conf, text=None, faces=None):
    """Turn measurements into one decision per image.

    `rot_probs` maps item index -> [p0..p3] from the scene model; `text` maps
    item index -> text scores (see text_verdict); `bursts` is a list of
    item-index groups. Precedence: screenshot, then blurry, then facial. `rotate` is the number of clockwise quarter turns to apply.

    Readable text outranks the scene model: it can cancel a proposed turn, and
    it can propose one unless the scene model is sure the photo is upright
    (book spines and vertical banners are sideways text in an upright photo).

    `faces` maps item index -> faces found in it (see burst_faces). Every
    frame of a burst is kept, except frames whose faces are clearly worse than
    in the other frames of the same burst (closed eyes, a much weaker
    expression): those are left behind with reason "facial".
    """
    text, faces = text or {}, faces or {}
    plan = {}
    for i, it in enumerate(items):
        if it["kind"] != "image":
            continue
        d = {"keep": True, "reason": None, "rotate": 0, "group": None,
             "sharpness": it.get("sharp_p90")}
        if it["screenshot"]:
            d.update(keep=False, reason="screenshot")
        elif it.get("error"):
            d["unreadable"] = it["error"]
        elif it["sharp_p90"] < blur_below:
            d.update(keep=False, reason="blurry")
        if i in rot_probs and d["reason"] != "screenshot":
            p = rot_probs[i]
            s = max(range(4), key=lambda k: p[k])
            if s and p[s] >= rot_conf:
                d.update(rotate=s, rotate_by="scene")
            d["rotate_guess"], d["rotate_conf"] = s, round(float(p[s]), 3)
            if text.get(i):
                d["text_scores"] = text[i]
                v = text_verdict(text[i])
                if v is not None and (v == 0 or p[0] < SCENE_UPRIGHT):
                    d.update(rotate=v, rotate_by="text")
        plan[it["name"]] = d
    for gid, group in enumerate(bursts):
        for i in group:
            plan[items[i]["name"]]["group"] = gid
        alive = [i for i in group if plan[items[i]["name"]]["keep"]]
        if len(alive) < 2:
            continue
        for i, f in zip(alive, burst_faces([faces.get(i) for i in alive])):
            if not f:
                continue
            d = plan[items[i]["name"]]
            d.update({k: f[k] for k in ("faces", "eyes_closed", "smile")})
            if f["drop"]:
                d.update(keep=False, reason="facial", why=f["drop"],
                         kept_instead=items[alive[f["better"]]]["name"])
    return plan


ACCIDENT_CONFIDENCE = 0.5  # leave a photo behind as an accidental shot above this probability
RECEIPT_CONFIDENCE = 0.5   # leave a photo behind as a receipt above this probability


def apply_composition(plan, scores, min_accident=ACCIDENT_CONFIDENCE, min_receipt=RECEIPT_CONFIDENCE):
    """Leave behind photos a vision-language model is confident were taken by
    accident, and photos of receipts (kept elsewhere for expenses, not wanted
    in a photo archive).

    `scores` maps name -> [accident, record, normal, receipt] probabilities
    (see composition_scores). Only photos the plan still keeps are affected,
    an accident before a receipt; `None` switches a check off. The
    probabilities are recorded on every scored photo.
    """
    for name, (accident, _, _, receipt) in scores.items():
        d = plan[name]
        d["accident"] = round(accident, 3)
        if receipt:
            d["receipt"] = round(receipt, 3)
        if d["keep"] and min_accident is not None and accident >= min_accident:
            d.update(keep=False, reason="composition", why="looks accidental")
        elif d["keep"] and min_receipt is not None and receipt >= min_receipt:
            d.update(keep=False, reason="receipt")
    return plan


def resolve(scan, plan, overrides):
    """Final per-file decision for every file in the source folder.

    Returns {filename: {"keep": bool, "reason": str|None, "rotate": int}}
    covering images, videos, sidecars, orphan JSON and anything else. A Live
    Photo's video and every sidecar follow the file they belong to.
    """
    final = {}
    for it in scan["items"]:
        if it["kind"] != "image":
            continue
        p = plan.get(it["name"], {})
        keep, reason, rotate = p.get("keep", True), p.get("reason"), p.get("rotate", 0)
        o = overrides.get(it["name"])
        if o:
            keep, rotate = bool(o["keep"]), int(o.get("rotate", 0))
            reason = None if keep else (reason or "manual")
        final[it["name"]] = {"keep": keep, "reason": reason, "rotate": rotate if keep else 0}
    for it in scan["items"]:
        if it["kind"] != "image":
            owner = final.get(it.get("live_of"))
            final[it["name"]] = {"keep": owner["keep"] if owner else True,
                                 "reason": owner["reason"] if owner else None, "rotate": 0}
    for it in scan["items"]:
        for j in it["sidecars"]:
            final[j] = {"keep": final[it["name"]]["keep"],
                        "reason": final[it["name"]]["reason"], "rotate": 0}
    for j in scan["orphan_json"]:
        final[j] = {"keep": True, "reason": None, "rotate": 0}
    return final


# ---------------------------------------------------------------- feedback

def feedback_records(plan, overrides):
    """What the review said about each proposal: one record per photo that was
    on the review page (or that the user changed).

    `category` is what the tool proposed: the reason for leaving the photo
    behind, "rotate" for a proposed turn, or "none" for a photo shown only as
    context. `agreed` is False when the user changed it; for "none" that marks
    something the tool missed. The measurements the decision rested on
    (sharpness, rotation confidence, face scores...) are copied along, so the
    thresholds can later be checked against what the user actually wanted.
    """
    out = []
    for name in dict.fromkeys(review_names(plan) + sorted(overrides)):
        p, o = plan[name], overrides.get(name)
        proposed = {"keep": p["keep"], "rotate": p["rotate"] if p["keep"] else 0}
        final = {"keep": bool(o["keep"]), "rotate": int(o.get("rotate", 0)) if o["keep"] else 0} if o else proposed
        rec = {"file": name, "category": p["reason"] or ("rotate" if p["rotate"] else "none"),
               "proposed": proposed, "final": final, "agreed": final == proposed}
        rec.update({k: v for k, v in p.items() if k not in ("keep", "reason", "rotate", "group", "kept_instead")})
        out.append(rec)
    return out


def feedback_dir(given=None):
    """Where review feedback is kept across folders and runs."""
    return Path(given or os.environ.get("PHOTO_CULL_FEEDBACK")
                or Path.home() / ".local" / "share" / "photo-cull" / "feedback")


def write_feedback(folder, src, work, plan, params, overrides):
    """Save one reviewed folder's feedback as FOLDER/<source name>-<hash>.jsonl:
    a `run` line, then one line per reviewed photo. Applying the same source
    again replaces the file, so a folder never counts twice."""
    src = Path(src)
    recs = feedback_records(plan, overrides)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{src.name}-{hashlib.sha1(str(src.resolve()).encode()).hexdigest()[:8]}.jsonl"
    run = {"src": str(src), "work": str(work), "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "params": params, "images": len(plan), "reviewed": len(recs),
           "overridden": sum(1 for r in recs if not r["agreed"])}
    path.write_text("".join(json.dumps(l) + "\n" for l in [{"run": run}] + recs))
    return path


def cmd_feedback(args):
    """Print how often each kind of proposal was accepted, over every folder reviewed so far."""
    folder = feedback_dir(args.feedback_dir)
    lines = [json.loads(l) for f in sorted(folder.glob("*.jsonl")) for l in f.read_text().splitlines()]
    recs = [l for l in lines if "file" in l]
    if not recs:
        print(f"No feedback yet in {folder}: it is saved by `apply --apply`.")
        return
    runs = sum(1 for l in lines if "run" in l)
    print(f"Review feedback from {runs} folder{'s' * (runs != 1)}, {len(recs)} photos reviewed ({folder})\n")
    detail = {"rotate": "rotate_by", "facial": "why"}
    groups = {}
    for r in recs:
        keys = [r["category"]] + ([f"  {r['category']}: {r[detail[r['category']]]}"]
                                  if r.get(detail.get(r["category"])) else [])
        for k in keys:
            g = groups.setdefault(k, [0, 0])
            g[0] += 1
            g[1] += r["agreed"]
    print(f"{'proposal':34s}{'proposed':>9s}{'accepted':>9s}{'share':>7s}")
    for k in sorted(groups, key=lambda k: (k.strip().split(":")[0], k.startswith(" "), k)):
        n, ok = groups[k]
        if k == "none":
            continue
        print(f"{k:34s}{n:9d}{ok:9d}{round(100 * ok / n):6d}%")
    missed = [r for r in recs if r["category"] == "none" and not r["agreed"]]
    print(f"\nChanged by hand with no proposal (missed by the tool): {len(missed)} "
          f"({sum(1 for r in missed if not r['final']['keep'])} excluded, "
          f"{sum(1 for r in missed if r['final']['keep'])} rotated)")
    print("Only photos on the review page can be judged, so photos the tool wrongly left alone "
          "mostly go unseen here.")


# ---------------------------------------------------------------- rotation

# EXIF Orientation <-> quarter turns counter-clockwise needed to display the
# stored pixels (the unit HEIF's irot box uses). Mirrored values are not handled.
EXIF_TO_CCW = {1: 0, 8: 1, 3: 2, 6: 3}
CCW_TO_EXIF = {v: k for k, v in EXIF_TO_CCW.items()}


def new_ccw(current_ccw, rotate_cw):
    """Display rotation (quarter turns CCW) after turning the picture
    `rotate_cw` quarter turns clockwise."""
    return (current_ccw - rotate_cw) % 4


def _boxes(buf, start, end):
    """Yield (type, payload_start, box_end) for ISO BMFF boxes in buf[start:end]."""
    pos = start
    while pos + 8 <= end:
        size, typ = struct.unpack(">I4s", buf[pos:pos + 8])
        head = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            head = 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            return
        yield typ, pos + head, pos + size
        pos += size


def find_primary_irot(buf):
    """Byte offset of the rotation value that applies to a HEIC's primary image.

    Returns None when the primary item has no irot property (then the file
    cannot be rotated by changing one byte).
    """
    top = {t: (s, e) for t, s, e in _boxes(buf, 0, len(buf))}
    if b"meta" not in top:
        return None
    ms, me = top[b"meta"]
    meta = {t: (s, e) for t, s, e in _boxes(buf, ms + 4, me)}  # skip FullBox header
    if b"pitm" not in meta or b"iprp" not in meta:
        return None
    ps = meta[b"pitm"][0]
    primary = (struct.unpack(">H", buf[ps + 4:ps + 6]) if buf[ps] == 0
               else struct.unpack(">I", buf[ps + 4:ps + 8]))[0]
    iprp = list(_boxes(buf, *meta[b"iprp"]))
    props = [list(_boxes(buf, s, e)) for t, s, e in iprp if t == b"ipco"]
    props = props[0] if props else []
    for t, s, e in iprp:
        if t != b"ipma":
            continue
        version, wide = buf[s], buf[s + 3] & 1
        count = struct.unpack(">I", buf[s + 4:s + 8])[0]
        pos = s + 8
        for _ in range(count):
            if version < 1:
                item = struct.unpack(">H", buf[pos:pos + 2])[0]
                pos += 2
            else:
                item = struct.unpack(">I", buf[pos:pos + 4])[0]
                pos += 4
            n = buf[pos]
            pos += 1
            for _ in range(n):
                if wide:
                    idx = struct.unpack(">H", buf[pos:pos + 2])[0] & 0x7FFF
                    pos += 2
                else:
                    idx = buf[pos] & 0x7F
                    pos += 1
                if item == primary and 1 <= idx <= len(props) and props[idx - 1][0] == b"irot":
                    return props[idx - 1][1]
    return None


def rotate_copy(path, rotate_cw):
    """Rotate a copied file by rewriting only its orientation flag.

    Returns None on success or a reason string when it cannot be done.
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext in (".heic", ".heif"):
        buf = path.read_bytes()
        off = find_primary_irot(buf)
        if off is None:
            return "HEIC has no rotation box to edit"
        st = path.stat()
        with open(path, "r+b") as f:
            f.seek(off)
            f.write(bytes([new_ccw(buf[off] & 3, rotate_cw)]))
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        return None
    if ext in (".jpg", ".jpeg"):
        cur = subprocess.run(["exiftool", "-n", "-s3", "-Orientation", str(path)],
                             capture_output=True, text=True).stdout.strip()
        cur = int(cur) if cur.isdigit() else 1
        if cur not in EXIF_TO_CCW:
            return f"mirrored EXIF orientation {cur}"
        new = CCW_TO_EXIF[new_ccw(EXIF_TO_CCW[cur], rotate_cw)]
        r = subprocess.run(["exiftool", "-n", "-P", "-overwrite_original", "-q", "-q",
                            f"-Orientation#={new}", str(path)], capture_output=True, text=True)
        return None if r.returncode == 0 else f"exiftool failed: {r.stderr.strip()}"
    return f"rotation not supported for {ext}"


# ---------------------------------------------------------------- log

LOG_NAME = "operations.jsonl"


def log_event(work, stage, event, **fields):
    """Append one JSON line to WORK/operations.jsonl: the audit trail of
    everything every stage measured, decided and did."""
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "stage": stage, "event": event, **fields}
    Path(work).mkdir(parents=True, exist_ok=True)
    with open(Path(work) / LOG_NAME, "a") as f:
        f.write(json.dumps(rec) + "\n")


class Timer:
    """Wall-clock seconds per step of a stage, logged as one `timing` event, so
    the slowest step of a real run can be read from the log."""

    def __init__(self, work, stage):
        self.work, self.stage, self.seconds = work, stage, {}

    def step(self, name):
        timer = self

        class _Step:
            def __enter__(self):
                self.t = time.monotonic()

            def __exit__(self, *exc):
                timer.seconds[name] = round(timer.seconds.get(name, 0) + time.monotonic() - self.t, 1)
        return _Step()

    def report(self):
        log_event(self.work, self.stage, "timing", seconds=self.seconds)
        print(f"[{self.stage}] seconds per step: {self.seconds}", file=sys.stderr)


# ---------------------------------------------------------------- scan stage

EXIF_TAGS = ["FileName", "MIMEType", "Make", "ImageWidth", "ImageHeight", "Orientation",
             "UserComment", "DateTimeOriginal", "SubSecTimeOriginal", "OffsetTimeOriginal"]
LIVE_VIDEO_EXT = {".mp4", ".mov"}


def _register_heif():
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass


def pixels(path):
    """Decoded RGB pixels as displayed (orientation flags applied)."""
    import numpy as np
    from PIL import Image, ImageOps
    _register_heif()
    return np.asarray(ImageOps.exif_transpose(Image.open(path)).convert("RGB"))


SCANNING = "scanning"  # exists in WORK while a scan is still writing thumbnails


def _await(path):
    """Wait for a thumbnail the scan has yet to write. False if the scan ended
    (or none is running) without it: the photo could not be read."""
    flag = Path(path).parent.parent / SCANNING
    while not os.path.exists(path):
        if not flag.exists():
            return os.path.exists(path)
        time.sleep(0.05)
    return True


def close_in_time(times, gap):
    """For each capture time (None: unknown), whether another photo was taken
    within `gap` seconds of it. Only such photos can be part of a burst."""
    known = sorted((t, k) for k, t in enumerate(times) if t is not None)
    near = [False] * len(times)
    for (t0, a), (t1, b) in zip(known, known[1:]):
        if t1 - t0 <= gap:
            near[a] = near[b] = True
    return near


def _scan_image(job):
    """Decode one photo once and take everything that needs its full pixels:
    sharpness, the thumbnail and, if asked, its faces."""
    src, thumb, want_faces = job
    try:
        import cv2
        import numpy as np
        from PIL import Image
        rgb = pixels(src)
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
        scale = SHARP_LONG_SIDE / max(g.shape)
        if scale < 1:
            g = cv2.resize(g, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        p90, med = tile_sharpness(g)
        im = Image.fromarray(rgb)
        im.thumbnail((THUMB_SIZE, THUMB_SIZE))
        im.save(thumb + ".part", "JPEG", quality=85)
        os.replace(thumb + ".part", thumb)  # the analysis may be waiting for it: never show it half-written
        res = {"sharp_p90": round(p90, 2), "sharp_med": round(med, 2)}
        if want_faces:
            try:
                res["faces"] = _faces_of(rgb)
            except Exception:  # the analysis measures the faces it needs itself, and reports what goes wrong
                pass
        return res
    except Exception as e:  # unreadable image: keep it, never judge it
        return {"error": f"{type(e).__name__}: {e}"}


def read_metadata(src, media, jobs):
    """exiftool's tags for each media file, {name: tags}. exiftool works
    through its files one at a time, so the list is shared out among several."""
    def read(part):
        return json.loads(subprocess.run(
            ["exiftool", "-json", "-n", "-fast2", "-q", "-q"] + [f"-{t}" for t in EXIF_TAGS] + ["-@", "-"],
            input="\n".join(str(src / f) for f in part), capture_output=True, text=True, check=False).stdout or "[]")
    n = max(1, min(jobs or 1, 12, len(media) // 20))
    with ThreadPoolExecutor(n) as pool:
        return {m["FileName"]: m for part in pool.map(read, [media[k::n] for k in range(n)]) for m in part}


def scan_list(src, work, clock, jobs=None):
    """Every file of `src` with its metadata, before any image is opened:
    (items, names of the json sidecars, json files that belong to no media file)."""
    names = sorted(f for f in os.listdir(src) if (src / f).is_file())
    jsons = [f for f in names if f.lower().endswith(".json")]
    media = [f for f in names if not f.lower().endswith(".json")]
    log_event(work, "scan", "start", src=str(src.resolve()), files=len(media), json_sidecars=len(jsons))
    print(f"[scan] {len(media)} files + {len(jsons)} json sidecars", file=sys.stderr)

    with clock.step("metadata"):
        meta = read_metadata(src, media, jobs)
    sidecars, orphans = match_sidecars(media, jsons)

    items = []
    for i, name in enumerate(media):
        m = meta.get(name, {})
        mime = str(m.get("MIMEType") or "")
        kind = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "other"
        sc = {}
        for j in sidecars[name][:1]:
            try:
                sc = json.loads((src / j).read_text())
            except (OSError, ValueError):
                pass
        items.append({
            "name": name, "kind": kind, "mime": mime, "make": m.get("Make"),
            "w": m.get("ImageWidth"), "h": m.get("ImageHeight"),
            "orientation": m.get("Orientation"), "t": capture_epoch(m, sc),
            "screenshot": kind == "image" and is_screenshot(name, m, sc),
            "sidecars": sidecars[name],
            "thumb": f"thumbs/{i:06d}.jpg" if kind == "image" else None,
        })

    # A Live Photo is an image plus one same-stem video; the video follows the image.
    by_stem = {}
    for it in items:
        by_stem.setdefault(os.path.splitext(it["name"])[0], []).append(it)
    for group in by_stem.values():
        imgs = [g for g in group if g["kind"] == "image"]
        vids = [g for g in group if g["kind"] == "video"
                and os.path.splitext(g["name"])[1].lower() in LIVE_VIDEO_EXT]
        if len(imgs) == 1 and len(vids) == 1:
            vids[0]["live_of"] = imgs[0]["name"]
    return items, jsons, orphans


def scan_images(src, work, items, jsons, orphans, jobs, faces, clock):
    """Open every image once (see _scan_image), fill in `items` and write
    WORK/scan.json. WORK/scanning exists until this returns, so an analysis
    running at the same time knows more thumbnails are coming."""
    try:
        todo = [it for it in items if it["kind"] == "image"]
        # Faces matter only within bursts, and a burst needs two photos taken close together.
        maybe = [it for it in todo if not it["screenshot"]]
        near = {it["name"] for it, ok in zip(maybe, close_in_time([it["t"] for it in maybe], BURST_SECONDS)) if ok}
        if faces and near:
            try:
                import mediapipe  # noqa: F401
                face_models()  # download once, before the workers start
                os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # the workers' model-loading chatter
            except ImportError:
                near = set()
        work_list = [(str(src / it["name"]), str(work / it["thumb"]), bool(faces) and it["name"] in near) for it in todo]
        found = {}
        # Low priority: an analysis running alongside needs little CPU, but needs it promptly to keep the GPUs fed.
        with clock.step("images"), ProcessPoolExecutor(max_workers=jobs, initializer=os.nice, initargs=(10,)) as ex:
            for n, (it, res) in enumerate(zip(todo, ex.map(_scan_image, work_list, chunksize=4)), 1):
                if "faces" in res:
                    found[it["name"]] = res.pop("faces")
                it.update(res)
                if res.get("error"):
                    log_event(work, "scan", "unreadable", file=it["name"], error=res["error"])
                if n % 500 == 0 or n == len(todo):
                    print(f"[scan] {n}/{len(todo)} images", file=sys.stderr)
        if found:
            save_faces(work, found)
        (work / "scan.json").write_text(json.dumps(
            {"src": str(src), "items": items, "orphan_json": orphans}, indent=1))
    finally:
        (work / SCANNING).unlink(missing_ok=True)
    summary = {"images": len(todo), "unreadable": sum(1 for it in items if it.get("error")),
               "screenshots": sum(1 for it in items if it["screenshot"]),
               "videos": sum(it["kind"] == "video" for it in items),
               "live_photo_videos": sum(1 for it in items if it.get("live_of")),
               "other": sum(it["kind"] == "other" for it in items),
               "json_sidecars": len(jsons), "orphan_json": len(orphans)}
    log_event(work, "scan", "done", **summary)
    clock.report()
    print(f"[scan] done: {summary}")


def scan_start(src, work):
    """An empty place for a new scan's thumbnails and previews. Both are named
    by position in the scan, so those of an earlier scan may show other photos."""
    if src.resolve() == work.resolve() or src.resolve() in work.resolve().parents:
        sys.exit(f"WORK ({work}) must be outside the photo folder: the source is never written to.")
    mine = (work / LOG_NAME).exists()
    if mine:  # everything measured is cached in WORK by file name, and names repeat from folder to folder
        for line in (work / LOG_NAME).read_text().splitlines():
            if '"event": "start"' in line and '"stage": "scan"' in line:
                earlier = json.loads(line).get("src")
                if earlier and Path(earlier).resolve() != src.resolve():
                    sys.exit(f"{work} holds the scan of another folder ({earlier}). Use a new WORK for {src}.")
    if not mine and any((work / d).exists() for d in ("full", "thumbs")):
        sys.exit(f"{work} has a thumbs or full folder but no {LOG_NAME}: it is not a work folder of this tool, "
                 "and scanning would empty those folders. Choose another WORK.")
    shutil.rmtree(work / "full", ignore_errors=True)
    shutil.rmtree(work / "thumbs", ignore_errors=True)
    (work / "thumbs").mkdir(parents=True)
    (work / "scan.json").unlink(missing_ok=True)
    (work / SCANNING).touch()


def cmd_scan(args):
    src, work = Path(args.src), Path(args.work)
    clock = Timer(work, "scan")
    scan_start(src, work)
    try:
        listed = scan_list(src, work, clock, args.jobs)
    except BaseException:
        (work / SCANNING).unlink(missing_ok=True)
        raise
    scan_images(src, work, *listed, args.jobs, args.faces, clock)


# ---------------------------------------------------------------- review page

REVIEW_HTML = """<!doctype html><meta charset="utf-8"><title>Photo cull review</title>
<style>
body{font:14px system-ui,sans-serif;margin:16px;background:#111;color:#ddd}
h2{margin:28px 0 4px}p{color:#999;margin:4px 0 10px}
.row{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,calc(var(--w,320px)*var(--zoom,1))),1fr));
gap:6px;margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #333}
.c{min-width:0;cursor:pointer;border:3px solid #2a7;position:relative;background:#000}
.c.rej{border-color:#c33}.c.rej .x{background:#c33;color:#fff;border-color:#c33}.c.off{border-color:#888}
.c .x,.c .r{position:absolute;top:2px;left:2px;font-size:12px;padding:2px 8px;cursor:pointer;z-index:1}
.c .r{left:auto;right:30px;font-size:14px}.c a{z-index:1}
.why{position:absolute;top:30px;left:2px;z-index:1;background:#c33;color:#fff;font-size:15px;font-weight:600;padding:2px 8px;border-radius:3px}
.c .box{width:100%;aspect-ratio:1;display:flex;align-items:center;justify-content:center;overflow:hidden}
.c img{width:100%;height:100%;object-fit:contain;display:block}
.c span{display:block;font-size:12px;padding:2px 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.c a{position:absolute;top:2px;right:4px;color:#fff;text-decoration:none;background:#0008;padding:0 4px}
.tab{display:none}.tab.on{display:block}
#tabs{margin-top:8px}#tabs button{margin-right:6px;background:#222;color:#bbb;border:1px solid #555;cursor:pointer}
#tabs button.on{background:#2a7;color:#000;border-color:#2a7;font-weight:600}
#bar{font-size:18px;position:sticky;top:0;background:#111;padding:8px 0;z-index:2;border-bottom:1px solid #444}
button{font:inherit;padding:6px 12px}
#view{display:none;position:fixed;inset:0;background:#000;z-index:5;overflow:auto}
#view.on{display:block}
#big{display:block;margin:auto;width:100vw;height:100vh;object-fit:contain;cursor:zoom-in}
#view.zoom #big{width:auto;height:auto;cursor:grab}#view.drag #big{cursor:grabbing}
#cap{position:fixed;top:0;left:0;right:0;background:#000b;padding:8px 12px;z-index:6;font-size:18px}
</style>
<div id="bar"><button onclick="save()">Download overrides.json</button>
<label>Preview size <input type="range" id="size" min="0.4" max="2" step="0.1" value="1"></label>
<button id="reset">Reset my changes</button> <span id="saved"></span>
<span id="count"></span><br>Green = keep, red = excluded (left behind). The Exclude button on any photo
excludes it or brings it back; so does clicking the photo, except under "Rotate", where a click turns the
proposed rotation off (grey border) or on again. The &#8635; button turns any photo a quarter turn clockwise;
what you see is what will be saved. The arrow in a photo's corner opens it full size; there the
left/right keys show the shots taken before and after it.
Your changes are saved in this browser as you go and restored when you reopen this page.
Download the file even if you change nothing.<div id="tabs"></div></div>
<div id="root"></div>
<div id="view"><div id="cap"></div><img id="big"></div>
<script>
const D = __DATA__;
// Every photo as [name, thumbnail], in capture order.
const T = __TIMELINE__;
// Fingerprint of the proposals on this page; apply checks it against the current plan.
const PLAN = __PLAN__;
const esc = s => String(s).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
const state = {};
for (const it of D) state[it.name] = {keep: it.keep, rotate: it.rotate};
// Progress survives closing the browser: every change is written to localStorage
// under this page's path and re-applied on load.
const KEY = 'photo-cull:' + location.pathname;
let canSave = true, restored = 0;
function changes() {
  const o = {};
  for (const it of D) { const s = state[it.name];
    if (s.keep !== it.keep || s.rotate !== it.rotate) o[it.name] = {keep: s.keep, rotate: s.rotate}; }
  return o;
}
try {
  const saved = JSON.parse(localStorage.getItem(KEY) || '{}');
  for (const n in saved) if (state[n]) {
    state[n].keep = !!saved[n].keep; state[n].rotate = (saved[n].rotate | 0) % 4;
    if (state[n].rotate) state[n].last = state[n].rotate;
    restored++;
  }
} catch (e) { canSave = false; }
function persist() {
  const o = changes(), n = Object.keys(o).length;
  try { localStorage.setItem(KEY, JSON.stringify(o)); } catch (e) { canSave = false; }
  const el = document.getElementById('saved');
  el.style.color = canSave ? '#6d9' : '#f66';
  el.textContent = canSave ? n + ' change' + (n === 1 ? '' : 's') + ' saved in this browser' +
      (restored ? ' (' + restored + ' restored from last time)' : '')
    : 'This browser is not letting the page save progress: download overrides.json before closing.';
}
const painters = {};  // name -> paint functions of every card showing that photo
const repaint = name => { painters[name].forEach(f => f()); count(); };
const commit = name => { repaint(name); persist(); };  // a change made by the user
function card(it, mode, row) {
  const c = document.createElement('div'); c.className = 'c';
  const st = state[it.name];
  c.innerHTML = '<div class="box"><img loading="lazy" src="' + it.thumb + '"></div><span title="' +
    esc(it.name) + ' ' + it.note + '">' + esc(it.name) + ' ' + it.note + '</span><a href="' + (it.full || it.thumb) +
    '" title="View full size">&#8599;</a><button class="x"></button><button class="r" title="Turn clockwise">&#8635;</button>' +
    (it.why ? '<b class="why">' + it.why + '</b>' : '');
  (painters[it.name] = painters[it.name] || []).push(() => {
    c.classList.toggle('rej', !st.keep);
    c.classList.toggle('off', mode === 'rot' && st.keep && st.rotate === 0);
    c.querySelector('img').style.transform = 'rotate(' + (st.rotate * 90) + 'deg)';
    c.querySelector('.x').textContent = st.keep ? 'Exclude' : 'Excluded \u2013 undo';
  });
  c.querySelector('a').onclick = e => { e.preventDefault(); e.stopPropagation(); openView(row, row.indexOf(it)); };
  c.querySelector('.x').onclick = e => { e.stopPropagation(); st.keep = !st.keep; commit(it.name); };
  c.querySelector('.r').onclick = e => {
    e.stopPropagation(); st.rotate = (st.rotate + 1) % 4; if (st.rotate) st.last = st.rotate; commit(it.name);
  };
  c.onclick = () => {
    if (mode === 'rot') st.rotate = st.rotate ? 0 : (st.last || it.rotate); else st.keep = !st.keep;
    commit(it.name);
  };
  setTimeout(() => repaint(it.name)); return c;
}
// Each section is a tab: only the chosen one is shown, and the choice is remembered.
const panes = [];
function section(title, help, rows, mode, w) {
  if (!rows.length || !rows[0].length) return;
  const pane = document.createElement('div'); pane.className = 'tab';
  pane.insertAdjacentHTML('beforeend', '<p>' + help + '</p>');
  for (const r of rows) {
    const div = document.createElement('div'); div.className = 'row'; div.style.setProperty('--w', w);
    r.forEach(it => div.appendChild(card(it, mode, r))); pane.appendChild(div);
  }
  document.getElementById('root').appendChild(pane);
  const b = document.createElement('button'), id = title.split(' (')[0];
  b.textContent = title; b.onclick = () => pick(id);
  document.getElementById('tabs').appendChild(b); panes.push({id, b, pane});
}
function pick(id) {
  const t = panes.find(p => p.id === id) || panes[0];
  if (!t) return;
  for (const p of panes) { p.b.classList.toggle('on', p === t); p.pane.classList.toggle('on', p === t); }
  scrollTo(0, 0);
  try { localStorage.setItem(KEY + ':tab', t.id); } catch (e) {}
}
function count() {
  const all = Object.values(state), ex = all.filter(s => !s.keep).length;
  const rot = all.filter(s => s.keep && s.rotate).length;
  document.getElementById('count').textContent = 'Of the ' + all.length + ' photos on this page, ' +
    (all.length - ex) + ' will be copied (' + rot + ' of them rotated) and ' + ex + ' excluded.' +
    ' Photos not shown here are copied unchanged.';
}
function save() {
  const o = Object.assign({_plan: PLAN}, changes());
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(o, null, 1)], {type: 'application/json'}));
  a.download = 'overrides.json'; a.click();
}
// Full-size viewer. Up/down step through the photos under review in the same row
// and keep the zoom position, so frames of a burst can be compared at 100%.
// Left/right walk every photo in capture order, to see what was shot before and after.
const view = document.getElementById('view'), big = document.getElementById('big'), cap = document.getElementById('cap');
const byName = {}, pos = {};
D.forEach(it => byName[it.name] = it); T.forEach((t, i) => pos[t[0]] = i);
let cur = null;
const home = () => pos[cur.row[cur.i].name];
function show() {
  const anchor = cur.row[cur.i], off = cur.t === undefined ? 0 : cur.t - home();
  const name = off ? T[cur.t][0] : anchor.name, it = byName[name], st = state[name];
  big.src = it ? (it.full || it.thumb) : T[cur.t][1];
  cap.textContent = (off ? 'CONTEXT: ' + Math.abs(off) + (off < 0 ? ' before ' : ' after ') + anchor.name
      : (cur.i + 1) + '/' + cur.row.length) + '   ' + name + '   ' + (it ? it.note + '   ' + (it.why ? it.why.toUpperCase() + '   ' : '') : '') +
    (st ? (st.keep ? 'KEEP' : 'EXCLUDED') : 'not under review, copied unchanged') +
    (it && it.full ? '' : '   (small preview only)') +
    '      ← → shot before/after · ↑ ↓ ' + (off ? 'back to ' + anchor.name : 'previous/next under review') +
    ' · click: 100% zoom, then drag to move · x: exclude/keep · Esc: close';
  cap.style.color = !st ? '#ccc' : st.keep ? '#6d9' : '#f66';
}
function openView(row, i) { cur = {row, i}; cur.t = home(); view.classList.remove('zoom'); view.classList.add('on'); show(); }
function closeView() { view.classList.remove('on'); cur = null; big.removeAttribute('src'); }
// Zoomed in, dragging the photo moves it; a click (no drag) zooms in on that spot or back out.
let drag = null, dragged = false;
big.ondragstart = e => e.preventDefault();
big.onmousedown = e => {
  if (e.button || !view.classList.contains('zoom')) return;
  drag = {x: e.clientX, y: e.clientY, left: view.scrollLeft, top: view.scrollTop}; dragged = false;
  view.classList.add('drag'); e.preventDefault();
};
window.onmousemove = e => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) dragged = true;
  view.scrollLeft = drag.left - dx; view.scrollTop = drag.top - dy;
};
window.onmouseup = () => { drag = null; view.classList.remove('drag'); };
big.onclick = e => {
  if (dragged) { dragged = false; return; }
  // Where in the photo the click landed; fitted to the window, the photo is letterboxed inside the element.
  const r = big.getBoundingClientRect(), k = Math.min(r.width / big.naturalWidth, r.height / big.naturalHeight);
  const fx = (e.clientX - r.left - (r.width - big.naturalWidth * k) / 2) / (big.naturalWidth * k);
  const fy = (e.clientY - r.top - (r.height - big.naturalHeight * k) / 2) / (big.naturalHeight * k);
  if (view.classList.toggle('zoom')) {
    view.scrollLeft = fx * big.naturalWidth - innerWidth / 2;
    view.scrollTop = fy * big.naturalHeight - innerHeight / 2;
  }
};
view.onclick = e => { if (e.target === view) closeView(); };
document.onkeydown = e => {
  if (!cur) return;
  if (e.key === 'Escape') closeView();
  else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    if (cur.t !== undefined) cur.t = Math.max(0, Math.min(T.length - 1, cur.t + (e.key === 'ArrowRight' ? 1 : -1)));
    show(); e.preventDefault();
  } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
    if (cur.t === home()) cur.i = (cur.i + (e.key === 'ArrowDown' ? 1 : cur.row.length - 1)) % cur.row.length;
    cur.t = home(); show(); e.preventDefault();
  } else if (e.key === 'x') {
    const name = cur.t === undefined ? cur.row[cur.i].name : T[cur.t][0];
    if (state[name]) { state[name].keep = !state[name].keep; commit(name); show(); }
  }
};
document.getElementById('reset').onclick = () => {
  if (!confirm('Discard all your changes on this page and go back to the proposed decisions?')) return;
  for (const it of D) { state[it.name].keep = it.keep; state[it.name].rotate = it.rotate; delete state[it.name].last; }
  restored = 0;
  for (const n in painters) painters[n].forEach(f => f());
  count(); persist();
};
persist();
const size = document.getElementById('size');
const zoom = z => document.documentElement.style.setProperty('--zoom', z);
try { size.value = localStorage.getItem('zoom') || 1; } catch (e) {}
zoom(size.value);
size.oninput = () => { zoom(size.value); try { localStorage.setItem('zoom', size.value); } catch (e) {} };
const by = r => D.filter(it => it.reason === r);
const groups = {};
for (const it of D) if (it.group !== null) (groups[it.group] = groups[it.group] || []).push(it);
const facial = Object.values(groups).filter(g => g.some(it => it.reason === 'facial'));
section('Rotate (' + D.filter(it => it.rotate).length + ')', 'Shown as they will look after rotation.',
  [D.filter(it => it.rotate)], 'rot', '320px');
section('Facial (' + facial.length + ' groups)', 'One row per burst of near-identical shots in which a frame has worse faces than the others: closed eyes or a much weaker expression. Only those frames are dropped; the rest of the burst is kept.',
  facial, 'keep', '420px');
section('Composition (' + by('composition').length + ')', 'Photos that look taken by accident (the shutter pressed unintentionally): a blank surface, the ground, a hand over the lens, smeared motion.',
  [by('composition')], 'keep', '320px');
section('Receipts (' + by('receipt').length + ')', 'Photos of receipts, bills and invoices: kept elsewhere for expenses, not wanted in the photo archive.',
  [by('receipt')], 'keep', '320px');
section('Blurry (' + by('blurry').length + ')', 'Soft everywhere in the frame.', [by('blurry')], 'keep', '420px');
section('Screenshots (' + by('screenshot').length + ')', 'Tagged as screenshots in their metadata.',
  [by('screenshot')], 'keep', '200px');
let lastTab = null;
try { lastTab = localStorage.getItem(KEY + ':tab'); } catch (e) {}
pick(lastTab);
</script>
"""


def plan_id(plan):
    """Short fingerprint of what a plan proposes for every photo. The review
    page puts it in overrides.json, and apply refuses overrides made against
    different proposals: a re-run of analyze may propose leaving behind
    photos the user never saw."""
    core = sorted((n, p["keep"], p["rotate"] if p["keep"] else 0) for n, p in plan.items())
    return hashlib.sha256(json.dumps(core).encode()).hexdigest()[:12]


def review_names(plan):
    """Names of the photos the review page shows, in plan order: everything
    the plan would leave behind or rotate, plus the kept frames of each
    burst that has a facial reject."""
    burst_groups = {p["group"] for p in plan.values() if p["reason"] == "facial"}
    return [n for n, p in plan.items()
            if not p["keep"] or p["rotate"] or p["group"] in burst_groups]


def _full_preview(job):
    src, dst = job
    try:
        from PIL import Image
        Image.fromarray(pixels(src)).save(dst + ".part", "JPEG", quality=90)
        os.replace(dst + ".part", dst)  # existing previews are trusted, so never leave half of one
        return True
    except Exception:
        return False


def make_previews(src, work, items, names, jobs=None):
    """Full-resolution, upright JPEG copies of the named photos in WORK/full,
    for judging focus in the review page (browsers cannot show HEIC).
    Existing previews are kept. Returns {name: path relative to WORK}.
    """
    src, work = Path(src), Path(work)
    (work / "full").mkdir(parents=True, exist_ok=True)
    wanted = set(names)
    rel = {it["name"]: "full/" + os.path.basename(it["thumb"])
           for it in items if it["name"] in wanted and it.get("thumb")}
    todo = [n for n, r in rel.items() if not (work / r).exists()]
    if todo:
        print(f"[analyze] making {len(todo)} full-size previews", file=sys.stderr)
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            ok = list(ex.map(_full_preview, [(str(src / n), str(work / rel[n])) for n in todo], chunksize=4))
        for n, good in zip(todo, ok):
            if not good:
                del rel[n]
    return rel


def write_outputs(work, items, plan, params, full=None):
    """Persist an analysis: plan.json, one log line per image, review.html.

    The review page shows every photo the plan would change (screenshots,
    blurry, bursts with a facial reject, rotations) and nothing else.
    """
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    pid = plan_id(plan)
    (work / "plan.json").write_text(json.dumps({"id": pid, "params": params, "plan": plan}, indent=1))
    log_event(work, "analyze", "params", **params)
    data, shown, full = [], set(review_names(plan)), full or {}
    for it in items:
        p = plan.get(it["name"])
        if not p:
            continue
        log_event(work, "analyze", "decision", file=it["name"], **p)
        if it["name"] not in shown:
            continue
        note = "" if p["sharpness"] is None else f"sharp={p['sharpness']:.0f}"
        if p["rotate"]:
            note += " by text" if p.get("rotate_by") == "text" else f" conf={p['rotate_conf']}"
        if p.get("reason") == "composition":
            note += f" accident={p['accident']}"
        if p.get("reason") == "receipt":
            note += f" receipt={p['receipt']}"
        if p.get("faces"):
            note += f" faces={p['faces']}" + ("" if p["smile"] is None else f" smile={p['smile']}")
            note += " eyes closed" * bool(p["eyes_closed"] and p.get("why") != "eyes closed")
        data.append({"name": it["name"], "thumb": it["thumb"], "full": full.get(it["name"]), "keep": p["keep"],
                     "reason": p["reason"], "rotate": p["rotate"], "group": p["group"],
                     "note": note, "why": p.get("why"), "t": it["t"] or 0})
    data.sort(key=lambda d: d["t"])
    timeline = [[it["name"], it["thumb"]] for it in sorted(items, key=lambda it: (it["t"] or 0, it["name"]))
                if it["name"] in plan and it.get("thumb")]
    embed_json = lambda v: json.dumps(v).replace("</", "<\\/")
    (work / "review.html").write_text(
        REVIEW_HTML.replace("__DATA__", embed_json(data)).replace("__TIMELINE__", embed_json(timeline))
        .replace("__PLAN__", embed_json(pid)))
    count = lambda r: sum(1 for p in plan.values() if p["reason"] == r)
    summary = {"images": len(plan), "screenshots": count("screenshot"), "blurry": count("blurry"),
               "facial": count("facial"), "composition": count("composition"), "receipts": count("receipt"),
               "rotate": sum(1 for p in plan.values() if p["rotate"]),
               "kept": sum(1 for p in plan.values() if p["keep"])}
    log_event(work, "analyze", "done", **summary)
    return summary


# ---------------------------------------------------------------- apply stage

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _place(s, d, turns):
    """Copy s to d (never overwriting a different file), rotate, verify.

    Returns (log fields, problem or None).
    """
    import numpy as np
    src_hash = sha256(s)
    if not d.exists():
        part = d.with_name(d.name + ".part")  # a copy cut short never looks like the finished file
        shutil.copy2(s, part)
        os.replace(part, d)
    same = sha256(d) == src_hash
    if not turns:
        return ({"rotate": 0, "verified": "sha256" if same else "MISMATCH"},
                None if same else "a different file with this name is already in the output")
    why = rotate_copy(d, turns) if same else None  # not same: rotated on an earlier run?
    if why:
        return {"rotate": 0, "rotate_skipped": why, "verified": "sha256"}, f"not rotated ({why})"
    if np.array_equal(np.rot90(pixels(s), -turns), pixels(d)):
        return {"rotate": turns, "verified": "pixels identical after rotation"}, None
    return {"rotate": turns, "verified": "MISMATCH"}, "output differs from the rotated source"


def cmd_apply(args):
    src, work, out = Path(args.src), Path(args.work), Path(args.out)
    if args.apply and not args.overrides:
        sys.exit("refusing to copy: pass --overrides with the overrides.json downloaded from "
                 "review.html, so the review happens before anything is copied")
    if src.resolve() == out.resolve() or src.resolve() in out.resolve().parents:
        sys.exit(f"OUT ({out}) must be outside the photo folder: the source is never written to.")
    scan = json.loads((work / "scan.json").read_text())
    saved = json.loads((work / "plan.json").read_text())
    plan = saved["plan"]
    overrides = json.loads(Path(args.overrides).read_text()) if args.overrides else {}
    reviewed = overrides.pop("_plan", None)
    if args.overrides and reviewed != plan_id(plan):
        sys.exit("refusing to continue: these overrides were not made against the current plan"
                 + (" (they carry no plan id)" if reviewed is None else " (analyze was run again since)")
                 + ". Open review.html again, review, and download a fresh overrides.json.")
    unknown = sorted(set(overrides) - set(plan))
    if unknown:
        sys.exit(f"overrides name files that are not in the plan: {unknown[:5]}")
    final = resolve(scan, plan, overrides)

    on_disk = {f for f in os.listdir(src) if (src / f).is_file()}
    if on_disk != set(final):
        sys.exit(f"source folder changed since the scan ({len(on_disk - set(final))} new, "
                 f"{len(set(final) - on_disk)} missing): re-run scan and analyze")
    keep = sorted(n for n, d in final.items() if d["keep"])
    left = {n: d["reason"] for n, d in sorted(final.items()) if not d["keep"]}
    reasons = {}
    for r in left.values():
        reasons[r] = reasons.get(r, 0) + 1
    summary = {"source_files": len(final), "keep": len(keep), "left_behind": len(left),
               "left_behind_by_reason": reasons,
               "rotate": sum(1 for n in keep if final[n]["rotate"]),
               "overrides": len(overrides),
               "keep_gb": round(sum((src / n).stat().st_size for n in keep) / 1e9, 2)}
    print(f"[apply] {summary}")
    if not args.apply:
        print("[apply] dry run, nothing copied. Re-run with --apply.")
        return

    log_event(work, "apply", "start", src=str(src), out=str(out), **summary)
    fb = write_feedback(feedback_dir(args.feedback_dir), src, work, plan, saved.get("params", {}), overrides)
    print(f"[apply] review feedback saved: {fb}", file=sys.stderr)
    for name, o in sorted(overrides.items()):
        log_event(work, "apply", "override", name=name, **o)
    out.mkdir(parents=True, exist_ok=True)
    report = {"src": str(src), "out": str(out), "kept": {}, "left_behind": left, "problems": []}
    for k, name in enumerate(keep, 1):
        fields, problem = _place(src / name, out / name, final[name]["rotate"])
        report["kept"][name] = fields
        log_event(work, "apply", "kept", file=name, **fields)
        if problem:
            report["problems"].append(f"{name}: {problem}")
        if k % 500 == 0 or k == len(keep):
            print(f"[apply] {k}/{len(keep)} copied and verified", file=sys.stderr)
    for name, reason in left.items():
        log_event(work, "apply", "left_behind", file=name, reason=reason)
    for stale in out.glob("*.part"):  # left by an earlier run that was interrupted
        if stale.name[:-5] in final:
            stale.unlink()
    extra = sorted(set(os.listdir(out)) - set(keep))
    if extra:
        report["problems"].append(f"unexpected files in output: {extra[:10]}")
    (work / "apply_report.json").write_text(json.dumps(report, indent=1))
    log_event(work, "apply", "done", kept=len(report["kept"]), left_behind=len(left),
              problems=report["problems"])
    print(f"[apply] done: {len(report['kept'])} kept, {len(left)} left behind in the source, "
          f"{len(report['problems'])} problems. Report: {work / 'apply_report.json'}")
    if report["problems"]:
        sys.exit("PROBLEMS:\n  " + "\n  ".join(report["problems"][:20]))


# ---------------------------------------------------------------- analyze stage

# Defaults calibrated on iPhone photos; see SKILL.md "Tuning".
BLUR_BELOW = 20.0
ROTATE_CONFIDENCE = 0.95
BURST_SECONDS = 30.0
BURST_SIMILARITY = 0.85


def _load_square(path, side=224):
    """Centre square of an image, resized to side x side, as uint8 HWC."""
    import numpy as np
    from PIL import Image
    if not _await(path):  # an unreadable photo: a blank stands in, and its row is dropped later
        return np.zeros((side, side, 3), dtype=np.uint8)
    im = Image.open(path).convert("RGB")
    s = side / min(im.size)
    im = im.resize((max(side, round(im.width * s)), max(side, round(im.height * s))))
    l, t = (im.width - side) // 2, (im.height - side) // 2
    return np.asarray(im.crop((l, t, l + side, t + side)), dtype=np.uint8)


REGION_GRID = 4
TEXT_MIN_SCORE = 20.0      # roughly 20 confidently read characters
TEXT_MARGIN = 3.0          # the best direction must read this many times better than the runner-up
SCENE_UPRIGHT = 0.9        # sideways text is ignored when the scene model is this sure the photo is upright


def default_devices():
    """Every CUDA device, or the CPU when there is none."""
    import torch
    return [f"cuda:{i}" for i in range(torch.cuda.device_count())] or ["cpu"]


ISOLATE_DEVICES = True  # one process per device; see _on_devices
WORKER_THREADS = 2      # CPU threads each such process may use for its numeric libraries


def _on_devices(fn, chunks, *args):
    """Run fn(device, part, *args) for every (device, part) of `chunks` at the
    same time and return the results in order, with the exception in place
    of a result that failed (so the caller can keep what did finish).

    Each device gets its own process. Loading a model on several GPUs from
    threads of one process is not safe (one copy came out with missing
    weights), and separate processes also keep the Python work around each
    GPU (decoding, pre- and post-processing) from queueing on one interpreter.
    """
    import multiprocessing

    def settle(job):
        try:
            return job.result()
        except Exception as e:
            return e
    if len(chunks) == 1:
        try:
            return [fn(chunks[0][0], chunks[0][1], *args)]
        except Exception as e:
            return [e]
    if not ISOLATE_DEVICES:
        with ThreadPoolExecutor(len(chunks)) as pool:
            return [settle(j) for j in [pool.submit(fn, d, part, *args) for d, part in chunks]]
    # Each worker is held to a few CPU threads. Left alone, every process starts one thread per
    # core for its numeric libraries, and two such workers ran slower than one. The libraries read
    # these variables when they load, so they are set for the moment the workers are started.
    limits = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")
    before = {v: os.environ.get(v) for v in limits}
    os.environ.update({v: str(WORKER_THREADS) for v in limits if before[v] is None})
    try:
        with ProcessPoolExecutor(len(chunks), mp_context=multiprocessing.get_context("spawn")) as pool:
            return [settle(j) for j in [pool.submit(fn, d, part, *args) for d, part in chunks]]
    finally:
        for v in limits:
            if before[v] is None:
                os.environ.pop(v, None)


def _split(devices, things):
    """`things` cut into one contiguous part per device: [(device, part), ...]."""
    import numpy as np
    bounds = np.linspace(0, len(things), len(devices) + 1).astype(int)
    return [(d, things[a:b]) for d, a, b in zip(devices, bounds, bounds[1:]) if b > a]


def _deal(devices, things):
    """`things` dealt out one at a time, like cards: [(device, part), ...].
    Photos that are slow for the same reason come in runs (a museum full of
    signs), and contiguous parts would leave one worker with all of them."""
    n = min(len(devices), len(things))
    return [(d, things[k::n]) for k, d in enumerate(devices[:n])]


def _embed_on(dev, paths, model_name, batch):
    import numpy as np
    import torch
    from transformers import AutoModel
    half = dev != "cpu"
    model = AutoModel.from_pretrained(model_name).to(dev).eval()
    model = model.half() if half else model
    mean = torch.tensor([0.485, 0.456, 0.406], device=dev).view(1, 3, 1, 1) * 255
    std = torch.tensor([0.229, 0.224, 0.225], device=dev).view(1, 3, 1, 1) * 255
    feats, regions = [], []
    with torch.no_grad(), ThreadPoolExecutor(8) as pool:
        for start in range(0, len(paths), batch):
            x = torch.from_numpy(np.stack(list(pool.map(_load_square, paths[start:start + batch]))))
            x = (x.to(dev).permute(0, 3, 1, 2).float() - mean) / std
            x = x.half() if half else x
            per_rot = []
            for k in range(4):
                h = model(pixel_values=torch.rot90(x, k, dims=(2, 3))).last_hidden_state
                per_rot.append(torch.cat([h[:, 0], h[:, 1:].mean(1)], dim=1))
                if k == 0:
                    side = int((h.shape[1] - 1) ** 0.5)
                    g = h[:, 1:].reshape(len(x), side, side, -1).permute(0, 3, 1, 2).float()
                    g = torch.nn.functional.adaptive_avg_pool2d(g, REGION_GRID).flatten(2).permute(0, 2, 1)
                    regions.append(torch.nn.functional.normalize(g, dim=2).cpu().numpy().astype(np.float16))
            feats.append(torch.stack(per_rot, 1).float().cpu().numpy().astype(np.float16))
    return np.concatenate(feats), np.concatenate(regions)


def embed(work, items, model_name, batch, devices=None):
    """DINOv2 features of every thumbnail, split evenly across `devices`
    (default: every GPU).

    Returns (feats, regions), both float16:
      feats   [N, 4, 2*D]  CLS token + mean patch token of the image turned
                           k quarter turns counter-clockwise, for k = 0..3
      regions [N, 16, D]   unit-length feature of each cell of a 4x4 grid
                           over the image as stored
    """
    import numpy as np
    chunks = _split(devices or default_devices(), [str(Path(work) / it["thumb"]) for it in items])
    print(f"[analyze] embedding {len(items)} photos on {[d for d, _ in chunks]}", file=sys.stderr)
    parts = _on_devices(_embed_on, chunks, model_name, batch)
    for p in parts:
        if isinstance(p, Exception):
            raise p
    return np.concatenate([p[0] for p in parts]), np.concatenate([p[1] for p in parts])


def _text_reader(languages, dev):
    """An EasyOCR reader living entirely on device `dev`."""
    import easyocr
    import torch
    reader = easyocr.Reader(languages, gpu=False if dev == "cpu" else dev, verbose=False)
    # EasyOCR wraps its models in DataParallel, which insists on the first GPU.
    for attr in ("detector", "recognizer"):
        model = getattr(reader, attr, None)
        if isinstance(model, torch.nn.DataParallel):
            setattr(reader, attr, model.module)
    return reader


def _text_scores_on(reader, paths):
    """Text scores of each image: None when it shows almost no text, else how
    much text reads cleanly after 0..3 clockwise quarter turns."""
    import numpy as np
    from PIL import Image
    out = []
    for path in paths:
        if not _await(path):
            out.append(None)
            continue
        im = np.asarray(Image.open(path).convert("RGB"))
        horizontal, free = reader.detect(im)
        if len(horizontal[0]) + len(free[0]) < 3:
            out.append(None)
            continue
        scores = []
        for k in range(4):
            found = reader.readtext(np.ascontiguousarray(np.rot90(im, -k)), batch_size=32)
            scores.append(round(float(sum(c * len(t) for _, t, c in found if c > 0.3 and len(t) > 1)), 1))
        out.append(scores)
        if FREE_GPU_MEMORY:
            FREE_GPU_MEMORY()
    return out


def _text_models(languages):
    """Download the OCR models once, before several processes ask for them at the same time."""
    import easyocr
    folder = Path.home() / ".EasyOCR" / "model"
    if ISOLATE_DEVICES and not (folder.is_dir() and any(folder.iterdir())):
        easyocr.Reader(languages, gpu=False, verbose=False)


TEXT_WORKERS_PER_GPU = 4   # reading text is held back by Python work around the GPU, not by the GPU
TEXT_WORKERS_SHARING = 2   # ... but each worker holds about 2 GB of GPU memory, and the vision model needs 10


FREE_GPU_MEMORY = None     # set in a text worker that shares its GPU


def _text_on(dev, paths, languages):
    """Text scores of each image, read on device `dev` (see _text_scores_on)."""
    global FREE_GPU_MEMORY
    if str(dev).startswith("cuda"):
        # A page full of text leaves gigabytes of freed GPU memory parked in this process. Hand it
        # back after each photo: the vision model on the same card needs the room.
        import torch
        FREE_GPU_MEMORY = torch.cuda.empty_cache
    return _text_scores_on(_text_reader(languages, dev), paths)


def text_scores(work, items, languages, devices=None, per_gpu=TEXT_WORKERS_PER_GPU):
    """Reading direction evidence for every thumbnail, split across `devices`.

    Returns {name: scores or None} (see _text_scores_on). Results are cached in
    WORK/text_scores.json; only photos not yet in the cache are read.
    """
    work = Path(work)
    cache = work / "text_scores.json"
    known = {}
    if cache.exists():
        saved = json.loads(cache.read_text())
        known = saved["scores"] if saved.get("languages") == languages else {}
    todo = [it for it in items if it["name"] not in known]
    if todo:
        devices = devices or default_devices()
        # Several workers share each GPU (see TEXT_WORKERS_PER_GPU); a worker is not worth starting for a few photos.
        share = max(1, min(per_gpu, len(todo) // (50 * len(devices)))) if ISOLATE_DEVICES else 1
        chunks = _deal([d for _ in range(share) for d in devices] if devices != ["cpu"] else devices, todo)
        print(f"[analyze] reading text in {len(todo)} photos with {len(chunks)} workers on {devices}", file=sys.stderr)
        _text_models(languages)
        failure = None
        results = _on_devices(_text_on, [(d, [str(work / it["thumb"]) for it in part]) for d, part in chunks], languages)
        for (_, part), got in zip(chunks, results):
            if isinstance(got, Exception):  # keep what the other devices finished
                failure = failure or got
            else:
                known.update({it["name"]: sc for it, sc in zip(part, got)})
        cache.write_text(json.dumps({"languages": languages, "scores": known}))
        if failure:
            raise failure
    return {it["name"]: known[it["name"]] for it in items}


FACE_MODELS = {
    "face_detection_yunet_2023mar.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
}
FACE_CACHE = 2             # layout of face_scores.json; older caches are measured again
FACE_DETECT_SIDE = 1280    # faces are searched for in the photo shrunk to this long side
FACE_MIN_WIDTH = 0.03      # as a fraction of the photo's width; smaller faces cannot be read
_face_tools = None


def face_models():
    """Paths of the three face models, downloaded to ~/.cache/photo-cull on first use."""
    import urllib.request
    folder = Path.home() / ".cache" / "photo-cull"
    folder.mkdir(parents=True, exist_ok=True)
    for name, url in FACE_MODELS.items():
        if not (folder / name).exists():
            print(f"[analyze] downloading {name}", file=sys.stderr)
            tmp = folder / (name + ".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.rename(folder / name)
    return [str(folder / name) for name in FACE_MODELS]


def _faces_in(path):
    """Faces in one photo file (see _faces_of)."""
    return _faces_of(pixels(path))


def _faces_of(rgb):
    """Faces in one photo's pixels: [{"x", "y", "w", "blink", "smile", "sharp", "emb"}]
    (see burst_faces and mark_familiar).

    A detector finds the faces in the whole photo; each is then cropped at
    full resolution and read by a landmark model, which only copes with faces
    that fill a good part of its input.
    """
    global _face_tools
    import cv2
    import numpy as np
    import mediapipe as mp
    if _face_tools is None:
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision
        yunet, sface, landmarker = face_models()
        _face_tools = (
            cv2.FaceDetectorYN.create(yunet, "", (320, 320), 0.7),
            cv2.FaceRecognizerSF.create(sface, ""),
            vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
                base_options=mpp.BaseOptions(model_asset_path=landmarker),
                output_face_blendshapes=True, num_faces=1)))
    detector, recogniser, reader = _face_tools
    h, w = rgb.shape[:2]
    scale = min(1.0, FACE_DETECT_SIDE / max(h, w))
    small = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    detector.setInputSize((small.shape[1], small.shape[0]))
    found = detector.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))[1]
    out, bgr = [], None
    for row in ([] if found is None else found):
        x, y, fw, fh = (float(v) / scale for v in row[:4])
        if fw < FACE_MIN_WIDTH * w:
            continue
        box = rgb[max(0, int(y)):int(y + fh), max(0, int(x)):int(x + fw)]
        if box.size == 0:
            continue
        gray = cv2.resize(cv2.cvtColor(box, cv2.COLOR_RGB2GRAY), (128, 128), interpolation=cv2.INTER_AREA)
        face = {"x": round((x + fw / 2) / w, 4), "y": round((y + fh / 2) / w, 4),
                "w": round(fw / w, 4), "blink": None, "smile": None,
                "sharp": round(float(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F).var()), 1)}
        # Who it is: an embedding of the face aligned on its eyes, nose and mouth at full resolution.
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR) if bgr is None else bgr
        full_row = np.concatenate([row[:14] / scale, row[14:]]).astype(np.float32)
        face["emb"] = [round(float(v), 4) for v in recogniser.feature(recogniser.alignCrop(bgr, full_row))[0]]
        side, cx, cy = max(fw, fh), x + fw / 2, y + fh / 2
        crop = rgb[max(0, int(cy - side)):int(cy + side), max(0, int(cx - side)):int(cx + side)]
        if max(crop.shape[:2]) > 512:
            k = 512 / max(crop.shape[:2])
            crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        got = reader.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)))
        if got.face_blendshapes:
            b = {c.category_name: c.score for c in got.face_blendshapes[0]}
            face["blink"] = round(max(b["eyeBlinkLeft"], b["eyeBlinkRight"]), 2)
            face["smile"] = round((b["mouthSmileLeft"] + b["mouthSmileRight"]) / 2, 2)
        out.append(face)
    return out


def save_faces(work, found):
    """Add {name: faces} to WORK/face_scores.json."""
    cache = Path(work) / "face_scores.json"
    saved = json.loads(cache.read_text()) if cache.exists() else {}
    known = saved["faces"] if saved.get("version") == FACE_CACHE else {}
    known.update(found)
    cache.write_text(json.dumps({"version": FACE_CACHE, "faces": known}))
    return known


def face_scores(work, paths, jobs=None):
    """Faces in each photo of `paths` ({name: image path}), measured on all
    cores (`jobs=0`: in this process). Cached in WORK/face_scores.json; only
    photos not yet in the cache are measured. The scan has usually measured
    them all already, while it had each photo open.
    """
    cache = Path(work) / "face_scores.json"
    saved = json.loads(cache.read_text()) if cache.exists() else {}
    known = saved["faces"] if saved.get("version") == FACE_CACHE else {}
    todo = [n for n in paths if n not in known]
    if todo:
        print(f"[analyze] looking at faces in {len(todo)} burst photos", file=sys.stderr)
        if jobs == 0:
            found = [_faces_in(paths[n]) for n in todo]
        else:
            face_models()  # download once, before the workers start
            os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # the workers' model-loading chatter
            with ProcessPoolExecutor(max_workers=jobs) as ex:
                found = list(ex.map(_faces_in, [paths[n] for n in todo], chunksize=4))
        known = save_faces(work, dict(zip(todo, found)))
    return {n: known[n] for n in paths}


COMPOSITION_MODEL = "Qwen/Qwen3-VL-4B-Instruct"
COMPOSITION_QUESTION = (
    "Classify this photo into one of three kinds.\n"
    "A: taken by accident. The shutter was pressed unintentionally: the floor or ground, a pocket, a hand or finger "
    "over the lens, smeared motion, a blank surface, nothing in particular.\n"
    "B: a deliberate record. A document, sign, screen, label, receipt, ticket or object photographed to keep the "
    "information or to show its condition.\n"
    "C: a normal deliberate photo of people, a place, a scene, food, an animal or anything else.\n"
    "Answer with one letter: A, B or C.")
RECEIPT_QUESTION = ("Is this a photo of a receipt, bill, invoice or payment slip, that is, a printed or handwritten "
                    "record of a purchase or payment? Answer with one word: Yes or No.")
RECEIPT_ASK = 0.3          # the receipt question is put only to photos this likely to be a deliberate record
COMPOSITION_CACHE = 2      # layout of composition.json; older caches are asked again


def _composition_on(dev, jobs, model_name, batch=8):
    """Ask a vision-language model, on one device, what kind of photo each of
    `jobs` [(thumbnail path, clockwise quarter turns)] is. Returns
    [accident, record, normal, receipt] probabilities per job: the first three
    are the model's next-word probabilities for the answers A, B and C; the
    last is its probability of "Yes" to the receipt question, asked only of
    likely records (0 otherwise)."""
    import torch
    from PIL import Image
    from transformers import AutoModelForImageTextToText, AutoProcessor
    proc = AutoProcessor.from_pretrained(model_name)
    proc.tokenizer.padding_side = "left"  # so the answer position is the last one in every row
    dtype = torch.bfloat16 if str(dev).startswith("cuda") else torch.float32
    model = AutoModelForImageTextToText.from_pretrained(model_name, dtype=dtype).to(dev).eval()
    ask = lambda q: proc.apply_chat_template(
        [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": q}]}],
        add_generation_prompt=True, tokenize=False)
    ids = lambda words: [proc.tokenizer(w, add_special_tokens=False).input_ids[0] for w in words]

    def answer(question, imgs, words):
        x = proc(text=[ask(question)] * len(imgs), images=[[im] for im in imgs], return_tensors="pt", padding=True).to(dev)
        with torch.no_grad():  # only the answer position goes through the vocabulary layer: same numbers, less work
            last = model.model(**x).last_hidden_state[:, -1]
            return model.lm_head(last)[:, ids(words)].float().softmax(-1).tolist()
    out = []
    for a in range(0, len(jobs), batch):
        here = [k for k, (path, _) in enumerate(jobs[a:a + batch]) if _await(path)]
        imgs = [Image.open(jobs[a + k][0]).convert("RGB").rotate(-90 * jobs[a + k][1], expand=True) for k in here]
        kinds = answer(COMPOSITION_QUESTION, imgs, "ABC") if imgs else []
        records = [k for k, p in enumerate(kinds) if p[1] >= RECEIPT_ASK]
        receipt = dict(zip(records, (p[0] for p in answer(RECEIPT_QUESTION, [imgs[k] for k in records], ["Yes", "No"])))) \
            if records else {}
        got = dict(zip(here, ([round(v, 4) for v in p] + [round(receipt.get(k, 0.0), 4)] for k, p in enumerate(kinds))))
        out.extend(got.get(k) for k in range(len(jobs[a:a + batch])))  # None: the photo has no thumbnail
        if (a // batch) % 50 == 0:
            print(f"[analyze] {dev}: {a + len(here)}/{len(jobs)} photos judged", file=sys.stderr)
    del model
    if str(dev).startswith("cuda"):
        torch.cuda.empty_cache()
    return out


def composition_scores(work, items, rotations, model_name=COMPOSITION_MODEL, devices=None):
    """[accident, record, normal, receipt] probabilities for every thumbnail, judged as
    it will look after its proposed rotation (`rotations`: name -> clockwise
    quarter turns), split across `devices`.

    Cached in WORK/composition.json; a photo is asked again only when its
    rotation or the model changed.

    `rotations=None` means they are not known yet: photos without an answer
    are judged as stored, which is how nearly all of them will stay. A later
    call with the real rotations then only has the turned ones left to ask.
    """
    work = Path(work)
    cache = work / "composition.json"
    saved = json.loads(cache.read_text()) if cache.exists() else {}
    known = saved["scores"] if (saved.get("model"), saved.get("version")) == (model_name, COMPOSITION_CACHE) else {}
    turns = lambda it: (rotations or {}).get(it["name"], 0)
    todo = [it for it in items if (it["name"] not in known if rotations is None
                                   else known.get(it["name"], {}).get("rotate") != turns(it))]
    if todo:
        chunks = _deal(devices or default_devices(), todo)
        print(f"[analyze] judging {len(todo)} photos for accidental shots and receipts on {[d for d, _ in chunks]}",
              file=sys.stderr)
        if len(chunks) > 1 and ISOLATE_DEVICES:  # download once, before several processes ask for the model
            from huggingface_hub import snapshot_download
            snapshot_download(model_name)
        failure = None
        results = _on_devices(_composition_on, [(d, [(str(work / it["thumb"]), turns(it)) for it in part])
                                                for d, part in chunks], model_name)
        for (_, part), got in zip(chunks, results):
            if isinstance(got, Exception):  # keep what the other devices finished
                failure = failure or got
            else:
                known.update({it["name"]: {"rotate": turns(it), "p": p} for it, p in zip(part, got) if p})
        cache.write_text(json.dumps({"model": model_name, "version": COMPOSITION_CACHE, "scores": known}))
        if failure:
            raise failure
    return {it["name"]: known[it["name"]]["p"] for it in items if it["name"] in known}


def predict_rotation(feats, order, folds=5, block=40, epochs=25, seed=0):
    """Self-supervised rotation check.

    Assume most photos are upright. Train a small classifier to tell which of
    the four rotations of a photo it is looking at, then ask it about each
    photo as stored. `order` lists photo indices by capture time; photos are
    held out in blocks of `block` consecutive shots, so a photo is never
    judged by a model trained on its own burst.

    Returns float array [N, 4]: probability that the photo, as displayed now,
    is an upright photo turned s quarter turns counter-clockwise (so it needs
    s clockwise turns). Column 0 means "already upright".
    """
    import numpy as np
    import torch
    torch.manual_seed(seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    n, _, d = feats.shape
    fold_of = np.empty(n, dtype=np.int64)
    fold_of[np.asarray(order)] = (np.arange(n) // block) % folds
    X = torch.from_numpy(np.asarray(feats, dtype=np.float32)).to(dev)
    X = (X - X.mean((0, 1))) / (X.std((0, 1)) + 1e-6)
    fold_t = torch.from_numpy(fold_of).to(dev)
    probs = torch.zeros(n, 4, device=dev)
    for f in range(folds):
        tr = (fold_t != f).nonzero().squeeze(1)
        te = (fold_t == f).nonzero().squeeze(1)
        if len(te) == 0 or len(tr) == 0:
            probs[te, 0] = 1.0
            continue
        head = torch.nn.Sequential(
            torch.nn.Linear(d, 512), torch.nn.ReLU(), torch.nn.Dropout(0.3),
            torch.nn.Linear(512, 4)).to(dev)
        opt = torch.optim.AdamW(head.parameters(), lr=1e-3, weight_decay=1e-2)
        xtr = X[tr].reshape(-1, d)
        ytr = torch.arange(4, device=dev).repeat(len(tr))
        for _ in range(epochs):
            head.train()
            for idx in torch.randperm(len(xtr), device=dev).split(1024):
                opt.zero_grad()
                torch.nn.functional.cross_entropy(head(xtr[idx]), ytr[idx]).backward()
                opt.step()
        head.eval()
        with torch.no_grad():
            logp = torch.log_softmax(head(X[te].reshape(-1, d)), 1).reshape(len(te), 4, 4)
        # View k of a photo in state s looks like an upright photo turned (s + k).
        ll = torch.stack([sum(logp[:, k, (s + k) % 4] for k in range(4)) / 4
                          for s in range(4)], 1)
        probs[te] = torch.softmax(ll, 1)
    return probs.cpu().numpy()


def cmd_analyze(args):
    work = Path(args.work)
    analyze(work, json.loads((work / "scan.json").read_text()), args)


def cmd_run(args):
    """Scan and analyze in one pass: the analysis works on each thumbnail as
    soon as the scan has written it, instead of waiting for the last one."""
    src, work = Path(args.src), Path(args.work)
    clock = Timer(work, "scan")
    scan_start(src, work)
    try:
        items, jsons, orphans = scan_list(src, work, clock, args.jobs)
    except BaseException:
        (work / SCANNING).unlink(missing_ok=True)
        raise
    with ThreadPoolExecutor(1) as background:
        scanning = background.submit(scan_images, src, work, items, jsons, orphans, args.jobs, args.faces, clock)
        analyze(work, {"src": str(src), "items": items}, args, scanning)


def analyze(work, scan, args, scanning=None):
    """Decide about every photo of `scan` and write the plan and review page.

    What depends on what sets the order. The scene and text models and a first
    pass of the vision-language model need only thumbnails, so they start at
    once and side by side (while `scanning`, a still-running scan, writes
    those thumbnails). Rotation needs all of the first two; bursts, faces and
    the plan follow. The vision model must see each photo the way it will be
    turned, which is known last: its first pass assumes "as stored", and only
    the few photos the plan turns are asked again.
    """
    import numpy as np
    items = scan["items"]
    cand = [i for i, it in enumerate(items)
            if it["kind"] == "image" and not it["screenshot"] and not it.get("error")]
    names = [items[i]["name"] for i in cand]
    clock = Timer(work, "analyze")
    began = time.monotonic()
    judging = args.composition or args.receipts

    def timed(step, fn, *a):
        with clock.step(step):
            return fn(*a)

    def embeddings():
        cache = work / "embeddings.npz"
        cached = np.load(cache, allow_pickle=False) if cache.exists() else None
        if cached is not None and cached["names"].tolist() == names and str(cached["model"]) == args.model:
            return cached["feats"], cached["regions"], False
        return *embed(work, [items[i] for i in cand], args.model, args.batch), True

    nothing = lambda: build_plan(items, {}, [], args.blur_below, args.rotate_confidence)

    def decide():
        nonlocal cand, names
        if not cand:  # only screenshots, videos or unreadable files: no model has anything to look at
            if scanning:
                scanning.result()
            return nothing()
        with ThreadPoolExecutor(3) as side:
            first_look = side.submit(timed, "composition", composition_scores, work, [items[i] for i in cand], None,
                                     args.composition_model) if judging else None
            reading = side.submit(timed, "text", text_scores, work, [items[i] for i in cand], args.text_languages.split(","),
                                  None, TEXT_WORKERS_SHARING if judging else TEXT_WORKERS_PER_GPU) \
                if args.text_direction else None
            feats, regions, fresh = timed("embed", embeddings)
            text = {}
            if reading:
                try:
                    by_name = reading.result()
                    text = {i: by_name[items[i]["name"]] for i in cand}
                except ImportError:
                    print("[analyze] easyocr is not installed: text direction is not checked", file=sys.stderr)
                    log_event(work, "analyze", "text_direction_skipped", why="easyocr is not installed")
            if scanning:
                scanning.result()  # every image has been opened: sharpness, faces and unreadable photos are known
                ok = [k for k, i in enumerate(cand) if not items[i].get("error")]
                cand, names, feats, regions = [cand[k] for k in ok], [names[k] for k in ok], feats[ok], regions[ok]
                if not cand:
                    return nothing()
            if fresh:
                np.savez(work / "embeddings.npz", feats=feats, regions=regions, names=np.array(names), model=args.model)

            times = np.array([items[i]["t"] if items[i]["t"] is not None else 0.0 for i in cand])
            order = np.argsort(times, kind="stable")
            with clock.step("scene rotation"):
                probs = predict_rotation(feats, order)
            rot_probs = {cand[k]: probs[k].tolist() for k in range(len(cand))}

            timed_shots = [k for k in order if items[cand[k]]["t"] is not None]
            with clock.step("bursts"):
                groups = group_bursts(times[timed_shots], regions[timed_shots].astype(np.float32),
                                      args.burst_seconds, args.burst_similarity)
            bursts = [[cand[timed_shots[j]] for j in g] for g in groups]

            faces = {}
            if args.faces and bursts:
                try:
                    import mediapipe  # noqa: F401
                    members = [items[i]["name"] for g in bursts for i in g]
                    with clock.step("faces"):  # read at full resolution, straight from the source photos
                        by_name = face_scores(work, {n: str(Path(scan["src"]) / n) for n in members}, args.jobs)
                    faces = {i: by_name[items[i]["name"]] for g in bursts for i in g}
                    day = lambda g: int((items[g[0]]["t"] or 0) // 86400)
                    people = mark_familiar([[faces[i] for i in g] for g in bursts], [day(g) for g in bursts])
                    print(f"[analyze] {people} people appear on {FAMILIAR_DAYS} or more days; "
                          + ("only their faces are judged" if people
                             else "too few to tell strangers apart, so every face is judged"), file=sys.stderr)
                    log_event(work, "analyze", "familiar_people", people=people, min_days=FAMILIAR_DAYS)
                except ImportError:
                    print("[analyze] mediapipe is not installed: faces are not checked", file=sys.stderr)
                    log_event(work, "analyze", "faces_skipped", why="mediapipe is not installed")

            plan = build_plan(items, rot_probs, bursts, args.blur_below, args.rotate_confidence, text, faces)
            if judging:
                try:
                    first_look.result()
                    with clock.step("composition, turned photos"):
                        scored = composition_scores(work, [items[i] for i in cand],
                                                    {n: p["rotate"] for n, p in plan.items()}, args.composition_model)
                    apply_composition(plan, scored, args.accident_confidence if args.composition else None,
                                      args.receipt_confidence if args.receipts else None)
                except ImportError as e:
                    print(f"[analyze] accidental shots and receipts are not checked: {e}", file=sys.stderr)
                    log_event(work, "analyze", "composition_skipped", why=str(e))
        return plan

    plan = decide()
    params = {k: v for k, v in vars(args).items() if k not in ("func", "work", "cmd", "src")}
    full = {}
    if args.full_previews:
        with clock.step("previews"):
            full = make_previews(scan["src"], work, items,
                                 [n for n in review_names(plan) if plan[n]["reason"] != "screenshot"], args.jobs)
    with clock.step("outputs"):
        summary = write_outputs(work, items, plan, params, full)
    clock.seconds["total"] = round(time.monotonic() - began, 1)  # steps overlap, so they add up to more than this
    clock.report()
    print(f"[analyze] {summary}")
    print(f"[analyze] review page: {work / 'review.html'}")


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    def analysis_options(p):
        p.add_argument("--model", default="facebook/dinov2-base")
        p.add_argument("--batch", type=int, default=64)
        p.add_argument("--blur-below", type=float, default=BLUR_BELOW,
                       help="sharpness under this is blurry (default %(default)s)")
        p.add_argument("--rotate-confidence", type=float, default=ROTATE_CONFIDENCE,
                       help="rotate only above this confidence (default %(default)s)")
        p.add_argument("--burst-seconds", type=float, default=BURST_SECONDS,
                       help="max seconds between shots of one burst (default %(default)s)")
        p.add_argument("--burst-similarity", type=float, default=BURST_SIMILARITY,
                       help="min similarity of the least similar image region within a burst (default %(default)s)")
        p.add_argument("--no-text-direction", dest="text_direction", action="store_false",
                       help="do not read text to decide which way up signs and documents are")
        p.add_argument("--text-languages", default="en",
                       help="comma-separated EasyOCR language codes of the text on signs (default: en)")
        p.add_argument("--no-faces", dest="faces", action="store_false",
                       help="do not look at faces; keep every frame of a burst")
        p.add_argument("--no-composition", dest="composition", action="store_false",
                       help="do not ask a vision-language model which photos were taken by accident")
        p.add_argument("--no-receipts", dest="receipts", action="store_false",
                       help="keep photos of receipts")
        p.add_argument("--receipt-confidence", type=float, default=RECEIPT_CONFIDENCE,
                       help="leave a photo behind as a receipt above this probability (default %(default)s)")
        p.add_argument("--composition-model", default=COMPOSITION_MODEL,
                       help="Hugging Face vision-language model that judges accidental shots and receipts "
                            "(default %(default)s)")
        p.add_argument("--accident-confidence", type=float, default=ACCIDENT_CONFIDENCE,
                       help="leave a photo behind as accidental above this probability (default %(default)s)")
        p.add_argument("--no-full-previews", dest="full_previews", action="store_false",
                       help="skip the full-resolution previews of photos under review (saves disk and time)")
        p.add_argument("--jobs", type=int, default=os.cpu_count())

    p = sub.add_parser("run", help="scan and analyze in one pass (fastest): ends with review.html")
    p.add_argument("src")
    p.add_argument("work")
    analysis_options(p)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("scan", help="metadata, thumbnails, sharpness, faces")
    p.add_argument("src")
    p.add_argument("work")
    p.add_argument("--jobs", type=int, default=os.cpu_count())
    p.add_argument("--no-faces", dest="faces", action="store_false", help="do not look at faces")
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser("analyze", help="decide; write plan.json, review.html and the log")
    p.add_argument("work")
    analysis_options(p)
    p.set_defaults(func=cmd_analyze)
    p = sub.add_parser("apply", help="copy keepers to OUT (dry run without --apply)")
    p.add_argument("src")
    p.add_argument("work")
    p.add_argument("out")
    p.add_argument("--overrides", help="overrides.json downloaded from review.html")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--feedback-dir", help="where to save what the review changed "
                   "(default: $PHOTO_CULL_FEEDBACK or ~/.local/share/photo-cull/feedback)")
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser("feedback", help="how often each kind of proposal was accepted in past reviews")
    p.add_argument("--feedback-dir")
    p.set_defaults(func=cmd_feedback)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
