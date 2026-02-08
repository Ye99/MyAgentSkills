---
name: format-markdownfile-code-block
description: Normalize Markdown notes so command/code lines are easy to read. Use fenced code blocks for multi-line command sequences and inline code for single-line commands while preserving existing non-shell code fences.
---

# Format Markdown File Code Block

## Purpose

Make technical Markdown notes easier to scan by formatting command-like content consistently:

- Multi-line command sequences -> fenced `bash` code blocks
- Single command lines -> inline code

## Scope

Use this skill when editing `.md` notes that contain shell/CLI commands mixed with prose.

## Formatting Rules

1. Preserve existing non-shell fenced blocks
- Do not alter fences such as `mermaid`, `json`, `yaml`, `python`, `typescript`, etc.

2. Rebuild shell-like formatting safely
- If needed, remove and rebuild only removable shell/text fences (`bash`, `sh`, `shell`, `zsh`, `fish`, `text`, or empty language) to get consistent output.

3. Multi-line sequences -> fenced block
- Group contiguous command/continuation lines into:

```bash
<command lines>
```

4. Single command lines -> inline code
- Convert a single standalone command-like line to:

`command ...`

5. Never format inside fenced blocks
- Skip all transformations while inside any existing fenced block.

## Command Detection Heuristics

Treat a line as command-like when it matches one or more:

- Starts with known CLI verbs (`git`, `ssh`, `curl`, `docker`, `kubectl`, `python`, `npm`, `yarn`, `pnpm`, `brew`, `systemctl`, etc.)
- Starts with prompt form (`$ ...`)
- Looks like env assignment (`KEY=value`)
- Looks like shell function/brace snippets (`name() {`, `{`, `}`)
- Uses shell operators (`|`, `&&`, `||`, `>`, `<`, `;`) or explicit continuation (`\\`)

## Heading Handling

Some notes store commands as headings (for example `### curl ...` or `### -H ...`).

- If a heading body is clearly command-like, demote it to a normal command line before block formatting.
- Keep true semantic headings unchanged.

## Continuation Handling

While inside a command sequence, include continuation lines such as:

- option lines (`--foo`, `-H ...`, including escaped variants like `\-H ...`)
- assignment lines
- lines ending with `\\`
- connector/operator lines

## Safety Guardrails

- Do not wrap prose paragraphs as code.
- Do not destroy Markdown links or note structure.
- Keep edits deterministic and minimal.
- Prefer one pass that is idempotent (running again should produce no further changes).

## Verification Checklist

After formatting:

- Non-shell fences (e.g., `mermaid`) still exist and are unchanged.
- Multi-line commands are fenced.
- Single command lines are inline-coded.
- No command lines were formatted inside existing code fences.
- Spot-check beginning, middle, and end of each modified file.

## Output Policy

- Keep original command text unchanged unless required to avoid formatting breakage.
- Use `bash` language tag for inserted fenced shell blocks.
