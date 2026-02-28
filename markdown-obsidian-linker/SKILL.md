---
name: markdown-obsidian-linker
description: Use when writing, editing, or auditing markdown for Obsidian where internal links like `[[#Header]]` must work reliably.
---

# Markdown Obsidian Linker

This skill enforces clean markdown headers to ensure compatibility with Obsidian internal linking and other markdown parsers.

## Instructions

When writing or auditing markdown content:

1.  **Scan directories recursively**: When a directory path is provided, use the `glob` tool with the pattern `**/*.md` to locate all markdown files within the target folder and its subdirectories.

2.  **Keep headers clean**: Do not include links or markdown formatting inside the header itself.
    *   **Bad**: `### [Header](url)`
    *   **Good**: `### Header`

3.  **Place links in content**: Put external links in the body text immediately following the header.

4.  **Reason**: Links inside headers break the anchor generation for internal linking in Obsidian (e.g., `[[#Header]]`) and many markdown parsers.

## Examples

### Correct Pattern

```markdown
### My Tool

[My Tool](http://url) - Description of the tool...
```

### Incorrect Pattern

```markdown
### [My Tool](http://url)
```

## Quick Fix

If you encounter a header with a link, refactor it immediately. When processing a folder, apply this to every `.md` file found:

**Target Folder:** `fix-links ./docs`

**File Search (using glob):** `glob(pattern="**/*.md", path="./docs")`

**Before:**
```markdown
## [Project X](https://github.com/org/project-x)
```

**After:**
```markdown
## Project X

[Project X](https://github.com/org/project-x)
```
