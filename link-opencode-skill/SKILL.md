---
name: link-opencode-skill
description: Use when a user wants to install or link local skills into OpenCode, either as a whole folder symlink or per-skill symlinks.
---

# Link OpenCode Skill

This skill allows you to "install" or "link" skills to OpenCode, making them available for use by the agent. It supports two linking strategies — **whole folder** or **individual skill** — and should present both options to the user with their trade-offs.

## Usage
Trigger this skill when the user asks to "install a skill", "link a skill", "link a skill folder", or similar.

## Step 1 — Ask the User Which Strategy

Present the two options with their pros and cons:

### Option A: Whole Folder Symlink _(recommended for single-source setups)_

Replaces the entire `~/.config/opencode/skills/` directory with a symlink to a git-tracked skills folder.

| Pros | Cons |
|---|---|
| Zero maintenance — new skills auto-appear | All-or-nothing — can't selectively disable a skill |
| One symlink to manage | All skills must live in the same repo |
| No risk of forgetting to link new skills | Loses any local-only files in the target dir |

### Option B: Individual Skill Symlinks _(recommended for multi-source setups)_

Creates one symlink per skill inside `~/.config/opencode/skills/`.

| Pros | Cons |
|---|---|
| Selective — enable/disable per skill | Must manually link each new skill |
| Can pull from **multiple** repos | More symlinks to track |
| Supports mixing git-tracked and local-only skills | Easy to forget a new skill |

---

## Option A — Whole Folder Symlink

### Steps

1. **Identify Source Folder**:
   - Get the absolute path to the skills folder the user wants to link.
   - Example: `/Users/user/p/MyAgentSkills` or `~/projects/my-skills-repo`.

2. **Verify Source**:
   - Confirm the folder exists and contains at least one subdirectory with a `SKILL.md` file.
   - Command: `find <source_path> -maxdepth 2 -name SKILL.md`

3. **Back Up Existing (if needed)**:
   - If `~/.config/opencode/skills` already exists (as a directory or symlink), warn the user that it will be replaced.
   - If it's a directory with contents, suggest backing up first:
     ```
     mv ~/.config/opencode/skills ~/.config/opencode/skills.bak
     ```

4. **Link**:
   - Remove the existing target and create the symlink:
     ```
     rm -rf ~/.config/opencode/skills
     ln -s "<source_path>" ~/.config/opencode/skills
     ```

5. **Verify**:
   - Confirm the symlink is correct:
     ```
     ls -la ~/.config/opencode/skills
     ls ~/.config/opencode/skills/
     ```

### Example
User: "Link my skills folder at ~/p/MyAgentSkills"
```
rm -rf ~/.config/opencode/skills
ln -s ~/p/MyAgentSkills ~/.config/opencode/skills
```

---

## Option B — Individual Skill Symlinks

### Steps

1. **Identify Source**:
   - Get the absolute path of the skill directory the user wants to install.
   - Example: `~/projects/my-new-skill` or `/Users/user/p/MyAgentSkills/skill-name`.
   - **Do NOT assume a specific parent directory.** The user can provide any path.

2. **Verify Source**:
   - Confirm the source directory exists and contains a `SKILL.md` file.
   - Command: `ls <source_path>/SKILL.md`

3. **Ensure Destination Directory Exists**:
   - The destination must be a real directory (not a folder symlink):
     ```
     mkdir -p ~/.config/opencode/skills
     ```

4. **Link**:
   - Create a symbolic link from the source to the destination.
   - `<skill_name>` should match the source directory name.
     ```
     ln -s "<source_path>" "$HOME/.config/opencode/skills/<skill_name>"
     ```

5. **Verify**:
   - Confirm the link was created:
     ```
     ls -l "$HOME/.config/opencode/skills/<skill_name>"
     ```

### Example
User: "Install the skill at ~/projects/awesome-skill"
```
mkdir -p ~/.config/opencode/skills
ln -s ~/projects/awesome-skill ~/.config/opencode/skills/awesome-skill
```

---

## Notes
- Both strategies also apply to `~/.gemini/antigravity/skills/` for Antigravity/Gemini agents. If the user wants skills linked there too, repeat the same process for that path.
- When switching **from individual symlinks to whole folder**, all existing individual symlinks are removed. Warn the user if any of those symlinks pointed to different source repos.
- When switching **from whole folder to individual**, first remove the folder symlink, then `mkdir -p` the directory, then create individual symlinks.
