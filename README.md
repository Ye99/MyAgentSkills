# MyAgentSkills

A collection of specialized skills to extend the capabilities of AI coding agents.

Each skill lives in its own directory and is documented in a `SKILL.md` file.

## Available Skills

- **format-markdownfile-code-block** ([`format-markdownfile-code-block/SKILL.md`](format-markdownfile-code-block/SKILL.md)): Normalize Markdown notes so command/code lines are easy to read.
- **markdown-topic-normalizer** ([`markdown-topic-normalizer/SKILL.md`](markdown-topic-normalizer/SKILL.md)): Normalize escaped topic markers into proper Markdown headings while preserving code fences and real lists.
- **utm-backup-restore** ([`utm-backup-restore/SKILL.md`](utm-backup-restore/SKILL.md)): Back up and restore UTM VM bundles with full macOS bundle metadata.

## Usage

To use these skills, import the relevant `SKILL.md` path into your agent's context or skill library.

The `SKILL.md` files are designed to be self-contained; for skills that ship helper scripts, those are kept alongside the skill (for example: `utm-backup-restore/scripts`).

## Repository Notes

- Contributor and documentation policies: `AGENTS.md`

## License

MIT (see `LICENSE`).
