#!/usr/bin/env python3
"""Build Google Maps browser steps for trip-day travel-time estimates and summarize results.

Workflow (see ../SKILL.md):
  resolve-actions legs.json OUTDIR   -> browser batches that resolve place queries to exact Maps places
  merge-places    legs.json places.json -> write resolved places back into legs.json
  leg-actions     legs.json OUTDIR   -> browser batches that open each leg with its departure time
  report          legs.json results.json -> Markdown table of parsed estimates
"""
import argparse
import calendar
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.parse

MODES = {"drive": 0, "bike": 1, "walk": 2, "transit": 3}

# Waits until the directions panel renders, then stores a one-line summary in localStorage.
LEG_FN = """(async(label)=>{ const sl=ms=>new Promise(r=>setTimeout(r,ms));
let t=''; for(let w=0;w<30;w++){ await sl(400); t=document.body.innerText; if(t.includes('Copy link') && /\\d+ min|\\d+ hr/.test(t.slice(t.indexOf('Copy link')))) break; }
await sl(800); t=document.body.innerText;
const i=t.indexOf('Copy link'); const head=t.slice(Math.max(0,i-160),i).replace(/\\n+/g,' | ');
const body=t.slice(i+9,i+260).replace(/\\n+/g,' | ');
const S=JSON.parse(localStorage.gmt_legs||'{}'); S[label]={title:document.title.replace(' - Google Maps',''),head,body}; localStorage.gmt_legs=JSON.stringify(S);
return label+' => '+S[label].title+' || '+body.slice(0,140); })"""

# Opens the first result if a search returns a list, then stores name, feature ID, coordinates and address.
RESOLVE_FN = """(async(key)=>{ const sl=ms=>new Promise(r=>setTimeout(r,ms)); const q=s=>document.querySelector(s);
for(let w=0;w<25 && !q('h1.DUwDvf') && !q('div[role=feed] a[href*="/maps/place/"]');w++) await sl(400);
if(!q('h1.DUwDvf')){ const a=q('div[role=feed] a[href*="/maps/place/"]'); if(a){ a.click(); for(let w=0;w<25 && !q('h1.DUwDvf');w++) await sl(400);} }
let id, lat, lng; for(let w=0;w<20;w++){ const u=decodeURIComponent(location.href); id=u.match(/!1s(0x[0-9a-f]+:0x[0-9a-f]+)/)?.[1]; const c=u.match(/!3d(-?[\\d.]+)!4d(-?[\\d.]+)/); if(id&&c){lat=+c[1];lng=+c[2];break;} await sl(400); }
let approx=false; if(!id && q('h1.DUwDvf')){ const c=decodeURIComponent(location.href).match(/@(-?[\\d.]+),(-?[\\d.]+)/); if(c){lat=+c[1];lng=+c[2];approx=true;} }
const rec={name:q('h1.DUwDvf')?.innerText||'', id, lat, lng, approx, address:(q('button[data-item-id="address"]')?.innerText||'').replace(/\\n/g,' ').trim()};
const S=JSON.parse(localStorage.gmt_places||'{}'); S[key]=rec; localStorage.gmt_places=JSON.stringify(S);
return key+' => '+rec.name+' | '+rec.address+' | '+(id||('map centre '+lat+','+lng)); })"""


def setup_step():
    """One step that stores both helpers in localStorage; batch steps then call them by name."""
    js = ("localStorage.gmt_resolve_fn=" + json.dumps(RESOLVE_FN) + "; localStorage.gmt_leg_fn="
          + json.dumps(LEG_FN) + "; 'helpers stored'")
    return {"name": "javascript_tool", "input": {"action": "javascript_exec", "text": js}}


def local_epoch(text):
    """Maps encodes the chosen local wall-clock time as if it were UTC seconds."""
    t = dt.datetime.fromisoformat(text)
    return calendar.timegm(t.timetuple())


def waypoint(p):
    if p.get("id"):
        return f"!1m5!1m1!1s{p['id']}!2m2!1d{p['lng']}!2d{p['lat']}"
    # Coordinates only: used when Maps opened the place without exposing its feature ID.
    return f"!1m3!2m2!1d{p['lng']}!2d{p['lat']}"


def leg_url(leg, places):
    a, b = places[leg["from"]], places[leg["to"]]
    for k, p in ((leg["from"], a), (leg["to"], b)):
        if p.get("lat") is None:
            sys.exit(f"place {k!r} is not resolved; run resolve-actions and merge-places first")
    mode = MODES[leg["mode"]]
    if "arrive" in leg:
        when = f"!2m3!6e1!7e2!8j{local_epoch(leg['arrive'])}"
    else:
        when = f"!2m3!6e0!7e2!8j{local_epoch(leg['depart'])}"
    # !4m<n> counts every nested element that follows it, so it depends on whether each stop has an ID.
    inner = f"{waypoint(a)}{waypoint(b)}{when}!3e{mode}"
    n = inner.count("!")
    return (f"https://www.google.com/maps/dir/{a['lat']},{a['lng']}/{b['lat']},{b['lng']}/"
            f"data=!4m{n + 1}!4m{n}{inner}")


def write_setup(outdir):
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "setup.json").write_text(json.dumps([setup_step()], ensure_ascii=False))


def write_batches(actions, outdir, per_batch, stem):
    out = pathlib.Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for n, i in enumerate(range(0, len(actions), per_batch * 2)):
        f = out / f"{stem}{n:02d}.json"
        f.write_text(json.dumps(actions[i:i + per_batch * 2], ensure_ascii=False))
        files.append(str(f))
    print("\n".join(files))


def cmd_resolve(args):
    data = json.loads(pathlib.Path(args.legs).read_text())
    actions = []
    for key, p in data["places"].items():
        if p.get("lat") is not None and not args.all:
            continue
        actions.append({"name": "navigate", "input": {
            "url": "https://www.google.com/maps/search/" + urllib.parse.quote(p["query"])}})
        actions.append({"name": "javascript_tool", "input": {"action": "javascript_exec", "text":
            f"await eval(localStorage.gmt_resolve_fn)({json.dumps(key, ensure_ascii=False)})"}})
    write_setup(args.outdir)
    write_batches(actions, args.outdir, args.per_batch, "resolve")


def cmd_merge(args):
    path = pathlib.Path(args.legs)
    data = json.loads(path.read_text())
    resolved = json.loads(pathlib.Path(args.places).read_text())
    for key, rec in resolved.items():
        if key in data["places"] and rec.get("lat") is not None:
            data["places"][key].update(rec)
    missing = [k for k, p in data["places"].items() if p.get("lat") is None]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print("unresolved:", ", ".join(missing) if missing else "none")


def cmd_legs(args):
    data = json.loads(pathlib.Path(args.legs).read_text())
    actions = []
    for leg in data["legs"]:
        actions.append({"name": "navigate", "input": {"url": leg_url(leg, data["places"])}})
        actions.append({"name": "javascript_tool", "input": {"action": "javascript_exec", "text":
            f"await eval(localStorage.gmt_leg_fn)({json.dumps(leg['label'], ensure_ascii=False)})"}})
    write_setup(args.outdir)
    write_batches(actions, args.outdir, args.per_batch, "legs")


def parse(mode, body):
    body = body.replace("–", "-")
    dist = re.search(r"(\d+(?:\.\d+)?) (km|m)\b", body)
    dist = f"{dist.group(1)} {dist.group(2)}" if dist else ""
    if mode == "transit":
        dur = re.search(r"((?:\d+ hr )?\d+ min)", body)
        span = re.search(r"(\d{1,2}:\d{2}\s?[AP]M)[^|]*?—(\d{1,2}:\d{2}\s?[AP]M)", body)
        fare = re.search(r"NT\$[\d.,]+", body)
        return (dur.group(1) if dur else "?",
                f"{span.group(1)}→{span.group(2)}" if span else "",
                fare.group(0) if fare else "")
    # Forms seen: "typically 14-35 min", "typically 3 min", "typically 28 min to 1 hr 5 min".
    typ = re.search(r"typically ((?:\d+ hr ?)?\d+(?: min)?(?:-| to )(?:\d+ hr ?)?(?:\d+ min)?|\d+ min)", body)
    dur = typ.group(1).strip() if typ else (re.search(r"((?:\d+ hr )?\d+ min)", body) or [None, "?"])[1]
    return dur, dist, ""


def cmd_report(args):
    data = json.loads(pathlib.Path(args.legs).read_text())
    res = json.loads(pathlib.Path(args.results).read_text())
    print("| Leg | Mode | Time | Estimate | Distance / schedule | Fare | Maps route |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for leg in data["legs"]:
        r = res.get(leg["label"])
        when = leg.get("depart") or ("arrive " + leg["arrive"])
        if not r:
            print(f"| {leg['label']} | {leg['mode']} | {when} | **not measured** | | | |")
            continue
        dur, extra, fare = parse(leg["mode"], r["body"])
        print(f"| {leg['label']} | {leg['mode']} | {when} | {dur} | {extra} | {fare} | {r['title']} |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(required=True)
    s = sub.add_parser("resolve-actions"); s.add_argument("legs"); s.add_argument("outdir")
    s.add_argument("--per-batch", type=int, default=6); s.add_argument("--all", action="store_true")
    s.set_defaults(f=cmd_resolve)
    s = sub.add_parser("merge-places"); s.add_argument("legs"); s.add_argument("places"); s.set_defaults(f=cmd_merge)
    s = sub.add_parser("leg-actions"); s.add_argument("legs"); s.add_argument("outdir")
    s.add_argument("--per-batch", type=int, default=6); s.set_defaults(f=cmd_legs)
    s = sub.add_parser("report"); s.add_argument("legs"); s.add_argument("results"); s.set_defaults(f=cmd_report)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
