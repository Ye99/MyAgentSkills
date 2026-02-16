---
name: convert-external-images
description: Convert external image files (Obsidian-style ![[image.png]] or standard ![](image.png)) to embedded base64 data URLs in markdown. Uses reference-style syntax for clean main text with base64 data at file end. Supports PNG, JPG, JPEG, GIF, WEBP. Includes verification, and deletion of original files (default).
---

# Convert External Images to Embedded Base64

Convert external image files referenced in markdown to embedded base64 data URLs for portability while maintaining readability.

## When to Use

- Converting Obsidian vault notes to portable markdown
- Removing external image dependencies
- Creating self-contained markdown documents
- Migrating between note-taking systems

## Quick Start

```bash
cd /home/ye/p/MyAgentSkills/convert-external-images/scripts
./convert_images.sh /path/to/markdown/file.md
```

## Process Overview

1. **Detection** - Finds Obsidian-style `![[image.png]]` references
2. **Conversion** - Converts images to base64, uses reference-style syntax
3. **Placement** - Puts base64 definitions at end of file for readability
4. **Verification** - Confirms all conversions succeeded
5. **Cleanup** - Deletion of original image files (default, use --no-delete to skip)

## Output Format

**In main text** (clean and readable):
```markdown
![Descriptive alt text][embedded-image-1]
```

**At end of file** (base64 data):
```markdown
[embedded-image-1]: <data:image/png;base64,iVBORw0KGgo...>
```

## Features

- ✓ Automatic MIME type detection (png, jpg, jpeg, gif, webp)
- ✓ Reference-style syntax keeps main text clean
- ✓ Base64 data at file end for readability
- ✓ Verification step ensures success
- ✓ Deletes original files by default (skip with --no-delete)
- ✓ Handles missing image files gracefully
- ✓ Obsidian-compatible reference syntax

## Script Usage

See `scripts/convert_images.sh --help` for detailed options.

## Benefits Over Inline Base64

**Traditional inline**: 
```markdown
![image](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...)
```
Makes text unreadable with giant base64 strings.

**This approach**:
- Main text stays clean and readable
- All base64 data relegated to file end
- Standard markdown reference syntax
- Works in Obsidian, VS Code, GitHub, etc.

## Technical Details

- Uses `base64 -w 0` for single-line encoding
- Python script handles file I/O and regex replacements
- Sanitizes filenames to create clean reference IDs
- Preserves original file permissions
- Cross-platform compatible (Linux/macOS)
