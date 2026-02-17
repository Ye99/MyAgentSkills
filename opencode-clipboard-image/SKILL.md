---
name: opencode-clipboard-image
description: Save clipboard images to a timestamped file and return a ready-to-paste path for OpenCode (Linux-first, X11/Wayland aware).
---

# OpenCode Clipboard Image

## Purpose

Automate the common flow where image paste into terminal chat does not work: extract image data from clipboard, save it to a file, and provide a path the user can reference in OpenCode.

## When To Use

- User says image paste is not working in OpenCode/TUI.
- User asks to "paste screenshot" into OpenCode from clipboard.
- User wants a one-step workflow to save clipboard image and attach by path.

## Expected Outcome

1. Clipboard image is saved to `~/Pictures/opencode/clip-YYYYmmdd-HHMMSS.<ext>`.
2. Agent returns the absolute file path.
3. Agent gives the exact message pattern to use in OpenCode (for example: `@/home/user/Pictures/opencode/clip-....png`).

## Required Workflow

1. Detect Linux session type (`X11` or `Wayland`) when relevant:

```bash
echo "${XDG_SESSION_TYPE:-unknown}"
```

2. Ensure clipboard utility exists:
- X11: prefer `xclip`.
- Wayland: prefer `wl-paste` (package `wl-clipboard`).

3. If missing, instruct installation command for detected distro, then continue.

4. Save clipboard image into a stable directory:

```bash
mkdir -p "$HOME/Pictures/opencode"
```

5. Use the first available image MIME type, save with matching extension, and print final path.

## Commands

### Linux X11 (`xclip`)

```bash
mkdir -p "$HOME/Pictures/opencode"
mime="$(xclip -selection clipboard -t TARGETS -o | tr ' ' '\n' | grep '^image/' | head -n1)"
case "$mime" in
  image/png) ext="png" ;;
  image/jpeg) ext="jpg" ;;
  image/webp) ext="webp" ;;
  image/gif) ext="gif" ;;
  *) mime="image/png"; ext="png" ;;
esac
f="$HOME/Pictures/opencode/clip-$(date +%Y%m%d-%H%M%S).$ext"
xclip -selection clipboard -t "$mime" -o > "$f" && printf '%s\n' "$f"
```

### Linux Wayland (`wl-paste`)

```bash
mkdir -p "$HOME/Pictures/opencode"
mime="$(wl-paste --list-types | grep '^image/' | head -n1)"
case "$mime" in
  image/png) ext="png" ;;
  image/jpeg) ext="jpg" ;;
  image/webp) ext="webp" ;;
  image/gif) ext="gif" ;;
  *) mime="image/png"; ext="png" ;;
esac
f="$HOME/Pictures/opencode/clip-$(date +%Y%m%d-%H%M%S).$ext"
wl-paste --type "$mime" > "$f" && printf '%s\n' "$f"
```

## Install Hints

- Debian/Ubuntu (X11): `sudo apt update && sudo apt install -y xclip`
- Debian/Ubuntu (Wayland): `sudo apt update && sudo apt install -y wl-clipboard`
- Arch: `sudo pacman -S xclip` or `sudo pacman -S wl-clipboard`
- Fedora: `sudo dnf install -y xclip` or `sudo dnf install -y wl-clipboard`

## Response Template

After saving successfully, respond with:

- Saved: `<absolute_path>`
- Use in OpenCode: `@<absolute_path>`

If clipboard has no image MIME type, respond with:

- "Clipboard does not currently contain an image. Copy an image first, then retry."

## Safety

- Do not delete or overwrite existing files.
- Use timestamped filenames only.
- Never assume root/sudo access; provide install command if dependency is missing.
