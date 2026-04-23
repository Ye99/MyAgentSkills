# MyAgentSkills
A collection of specialized skills to extend the capabilities of AI coding agents.

## Available Skills

- **add-google-models-to-opencode** ([`add-google-models-to-opencode/SKILL.md`](add-google-models-to-opencode/SKILL.md)): Add or update Google provider models in the opencode.json configuration.
- **dedup-copy** ([`dedup-copy/SKILL.md`](dedup-copy/SKILL.md)): Copy files from a source to a destination while eliminating content-identical duplicates via jdupes, with scored keeper selection, JSON logging, and independent verification.
- **convert-external-images** ([`convert-external-images/SKILL.md`](convert-external-images/SKILL.md)): Convert external image files in Obsidian markdown to embedded base64 data URLs with clean reference-style syntax.
- **create_mcp_ext_apps** ([`create_mcp_ext_apps/SKILL.md`](create_mcp_ext_apps/SKILL.md)): Build MCP ext-apps using a reference architecture with reusable transport, registration, and UI runtime patterns.
- **format-markdownfile-code-block** ([`format-markdownfile-code-block/SKILL.md`](format-markdownfile-code-block/SKILL.md)): Normalize Markdown notes so command/code lines are easy to read.
- **find-missing-files** ([`find-missing-files/SKILL.md`](find-missing-files/SKILL.md)): Find files present in a source directory that are missing from a destination directory, compared by content hash rather than filename.
- **AI-folder-poi-itinerary-rename** ([`AI-folder-poi-itinerary-rename/SKILL.md`](AI-folder-poi-itinerary-rename/SKILL.md)): Rename day-based media folders with AI-selected itinerary landmarks from EXIF GPS clusters, preserving chronological order with resumable state and failure diagnostics.
- **jetson-ollama-upgrade** ([`jetson-ollama-upgrade/SKILL.md`](jetson-ollama-upgrade/SKILL.md)): Upgrade Ollama on Jetson using jetson-containers, build CUDA-enabled image, and verify newer model pull support.
- **link-opencode-skill** ([`link-opencode-skill/SKILL.md`](link-opencode-skill/SKILL.md)): Install local directories as OpenCode skills by creating symbolic links in `~/.config/opencode/skills/`.
- **locationiq-nearby-poi** ([`locationiq-nearby-poi/SKILL.md`](locationiq-nearby-poi/SKILL.md)): Implement and document LocationIQ Nearby POI lookups with safe API key setup and plan-aware rate limit handling.
- **markdown-obsidian-linker** ([`markdown-obsidian-linker/SKILL.md`](markdown-obsidian-linker/SKILL.md)): Enforce Obsidian-safe markdown headings so internal links like `[[#Header]]` resolve reliably.
- **markdown-topic-normalizer** ([`markdown-topic-normalizer/SKILL.md`](markdown-topic-normalizer/SKILL.md)): Normalize escaped topic markers into proper Markdown headings while preserving code fences and real lists.
- **opencode-clipboard-image** ([`opencode-clipboard-image/SKILL.md`](opencode-clipboard-image/SKILL.md)): Save clipboard images to timestamped files and return OpenCode-ready `@path` references.
- **photo-gps-from-exif** ([`photo-gps-from-exif/SKILL.md`](photo-gps-from-exif/SKILL.md)): Extract latitude/longitude from JPG, HEIC, and MOV files using exiftool with batch-friendly output.
- **review-tests** ([`review-tests/SKILL.md`](review-tests/SKILL.md)): Review test suites for redundant cases and maintainability while preserving fast, clear failure diagnosis.
- **setup-github-coauthors** ([`setup-github-coauthors/SKILL.md`](setup-github-coauthors/SKILL.md)): Set a repo-local git commit template with valid GitHub noreply co-author trailers for OpenCode, Claude, and Codex (using `openai` for `openai/codex`) with resolved account IDs and no hardcoded personal names or emails.
- **organize-photos-and-videos-by-day** ([`organize-photos-and-videos-by-day/SKILL.md`](organize-photos-and-videos-by-day/SKILL.md)): Organize large source trees into `%Y/%Y_%m_%d` using offline geo-timezone conversion, high-fidelity media copy, and no-loss verification.
- **utm-backup-restore** ([`utm-backup-restore/SKILL.md`](utm-backup-restore/SKILL.md)): Back up and restore UTM VM bundles with full macOS bundle metadata.
- **virsh-delete-and-flatten-snapshots** ([`virsh-delete-and-flatten-snapshots/SKILL.md`](virsh-delete-and-flatten-snapshots/SKILL.md)): Remove libvirt external snapshot records or retire all restore points and reclaim QEMU/KVM overlay disk space with deterministic helper-script execution.
- **split-gopro-video** ([`split-gopro-video/SKILL.md`](split-gopro-video/SKILL.md)): Split GoPro HEVC MP4 files into named time-range segments with full fidelity — original bitrate, GPMF/GPS track, camera metadata, and accurate timestamps all preserved.
- **virsh-vm-snapshots** ([`virsh-vm-snapshots/SKILL.md`](virsh-vm-snapshots/SKILL.md)): Manage external-only QEMU/KVM VM snapshots with `virsh` for UEFI-compatible backup workflows.

## AI agent rules

- **[AGENTS.md](AGENTS.md)**: Best practice rules.
- Keep the global, always-loaded agent rule minimal and stable: core identity/persona, security guardrails, tool usage principles, system-wide constraints, and non-negotiable policies.
- Use skills for conditional, scoped capabilities: domain-specific instructions, task patterns (for example, "when writing PR reviews..."), rare workflows, specialized formatting rules, and tool-specific heuristics.

## Usage
These are standards; your AI agent should apply them automatically.

## License
MIT (see `LICENSE`).
