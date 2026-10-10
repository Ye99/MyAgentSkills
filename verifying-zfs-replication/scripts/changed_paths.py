#!/usr/bin/env python3
"""Turn `zfs diff -FH <ds>@<from> <ds>@<to>` output into NUL-separated relative path lists.

usage: sudo zfs diff -FH <ds>@<from> <ds>@<to> | python3 -I changed_paths.py <mountpoint> <outdir>
Writes <outdir>/present.nul (added, modified, renamed-to: must match on the target) and
<outdir>/deleted.nul (removed or renamed-from, and not present again: must be absent on the target).
zfs diff escapes every space or non-printable byte as a backslash plus four octal digits (\\0040 = space).
"""
import os, re, sys

mount = os.fsencode(sys.argv[1].rstrip('/')) + b'/'
outdir = sys.argv[2]

def rel(field):
    path = re.sub(rb'\\0([0-7]{3})', lambda m: bytes([int(m.group(1), 8)]), field)
    if path + b'/' == mount:
        return None                       # the dataset root itself
    if b'\\' in path:
        sys.exit(f'undecoded escape: {field!r}')
    if not path.startswith(mount):
        sys.exit(f'{path!r} is outside {mount!r}: wrong mountpoint or a child dataset')
    return path[len(mount):]

present, gone = set(), set()
for line in sys.stdin.buffer.read().split(b'\n'):
    if not line:
        continue
    f = line.split(b'\t')                  # change, type, path[, new path]
    old = rel(f[2])
    if f[0] == b'R':
        gone.add(old)
        present.add(rel(f[3]))
    elif f[0] == b'-':
        gone.add(old)
    else:                                  # '+' or 'M'
        present.add(old)
present.discard(None)
gone = gone - present - {None}

os.makedirs(outdir, exist_ok=True)
for name, items in (('present.nul', present), ('deleted.nul', gone)):
    with open(os.path.join(outdir, name), 'wb') as fh:
        fh.write(b''.join(p + b'\0' for p in sorted(items)))
print(f'present {len(present)}  deleted {len(gone)}', file=sys.stderr)
