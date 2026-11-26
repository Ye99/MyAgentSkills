#!/usr/bin/env python3
"""Write a verified split (split_note.py output) into a vault through the Obsidian CLI, then verify.

usage: obsidian_write.py --vault VAULT_DIR --vault-name NAME --out OUT_DIR [--dry-run]

Requires Obsidian to be running. Files are written with `obsidian eval` using the vault API
(app.vault.create / modify / append) and base64 chunks cut at line boundaries, because
`obsidian create content=...` turns a literal backslash-n into a newline and cannot express it,
and one command-line argument is capped near 128 KB.

Order: new topic notes -> rewritten other notes -> the index (replacing the source) last,
so an interrupted run never leaves the source replaced while topic notes are missing.
Each file is staged in Obsidian's memory and written in one call, so no note is ever left
half-written. Originals of every replaced note are copied to OUT_DIR/backup first, and each
is checked again just before it is replaced, so an edit made meanwhile is never overwritten.

Checks (exit 1 on failure):
  pre   every vault file the splitter read still has the sha256 recorded in report.json;
        no topic note already exists
  E. bytes    each written file on disk is byte-identical to its verified copy in OUT_DIR
  F. links    Obsidian's own metadata cache resolves every heading link in the written files
  G. unresolved  `obsidian unresolved` gains no new link targets
"""
import argparse, base64, hashlib, json, pathlib, subprocess, sys, time

CHUNK = 60000


def ob(name, *args, timeout=120):
    r = subprocess.run(["obsidian", f"vault={name}", *args], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=timeout)
    return (r.stdout or "") + (r.stderr or "")


def eval_js(name, code):
    out = ob(name, "eval", f"code={code}").strip().splitlines()
    return out[-1] if out else ""


def chunks(data):
    parts, cur = [], b""
    for line in data.splitlines(keepends=True):
        if cur and len(cur) + len(line) > CHUNK:
            parts.append(cur); cur = b""
        cur += line
    if cur or not parts:
        parts.append(cur)
    return parts


def write_file(name, vault_path, data, mode):
    """Stage every chunk in Obsidian's memory, then write the whole file in one call,
    so a failed or interrupted transfer never leaves a half-written note on disk."""
    jp = json.dumps(vault_path)
    buf = f"globalThis.__noteSplitBuf[{jp}]"
    for k, part in enumerate(chunks(data)):
        b64 = base64.b64encode(part).decode()
        dec = f"new TextDecoder().decode(Uint8Array.from(atob('{b64}'),c=>c.charCodeAt(0)))"
        start = "globalThis.__noteSplitBuf=globalThis.__noteSplitBuf||{};" + (f"{buf}='';" if k == 0 else "")
        res = eval_js(name, f"(async()=>{{{start}{buf}+={dec};return 'ok'}})()")
        if res != "=> ok":
            eval_js(name, f"(async()=>{{if(globalThis.__noteSplitBuf)delete {buf};return 'ok'}})()")
            return f"chunk {k} failed: {res[:200]}"
    if mode == "create":
        body = f"await app.vault.create({jp},{buf})"
    else:
        body = f"const f=app.vault.getAbstractFileByPath({jp});await app.vault.modify(f,{buf})"
    res = eval_js(name, f"(async()=>{{try{{{body}}}finally{{delete {buf}}};return 'ok'}})()")
    if res != "=> ok":
        return f"write failed: {res[:200]}"
    return None


def unresolved(name):
    rows = set()
    for line in ob(name, "unresolved", "verbose", "format=tsv").splitlines():
        if "\t" in line:
            rows.add(line.split("\t")[0])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True)
    ap.add_argument("--vault-name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    vault, out, name = pathlib.Path(a.vault), pathlib.Path(a.out), a.vault_name
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    if report.get("errors") or report.get("stage") != "done":
        print("ABORT: report.json shows a failed or incomplete split; rerun split_note.py first"); return 1
    problems = []

    for rel, sha in report["read_sha256"].items():
        p = vault / rel
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            problems.append(f"vault file changed since the split was computed: {rel}")
    src = pathlib.PurePosixPath(report["source"])
    topic = [(f"{(src.parent / n).as_posix()}.md", out / "notes" / src.parent / f"{n}.md") for n in report["notes"]]
    for vp, _ in topic:
        if (vault / vp).exists():
            problems.append(f"topic note already exists: {vp}")
    rewrites = [(r, out / "rewrites" / r) for r in report["rewrites"]]
    index = (src.as_posix(), out / "notes" / src)
    if problems:
        print("ABORT:"); [print(" -", p) for p in problems]; return 1
    plan = [(vp, lp, "create") for vp, lp in topic] + [(vp, lp, "modify") for vp, lp in rewrites] + [(*index, "modify")]
    for vp, lp, mode in plan:
        print(f"  {mode:6} {vp} ({lp.stat().st_size} bytes)")
    if a.dry_run:
        print("dry run: nothing written"); return 0

    # Keep the originals of every note about to be replaced, outside the vault.
    backup = out / "backup"
    for vp, _, mode in plan:
        if mode != "modify":
            continue
        original = (vault / vp).read_bytes()
        (backup / vp).parent.mkdir(parents=True, exist_ok=True)
        (backup / vp).write_bytes(original)
    print(f"originals backed up to {backup} (restore: copy back over the vault paths)")

    before = unresolved(name)
    print(f"unresolved link targets before: {len(before)}")
    for vp, lp, mode in plan:
        data = lp.read_bytes()
        if mode == "modify":  # checked again right before: the user may be editing in Obsidian
            now = (vault / vp).read_bytes()
            if hashlib.sha256(now).hexdigest() != report["read_sha256"].get(vp):
                print(f"ABORT: {vp} changed while writing; not overwritten. Notes written so far are "
                      f"listed above; originals are in {backup}"); return 1
        elif (vault / vp).exists():
            print(f"ABORT: {vp} appeared while writing; not overwritten"); return 1
        e = write_file(name, vp, data, mode)
        if e:
            print(f"ABORT while writing {vp}: {e}"); return 1
        time.sleep(0.3)
        if (vault / vp).read_bytes() != data:                     # E. bytes
            print(f"ABORT: {vp} on disk differs from the verified copy"); return 1
        print(f"  byte-exact: {vp}")

    time.sleep(3)
    paths = json.dumps([vp for vp, _, _ in plan])
    js = ("(async()=>{const P=" + paths + ";const bad=[];let n=0;for(const p of P){const f=app.vault.getAbstractFileByPath(p);"
          "const c=app.metadataCache.getFileCache(f)||{};for(const l of (c.links||[])){const i=l.link.indexOf('#');if(i<0)continue;"
          "const lp=l.link.slice(0,i);const sub=l.link.slice(i+1).split('#');const d=lp?app.metadataCache.getFirstLinkpathDest(lp,p):f;"
          "if(!d||d.extension!=='md')continue;n++;const hs=((app.metadataCache.getFileCache(d)||{}).headings||[]).map(h=>h.heading);"
          "if(!hs.includes(sub[sub.length-1]))bad.push(p+' -> '+l.original);}}return JSON.stringify({n,bad})})()")
    res = eval_js(name, js)
    try:
        payload = res[3:] if res.startswith("=> ") else res
        data = json.loads(payload)
        data = json.loads(data) if isinstance(data, str) else data
        print(f"heading links resolved by Obsidian: {data['n'] - len(data['bad'])}/{data['n']}")
        problems += [f"Obsidian cannot resolve heading link: {b}" for b in data["bad"]]   # F. links
    except (ValueError, KeyError):
        problems.append(f"could not read link check result: {res[:200]}")

    after = unresolved(name)
    new = sorted(after - before)
    print(f"unresolved link targets after: {len(after)}; new: {len(new)}")
    problems += [f"new unresolved link target: {t}" for t in new]             # G. unresolved
    if problems:
        print("POST-WRITE CHECK FAILED:"); [print(" -", p) for p in problems]; return 1
    print("POST-WRITE CHECKS PASSED: bytes, Obsidian heading links, unresolved links")
    return 0


if __name__ == "__main__":
    sys.exit(main())
