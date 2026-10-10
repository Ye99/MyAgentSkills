#!/usr/bin/env python3
"""Compare source and target manifests from manifest.py.

usage: python3 -I compare.py <src-present.tsv> <dst-present.tsv> <dst-deleted.tsv>
Exit 0 and print PASS only when every present path matches on all fields
(type, mode, uid, gid, size, mtime_ns, link target, xattrs, SHA-256) and every deleted path is MISSING.
"""
import sys

def load(p):
    rows = {}
    for line in open(p, encoding='utf-8'):
        f = line.rstrip('\n').split('\t')
        if f[0]:
            rows[f[0]] = f[1:]
    return rows

src, dst, dele = (load(p) for p in sys.argv[1:4])
fields = ['type', 'mode', 'uid', 'gid', 'size', 'mtime_ns', 'link', 'xattrs', 'sha256']
bad = []
for path, s in src.items():
    d = dst.get(path)
    if d is None:
        bad.append(f'NOT IN TARGET MANIFEST  {path}')
    elif d != s:
        diffs = [n for n, a, b in zip(fields, s, d + [''] * len(fields)) if a != b] or ['MISSING' if d == ['MISSING'] else 'shape']
        bad.append(f'DIFF {",".join(diffs)}  {path}')
extra = set(dst) - set(src)
bad += [f'EXTRA IN TARGET MANIFEST  {p}' for p in sorted(extra)]
bad += [f'DELETED BUT PRESENT  {p}' for p, d in dele.items() if d != ['MISSING']]
src_missing = [p for p, s in src.items() if s == ['MISSING']]

files = [s for s in src.values() if s and s[0] == 'f']
gb = sum(int(s[4]) for s in files) / 1e9
print(f'source entries {len(src)} ({len(files)} files, {gb:.1f} GB hashed); deleted paths checked {len(dele)}')
if src_missing:
    print(f'WARNING: {len(src_missing)} listed paths are missing on the SOURCE snapshot (list/mountpoint mismatch?)')
for b in bad[:50]:
    print(b)
if len(bad) > 50:
    print(f'... {len(bad) - 50} more')
if not src and not dele:
    print('NOTE: zfs diff listed no changes between the two snapshots; only the GUID check applies')
print('PASS' if not bad and not src_missing else f'FAIL: {len(bad)} problems')
sys.exit(0 if not bad and not src_missing else 1)
