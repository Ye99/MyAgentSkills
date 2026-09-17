#!/usr/bin/env python3
"""Print the chapter/section outline of an EPUB, in spine order.

    epub_outline.py BOOK.epub [--max-level N] [--doc ch03]

The outline is the ground truth for regrouping reading notes: chapter titles and
section names come from the book, not from memory. Standard library only.

The EPUB is only read; nothing is written or extracted to disk.
"""
import argparse
import posixpath
import re
import sys
import zipfile

TAG = re.compile(r'<h([1-6])\b[^>]*>(.*?)</h\1>', re.I | re.S)
STRIP = re.compile(r'<[^>]+>')
ENTITIES = {'&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'",
            '&nbsp;': ' ', '&rsquo;': '\u2019', '&mdash;': '\u2014'}


def text_of(fragment):
    s = STRIP.sub(' ', fragment)
    for k, v in ENTITIES.items():
        s = s.replace(k, v)
    s = re.sub(r'&#(\d+);', lambda m: chr(int(m.group(1))), s)
    return re.sub(r'\s+', ' ', s).strip()


def spine_documents(zf):
    """Return content document names in reading order."""
    container = zf.read('META-INF/container.xml').decode('utf-8', 'replace')
    opf_path = re.search(r'full-path="([^"]+)"', container).group(1)
    opf = zf.read(opf_path).decode('utf-8', 'replace')
    base = posixpath.dirname(opf_path)

    ids = {}
    for m in re.finditer(r'<item\b([^>]+)>', opf):
        attrs = m.group(1)
        i = re.search(r'id="([^"]+)"', attrs)
        h = re.search(r'href="([^"]+)"', attrs)
        if i and h:
            ids[i.group(1)] = h.group(1)

    order = re.findall(r'<itemref\b[^>]*?idref="([^"]+)"', opf)
    docs = []
    for ref in order:
        href = ids.get(ref)
        if href:
            docs.append(posixpath.normpath(posixpath.join(base, href)) if base else href)
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('epub')
    ap.add_argument('--max-level', type=int, default=6)
    ap.add_argument('--doc', help='only documents whose name contains this string')
    args = ap.parse_args()

    with zipfile.ZipFile(args.epub) as zf:
        names = set(zf.namelist())
        for doc in spine_documents(zf):
            if doc not in names:
                continue
            if args.doc and args.doc not in doc:
                continue
            html = zf.read(doc).decode('utf-8', 'replace')
            heads = [(int(l), text_of(t)) for l, t in TAG.findall(html)]
            heads = [(l, t) for l, t in heads if t and l <= args.max_level]
            if not heads:
                continue
            print(f'\n=== {doc} ===')
            for lvl, title in heads:
                print('  ' * (lvl - 1) + title)


if __name__ == '__main__':
    sys.exit(main())
