---
name: upgrading-openclaw
description: Use when upgrading, updating, or asked to update an OpenClaw gateway (npm install, `openclaw update`, `/update`, a chat agent offering to self-upgrade), or when chat topics stop replying after an OpenClaw upgrade - silent hangs, instant error replies, "Tool use aborted", "Claude Code X does not support this model", "auth profile openai:default was not found", "no compatible credential source", `refresh_token_reused`, or `update.run completed ... status=skipped` with the version unchanged.
---

# Upgrading OpenClaw

## Overview

An OpenClaw upgrade can report success while every chat channel is dead. Local customizations (auth ownership, ACP adapter override, ACP permissions, systemd drop-ins) silently reset or stop matching new behavior. **The upgrade is done only when the check script passes AND each chat channel answers a real message.**

## Never upgrade from inside OpenClaw

Do not let a chat agent upgrade the gateway it runs in (`/update`, the gateway `update.run` action, `openclaw update` in an agent shell). The upgrade restarts that gateway; if the new version fails, the channel you would use to repair it is gone. Refuse and run the terminal procedure instead.

## Procedure (from an SSH terminal on the gateway host)

1. **Restore point.** Whole-VM backup if the host is a VM (e.g. Proxmox `vzdump <vmid> --mode snapshot`), plus a copy of `~/.openclaw/openclaw.json` and `~/.openclaw/state/openclaw.sqlite*`.
2. **Baseline:** run the check script (below) *before* upgrading; fix or note any FAIL so you don't blame the upgrade for it.
3. **Upgrade detached**, so an SSH drop can't kill the swap:
   `setsid nohup openclaw update --yes --json > update.json 2> update.err < /dev/null &`
   Poll `pgrep -x openclaw-update` until it exits; read `update.json` `status`.
4. **Canary timeout** (`Candidate validation deadline exceeded`): safe, the live install is untouched; rerun step 3. The 300 s canary budget includes snapshotting `~/.openclaw`, so shrink stale large files there first.
5. **Run the check script again**, then send a real message to every chat channel, including one that runs a shell command.

## Check script

```bash
ssh <gateway-host> 'MIN_CLAUDE_CODE=<version C model needs> bash -s' < scripts/openclaw_post_upgrade_check.sh
```

Read-only. Header comments list the env knobs (`O_AGENT`, `EXPECT_PERMISSION_MODE`, `EXTERNAL_REFRESH_RE`, ...).

| Check | FAIL means | Fix |
|---|---|---|
| `service-restart-policy` | Upgrade reconciled the unit; drop-in lost | Restore the `*.service.d/` drop-in (`StartLimitIntervalSec=0`, `RestartSec=60`), `daemon-reload` |
| `openai-single-refresher` | Two holders of one OpenAI refresh token → `refresh_token_reused` lockout | Keep exactly one: OpenClaw's imported profile (`openclaw models auth login --provider openai --method device-code --agent <id>` imports a valid Codex CLI login). Comment out any cron that refreshes `~/.codex/auth.json`; don't run `codex login` there |
| `no-custom-openai-provider` | An agent wrote `models.providers.openai`, forcing an API-key route | `openclaw config unset models.providers.openai` |
| `o-agent-auth-route` | O agent's model has no usable credential | See the two rows above |
| `c-acp-adapter` | C runs an adapter whose bundled Claude Code (`claude-agent-sdk 0.3.N` = `2.1.N`) is too old | `npm i --prefix <dir outside ~/.openclaw/npm> @agentclientprotocol/claude-agent-acp@<ver>`; set `plugins.entries.acpx.config.agents.claude.command` to `node <generated claude-agent-acp-wrapper.mjs> --openclaw-run-configured node <dir>/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js`. Keep the wrapper first (lease tracking) |
| `c-acp-permission-mode` | C reads files but every Bash call fails `Tool use aborted` | `openclaw config set plugins.entries.acpx.config.permissionMode <mode>` |

Restart the gateway after config fixes: `systemctl --user restart openclaw-gateway`.

## Common Mistakes

- **Trusting `update.run completed … status=skipped`** — that is the handoff RPC returning, not the upgrade. Check `openclaw --version`.
- **Smoke-testing C with `openclaw agent --agent <c-agent>`** — that uses the `claude-cli` runtime, not ACP. Only a real channel message tests C.
- **Upgrading the standalone `claude` CLI to fix C** — C uses the adapter's bundled runtime.
- **C message hangs with no `Inbound message` log line** — stale pre-upgrade ACP binding: `openclaw sessions delete --agent <c-agent> --yes <binding key>`, then restart. Older `sessions.json`-based repair helpers are no-ops once sessions live in SQLite.
- **Reading reply text from the gateway journal** — it isn't there. Read the ACP runtime transcript: newest `~/.claude/projects/<workspace>/*.jsonl`.
- **A reply ~3 s after the message** — that's an error message, not an answer.
