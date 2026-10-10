#!/usr/bin/env python3
"""Manifest of selected paths under a snapshot root: metadata + SHA-256 of regular files.

usage: sudo python3 -I manifest.py <root> <paths.nul> <out.tsv> [threads]
<paths.nul> holds NUL-separated paths relative to <root>. Missing paths are recorded as MISSING.
Output: one tab-separated line per path, sorted, with %-escaped path:
  path  type  mode  uid  gid  size  mtime_ns  link_target  xattrs_sha256  content_sha256
"""
import hashlib, os, stat, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor

root, listfile, out = sys.argv[1], sys.argv[2], sys.argv[3]
threads = int(sys.argv[4]) if len(sys.argv) > 4 else 4
rels = [p for p in open(listfile, 'rb').read().split(b'\0') if p]

def xattr_digest(path):
    h = hashlib.sha256()
    try:
        names = sorted(os.listxattr(path, follow_symlinks=False))
    except OSError:
        return '-'
    for n in names:
        h.update(n.encode('utf-8', 'surrogateescape') + b'\0')
        h.update(os.getxattr(path, n, follow_symlinks=False) + b'\0')
    return h.hexdigest() if names else '-'

def one(rel):
    path = os.path.join(os.fsencode(root), rel)
    q = urllib.parse.quote_from_bytes(rel, safe='/')
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return f'{q}\tMISSING'
    t = stat.S_IFMT(st.st_mode)
    kind = {stat.S_IFREG: 'f', stat.S_IFDIR: 'd', stat.S_IFLNK: 'l'}.get(t, 'o')
    link = urllib.parse.quote_from_bytes(os.readlink(path)) if kind == 'l' else '-'
    digest = '-'
    if kind == 'f':
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while chunk := f.read(8 << 20):
                h.update(chunk)
        digest = h.hexdigest()
    size = st.st_size if kind != 'd' else '-'   # directory sizes differ legitimately (ZAP layout)
    return '\t'.join(map(str, (q, kind, oct(stat.S_IMODE(st.st_mode)), st.st_uid, st.st_gid,
                               size, st.st_mtime_ns, link, xattr_digest(path), digest)))

with ThreadPoolExecutor(threads) as ex:
    lines = list(ex.map(one, rels))
with open(out, 'w') as f:
    f.write('\n'.join(sorted(lines)) + '\n')
print(f'{len(lines)} entries -> {out}', file=sys.stderr)
