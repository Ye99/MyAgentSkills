---
name: markdown-topic-normalizer
description: Normalize escaped topic markers in notes. Convert literal \# and leading * item markers into proper Markdown headings, while preserving fenced code blocks and real lists.
---

# Markdown Topic Normalizer

## Purpose

Standardize notes that use literal topic markers like `\# ...` and leading `*...` / `* ...` as pseudo-headings.

## When To Use

- A note uses `\#` to denote a topic start.
- A note uses leading `*`/`\*` as item-level topic markers (not intended as list bullets).

## Core Rules

1. Never edit inside fenced code blocks
- Skip all lines between opening and closing fences (` ``` ` or ` ~~~ `).

2. Normalize escaped hash topic markers
- `^\\#\s*(.+)$` -> heading (`##` or `###` based on file style)
- `^\\#\s*$` -> blank line
- `^(#{1,6})\s+\\#\s*(.+)$` -> keep same heading level, remove escaped hash

3. Normalize leading star topic markers (file-level decision)
- If file clearly uses star-prefixed topic items:
  - `^\*+\s*(.+)$` -> heading (`###` by default)
  - `^\\\*+\s*(.+)$` -> heading (`###` by default)
  - Empty marker-only lines -> blank line
- Do not convert normal bullet lists in files where `*` is used as actual list syntax.

4. Handle wrapped marker variants
- If content appears as italic-wrapped markers (e.g. `*\# title*`), strip wrappers and normalize.

## Heading Level Heuristic

- Prefer `##` if the note already uses `##` for top-level topics.
- Otherwise use `###` to avoid flattening document structure.

## Verification

After conversion, confirm no marker remnants outside code fences:

- no literal escaped hash topics remain: `^\\#`
- no escaped hash directly under headings: `^#{1,6}\s+\\#`
- no leftover star markers (if star conversion was intended): `^\\?\*\s*\S`

Also spot-check beginning + a middle section for readability.

## Safety

- Preserve original prose content; only change marker syntax.
- Avoid broad global replacements without line-anchored patterns.
- Keep changes minimal and deterministic.
