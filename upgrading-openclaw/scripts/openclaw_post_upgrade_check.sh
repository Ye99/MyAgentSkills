#!/usr/bin/env bash
# Read-only post-upgrade checks for an OpenClaw gateway host.
# Run ON the gateway host, e.g.:  ssh <gateway-host> 'bash -s' < openclaw_post_upgrade_check.sh
# Pass settings as env vars on the remote side:
#   ssh <gateway-host> 'MIN_CLAUDE_CODE=2.1.280 bash -s' < openclaw_post_upgrade_check.sh
#
# Prints one line per check: PASS|FAIL|INFO <check-id> <detail>. Exit 1 if any FAIL.
#
# Env (all optional):
#   OPENCLAW_BIN            openclaw CLI (default: on PATH, else ~/.npm-global/bin/openclaw)
#   SERVICE                 systemd --user unit (default: openclaw-gateway)
#   O_AGENT                 agent id behind the OpenAI/Codex chat (default: main)
#   EXPECT_PERMISSION_MODE  acpx permissionMode the C agent needs (default: approve-all)
#   MIN_CLAUDE_CODE         minimum Claude Code version C's model needs, e.g. 2.1.280 (default: unchecked)
#   EXTERNAL_REFRESH_RE     regex for crontab lines that would refresh the same OpenAI token
#                           (default: codex_auth_guard|codex_token_refresh|codex +login)
set -u

OPENCLAW_BIN="${OPENCLAW_BIN:-$(command -v openclaw || echo "$HOME/.npm-global/bin/openclaw")}"
SERVICE="${SERVICE:-openclaw-gateway}"
O_AGENT="${O_AGENT:-main}"
EXPECT_PERMISSION_MODE="${EXPECT_PERMISSION_MODE:-approve-all}"
MIN_CLAUDE_CODE="${MIN_CLAUDE_CODE:-}"
EXTERNAL_REFRESH_RE="${EXTERNAL_REFRESH_RE:-codex_auth_guard|codex_token_refresh|codex +login}"

failed=0
pass() { echo "PASS $1 $2"; }
fail() { echo "FAIL $1 $2"; failed=1; }
info() { echo "INFO $1 $2"; }
oc() { "$OPENCLAW_BIN" "$@" 2>/dev/null; }

info openclaw-version "$(oc --version | head -1)"

# 1. Upgrades rewrite the main unit; the restart-policy drop-in must still win.
unit="$(systemctl --user show "$SERVICE" -p ActiveState -p StartLimitIntervalUSec -p RestartPreventExitStatus -p RestartUSec 2>/dev/null)"
if grep -qx 'ActiveState=active' <<<"$unit" && grep -qx 'StartLimitIntervalUSec=0' <<<"$unit" \
   && grep -q '^RestartPreventExitStatus=.*\b78\b' <<<"$unit"; then
  pass service-restart-policy "$(tr '\n' ' ' <<<"$unit")"
else
  fail service-restart-policy "want active, StartLimitIntervalUSec=0, RestartPreventExitStatus has 78; got: $(tr '\n' ' ' <<<"$unit")"
fi

status_json="$(oc models status --agent "$O_AGENT" --json)"

# 2. Exactly one refresher per OpenAI refresh token, or both lock out (refresh_token_reused).
read -r default_model openai_profiles <<<"$(python3 -c '
import json, sys
try: d = json.loads(sys.argv[1])
except Exception: print("? -1"); sys.exit()
profiles = d.get("auth", {}).get("oauth", {}).get("profiles", [])
n = sum(1 for p in profiles if p.get("provider") == "openai" and p.get("type") == "oauth")
print(d.get("defaultModel") or "?", n)' "$status_json")"
active_refreshers="$(crontab -l 2>/dev/null | grep -v '^[[:space:]]*#' | grep -E "$EXTERNAL_REFRESH_RE" || true)"
if [ "$openai_profiles" = "-1" ]; then
  fail openai-single-refresher "could not read '$OPENCLAW_BIN models status --agent $O_AGENT --json'"
elif [ -n "$active_refreshers" ]; then
  fail openai-single-refresher "active crontab line also refreshes the OpenAI token: $active_refreshers"
elif [ "$openai_profiles" -gt 1 ]; then
  fail openai-single-refresher "$openai_profiles OpenAI OAuth profiles for agent $O_AGENT (want at most 1)"
elif [ "$openai_profiles" -eq 0 ] && [[ "$default_model" == openai/* ]]; then
  fail openai-single-refresher "agent $O_AGENT uses $default_model but has no OpenAI OAuth profile"
else
  pass openai-single-refresher "$openai_profiles OpenAI OAuth profile(s), no external refresh cron"
fi

# 3. An agent-authored provider block overrides the built-in OpenAI routes.
if custom="$(oc config get models.providers.openai)"; then
  fail no-custom-openai-provider "models.providers.openai is set: $(head -c 200 <<<"$custom" | tr '\n' ' ')"
else
  pass no-custom-openai-provider "models.providers.openai unset"
fi

# 4. The O agent's model must have a usable auth route.
route="$(python3 -c '
import json, sys
try: a = json.loads(sys.argv[1]).get("auth", {})
except Exception: print("unreadable"); sys.exit()
bad = [json.dumps(i)[:160] for i in a.get("modelRouteIssues", [])]
bad += [r.get("provider", "?") + ":" + r.get("status", "?") for r in a.get("runtimeAuthRoutes", []) if r.get("status") != "usable"]
print("; ".join(bad))' "$status_json")"
if [ -z "$route" ]; then
  pass o-agent-auth-route "$default_model routes usable"
else
  fail o-agent-auth-route "$route"
fi

# 5. C runs the Claude Code bundled in its ACP adapter, not the standalone CLI.
cmd="$(oc config get plugins.entries.acpx.config.agents.claude.command)"
if [[ "$cmd" == *--openclaw-run-configured* ]]; then
  adapter="$(grep -oE '[^ ]*claude-agent-acp/dist/index\.js' <<<"$cmd" | tail -1)"; source_desc="override"
else
  wrapper="$(grep -oE '[^ ]*claude-agent-acp-wrapper\.mjs' <<<"$cmd" | head -1)"
  adapter="$(grep -oE 'installedBinPath = "[^"]+"' "${wrapper:-/dev/null}" 2>/dev/null | cut -d'"' -f2)"; source_desc="acpx-pinned"
fi
sdk_pkg="${adapter%/@agentclientprotocol/claude-agent-acp/dist/index.js}/@anthropic-ai/claude-agent-sdk/package.json"
if [ -z "$adapter" ] || [ ! -f "$adapter" ]; then
  fail c-acp-adapter "$source_desc adapter not found (command: ${cmd:-unset})"
elif [ ! -f "$sdk_pkg" ]; then
  fail c-acp-adapter "cannot find bundled claude-agent-sdk next to $adapter"
else
  sdk="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$sdk_pkg")"
  claude_code="2.1.${sdk##*.}"   # claude-agent-sdk 0.3.N bundles Claude Code 2.1.N
  if [ -n "$MIN_CLAUDE_CODE" ] && [ "$(printf '%s\n' "$MIN_CLAUDE_CODE" "$claude_code" | sort -V | head -1)" != "$MIN_CLAUDE_CODE" ]; then
    fail c-acp-adapter "$source_desc adapter bundles Claude Code $claude_code < required $MIN_CLAUDE_CODE ($adapter)"
  else
    pass c-acp-adapter "$source_desc adapter bundles Claude Code $claude_code (sdk $sdk) at $adapter"
  fi
fi

# 6. Without the expected permission mode, C reads files but Bash fails with "Tool use aborted".
mode="$(oc config get plugins.entries.acpx.config.permissionMode)"
if [ "$mode" = "$EXPECT_PERMISSION_MODE" ]; then
  pass c-acp-permission-mode "$mode"
else
  fail c-acp-permission-mode "want $EXPECT_PERMISSION_MODE, got ${mode:-unset}"
fi

info manual "send a real message to BOTH chat topics; CLI smoke tests do not exercise the ACP path"
exit "$failed"
