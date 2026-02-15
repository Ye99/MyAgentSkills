#!/bin/bash
# Convert external image files referenced in markdown to embedded base64 images
# Usage: ./convert_images.sh <markdown_file> [--no-delete]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Help text
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
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
    Converts Obsidian-style image references (![[image.png]]) to embedded
    base64 data URLs using reference-style syntax. Places base64 data at
    the end of the file for readability.

EXAMPLES:
    # Convert images and prompt for deletion
    ./convert_images.sh ~/notes/document.md

    # Convert images but keep originals
    ./convert_images.sh ~/notes/document.md --no-delete

PROCESS:
    1. Creates backup (.backup file)
    2. Finds Obsidian-style image references
    3. Converts images to base64
    4. Updates markdown with reference-style links
    5. Places base64 definitions at end of file
    6. Verifies conversion
    7. Optionally deletes original files

OUTPUT FORMAT:
    Main text:       ![Description][embedded-image-1]
    End of file:     [embedded-image-1]: <data:image/png;base64,...>
EOF
    exit 0
fi

# Check arguments
MARKDOWN_FILE="$1"
NO_DELETE=false

if [ "$2" = "--no-delete" ]; then
    NO_DELETE=true
fi

if [ ! -f "$MARKDOWN_FILE" ]; then
    echo "Error: File not found: $MARKDOWN_FILE"
    exit 1
fi

echo "Converting external images to embedded base64 in: $MARKDOWN_FILE"

# Create backup
cp "$MARKDOWN_FILE" "${MARKDOWN_FILE}.backup"
echo "✓ Backup created: ${MARKDOWN_FILE}.backup"

# Python script to do the conversion
python3 << 'PYEOF'
import sys
import os
import re
import base64

markdown_file = sys.argv[1]
markdown_dir = os.path.dirname(os.path.abspath(markdown_file))

# Read the markdown file
with open(markdown_file, 'r') as f:
    content = f.read()

# Find all Obsidian-style image references: ![[filename.png]]
obsidian_pattern = r'!\[\[(.*?\.(png|jpg|jpeg|gif|webp))\]\]'
matches = re.findall(obsidian_pattern, content, re.IGNORECASE)

if not matches:
    print("No Obsidian-style images found to convert.")
    sys.exit(0)

print(f"Found {len(matches)} image(s) to convert")

converted_images = []
image_definitions = []

for i, (filename, ext) in enumerate(matches, 1):
    # Construct full path to image file
    image_path = os.path.join(markdown_dir, filename)
    
    if not os.path.exists(image_path):
        print(f"⚠️  Warning: Image file not found: {image_path}")
        continue
    
    # Read and encode image to base64
    with open(image_path, 'rb') as img_file:
        image_data = img_file.read()
        base64_data = base64.b64encode(image_data).decode('utf-8')
    
    # Determine image type from extension
    image_type = ext.lower()
    if image_type == 'jpg':
        image_type = 'jpeg'
    
    # Create reference ID (sanitized filename)
    ref_id = f"embedded-image-{i}"
    
    # Create a descriptive alt text from filename
    alt_text = filename.replace(f'.{ext}', '').replace('_', ' ').replace('-', ' ')
    
    # Replace in content with reference-style link
    old_ref = f"![[{filename}]]"
    new_ref = f"![{alt_text}][{ref_id}]"
    content = content.replace(old_ref, new_ref)
    
    # Store the definition to add at end
    definition = f"[{ref_id}]: <data:image/{image_type};base64,{base64_data}>"
    image_definitions.append(definition)
    
    # Track converted images for deletion
    converted_images.append((filename, image_path))
    
    print(f"✓ Converted: {filename} -> {ref_id}")

# Append all image definitions at the very end of file
if image_definitions:
    content = content.rstrip() + '\n\n' + '\n\n'.join(image_definitions) + '\n'

# Write back to file
with open(markdown_file, 'w') as f:
    f.write(content)

print(f"\n✅ Converted {len(converted_images)} image(s) to embedded base64")

# Store list of converted files for deletion step
with open(markdown_file + '.converted_files', 'w') as f:
    for filename, filepath in converted_images:
        f.write(f"{filepath}\n")

PYEOF "$MARKDOWN_FILE"

# Verify the conversion was successful
echo ""
echo "Verifying conversion..."

python3 << 'PYEOF'
import sys
import re

markdown_file = sys.argv[1]

with open(markdown_file, 'r') as f:
    content = f.read()

# Check for any remaining Obsidian-style images
remaining = re.findall(r'!\[\[(.*?\.(png|jpg|jpeg|gif|webp))\]\]', content, re.IGNORECASE)

if remaining:
    print(f"⚠️  Warning: {len(remaining)} Obsidian-style image(s) still remain (files may not exist)")
    for img in remaining:
        print(f"   - {img[0]}")
else:
    print("✓ No Obsidian-style image references remain")

# Check that definitions were added
embedded_count = len(re.findall(r'\[embedded-image-\d+\]: <data:image/', content))
print(f"✓ Found {embedded_count} embedded image definition(s) at end of file")

PYEOF "$MARKDOWN_FILE"

# Handle deletion
if [ "$NO_DELETE" = true ]; then
    echo ""
    echo "Skipping deletion (--no-delete flag set)"
    echo "Original files kept. List saved in: ${MARKDOWN_FILE}.converted_files"
    exit 0
fi

# Ask for confirmation before deleting original files
echo ""
echo "Conversion complete! The following files can now be deleted:"
cat "${MARKDOWN_FILE}.converted_files"
echo ""
read -p "Delete these original image files? (yes/no): " confirm

if [ "$confirm" = "yes" ]; then
    while IFS= read -r filepath; do
        if [ -f "$filepath" ]; then
            rm "$filepath"
            echo "✓ Deleted: $filepath"
        fi
    done < "${MARKDOWN_FILE}.converted_files"
    rm "${MARKDOWN_FILE}.converted_files"
    echo "✅ All original image files deleted"
else
    echo "Original files kept. List saved in: ${MARKDOWN_FILE}.converted_files"
fi

echo ""
echo "Done! Backup available at: ${MARKDOWN_FILE}.backup"
