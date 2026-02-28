#!/bin/bash
# Convert external image files referenced in markdown to embedded base64 images
# Usage: ./convert_images.sh <markdown_file> [--no-delete]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Help text
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat << 'EOF'
Convert External Images to Embedded Base64 in Markdown

USAGE:
    ./convert_images.sh <markdown_file> [--no-delete]

ARGUMENTS:
    <markdown_file>    Path to the markdown file to process

OPTIONS:
    --no-delete       Skip deletion step (keep original image files)
    -h, --help        Show this help message

DESCRIPTION:
    Converts local image references in Obsidian syntax (![[image.png]])
    and standard markdown syntax (![](image.png)) to embedded base64
    data URLs using reference-style syntax.

EXAMPLES:
    # Convert images and delete originals
    ./convert_images.sh ~/notes/document.md

    # Convert images but keep originals
    ./convert_images.sh ~/notes/document.md --no-delete

PROCESS:
    1. Finds Obsidian-style and standard markdown image references
    2. Converts images to base64
    3. Updates markdown with reference-style links
    4. Places base64 definitions at end of file
    5. Verifies conversion
    6. Deletes original files (unless --no-delete is used)

OUTPUT FORMAT:
    Main text:       ![Description][embedded-image-1]
    End of file:     [embedded-image-1]: <data:image/png;base64,...>
EOF
    exit 0
fi

# Check arguments
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: ./convert_images.sh <markdown_file> [--no-delete]" >&2
    exit 2
fi

MARKDOWN_FILE="$1"
NO_DELETE=false
CONVERTED_FILES="${MARKDOWN_FILE}.converted_files"

if [ "${2:-}" = "--no-delete" ]; then
    NO_DELETE=true
elif [ "$#" -eq 2 ]; then
    echo "Error: Unknown option: $2" >&2
    echo "Usage: ./convert_images.sh <markdown_file> [--no-delete]" >&2
    exit 2
fi

if [ ! -f "$MARKDOWN_FILE" ]; then
    echo "Error: File not found: $MARKDOWN_FILE"
    exit 1
fi

# Remove stale converted-files list from prior runs.
rm -f "$CONVERTED_FILES"

echo "Converting external images to embedded base64 in: $MARKDOWN_FILE"

# Python script to do the conversion
python3 - "$MARKDOWN_FILE" "$CONVERTED_FILES" << 'PYEOF'
import sys
import os
import re
import base64

markdown_file = sys.argv[1]
converted_files_path = sys.argv[2]
markdown_dir = os.path.dirname(os.path.abspath(markdown_file))

# Read the markdown file
with open(markdown_file, 'r') as f:
    content = f.read()

obsidian_pattern = re.compile(
    r'!\[\[(?P<path>[^\]\n]+?\.(?:png|jpg|jpeg|gif|webp))\]\]',
    re.IGNORECASE,
)
markdown_pattern = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\((?P<path>(?!https?://|data:|#)[^)\s]+?\.(?:png|jpg|jpeg|gif|webp))\)',
    re.IGNORECASE,
)

converted = {}
converted_images = []

def build_alt_text(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return name.replace('_', ' ').replace('-', ' ')

def register_image(path):
    if path in converted:
        return converted[path]

    image_path = os.path.join(markdown_dir, path)
    if not os.path.exists(image_path):
        print(f"⚠️  Warning: Image file not found: {image_path}")
        return None

    ext = os.path.splitext(path)[1].lower().lstrip('.')
    image_type = 'jpeg' if ext == 'jpg' else ext

    with open(image_path, 'rb') as img_file:
        base64_data = base64.b64encode(img_file.read()).decode('utf-8')

    ref_id = f"embedded-image-{len(converted) + 1}"
    definition = f"[{ref_id}]: <data:image/{image_type};base64,{base64_data}>"
    converted[path] = {
        'ref_id': ref_id,
        'definition': definition,
        'image_path': image_path,
    }
    converted_images.append(image_path)
    print(f"✓ Converted: {path} -> {ref_id}")
    return converted[path]

def replace_obsidian(match):
    path = match.group('path')
    item = register_image(path)
    if item is None:
        return match.group(0)
    alt_text = build_alt_text(path)
    return f"![{alt_text}][{item['ref_id']}]"

def replace_markdown(match):
    path = match.group('path')
    item = register_image(path)
    if item is None:
        return match.group(0)
    alt_text = match.group('alt').strip() or build_alt_text(path)
    return f"![{alt_text}][{item['ref_id']}]"

content = obsidian_pattern.sub(replace_obsidian, content)
content = markdown_pattern.sub(replace_markdown, content)

if not converted:
    print("No supported local image references found to convert.")
    sys.exit(0)

print(f"Found {len(converted)} unique image(s) to convert")

image_definitions = [item['definition'] for item in converted.values()]

# Append all image definitions at the very end of file
if image_definitions:
    content = content.rstrip() + '\n\n' + '\n\n'.join(image_definitions) + '\n'

# Write back to file
with open(markdown_file, 'w') as f:
    f.write(content)

print(f"\n✅ Converted {len(converted_images)} image(s) to embedded base64")

# Store list of converted files for deletion step
with open(converted_files_path, 'w') as f:
    for filepath in converted_images:
        f.write(f"{filepath}\n")

PYEOF

# Verify the conversion was successful
echo ""
echo "Verifying conversion..."

python3 - "$MARKDOWN_FILE" << 'PYEOF'
import sys
import re

markdown_file = sys.argv[1]

with open(markdown_file, 'r') as f:
    content = f.read()

remaining_obsidian = re.findall(r'!\[\[(.*?\.(png|jpg|jpeg|gif|webp))\]\]', content, re.IGNORECASE)
remaining_markdown = re.findall(
    r'!\[[^\]]*\]\(((?!https?://|data:|#)[^)\s]+?\.(?:png|jpg|jpeg|gif|webp))\)',
    content,
    re.IGNORECASE,
)

if remaining_obsidian:
    print(f"⚠️  Warning: {len(remaining_obsidian)} Obsidian-style image(s) still remain (files may not exist)")
    for img in remaining_obsidian:
        print(f"   - {img[0]}")
else:
    print("✓ No Obsidian-style image references remain")

if remaining_markdown:
    print(f"⚠️  Warning: {len(remaining_markdown)} standard markdown image(s) still remain (files may not exist)")
    for img in remaining_markdown:
        print(f"   - {img}")
else:
    print("✓ No standard markdown image references remain")

# Check that definitions were added
embedded_count = len(re.findall(r'\[embedded-image-\d+\]: <data:image/', content))
print(f"✓ Found {embedded_count} embedded image definition(s) at end of file")

PYEOF

# Handle deletion
if [ "$NO_DELETE" = true ]; then
    echo ""
    echo "Skipping deletion (--no-delete flag set)"
    if [ -f "$CONVERTED_FILES" ]; then
        echo "Original files kept. List saved in: ${CONVERTED_FILES}"
    else
        echo "No files were converted in this run."
    fi
    exit 0
fi

echo ""
echo "Conversion complete! Deleting original files..."
if [ -f "$CONVERTED_FILES" ]; then
    while IFS= read -r filepath; do
        if [ -f "$filepath" ]; then
            rm "$filepath"
            echo "✓ Deleted: $filepath"
        fi
    done < "$CONVERTED_FILES"
    rm "$CONVERTED_FILES"
    echo "✅ All original image files deleted"
else
    echo "No files to delete."
fi

echo ""
echo "Done!"
