# Link OpenCode Skill

This skill allows you to "install" or "link" a local directory as an OpenCode skill, making it available for use by the agent.

## Usage
Trigger this skill when the user asks to "install a skill", "link a skill", "use a skill from a local directory", or similar.

## Steps
1.  **Identify Source**:
    *   Get the absolute path of the skill directory the user wants to install.
    *   Example: `~/projects/my-new-skill` or `/Users/user/p/MyAgentSkills/skill-name`.
    *   **Do NOT assume a specific parent directory** (like `p/MyAgentSkills`). The user can provide any path.

2.  **Verify Source**:
    *   Check if the source directory exists and contains a `SKILL.md` file (this is required for OpenCode skills).
    *   Use `ls <source_path>/SKILL.md`.

3.  **Determine Destination**:
    *   The destination for user-installed skills is always: `~/.config/opencode/skills/<skill_name>`.
    *   Make sure `<skill_name>` matches the directory name of the source.

4.  **Link**:
    *   Create a symbolic link from the source to the destination.
    *   Command: `ln -s "<source_path>" "$HOME/.config/opencode/skills/<skill_name>"`

5.  **Verify**:
    *   Check that the link was created successfully.
    *   Command: `ls -l "$HOME/.config/opencode/skills/<skill_name>"`

## Example
User: "Install the skill at ~/projects/awesome-skill"
Action:
- Source: `~/projects/awesome-skill`
- Name: `awesome-skill`
- Command: `ln -s ~/projects/awesome-skill ~/.config/opencode/skills/awesome-skill`
