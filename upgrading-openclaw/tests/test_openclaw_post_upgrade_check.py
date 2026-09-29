"""Tests for scripts/openclaw_post_upgrade_check.sh.

Each test builds a fake gateway host (fake `openclaw`, `systemctl`, `crontab`
binaries plus an installed ACP adapter tree) in tmp_path, breaks exactly one
thing, and asserts the matching check FAILs while the rest PASS.
"""

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "openclaw_post_upgrade_check.sh"

HEALTHY_STATUS = {
    "defaultModel": "openai/gpt-6-sol",
    "auth": {
        "modelRouteIssues": [],
        "runtimeAuthRoutes": [{"provider": "openai", "runtime": "codex", "status": "usable"}],
        "oauth": {
            "profiles": [
                {"profileId": "openai:account-x", "provider": "openai", "type": "oauth"},
                {"profileId": "anthropic:claude-cli", "provider": "anthropic", "type": "oauth"},
            ]
        },
    },
}

SYSTEMD_OK = "RestartUSec=1min\nStartLimitIntervalUSec=0\nRestartPreventExitStatus=78\nActiveState=active\n"
CRON_OK = "# DISABLED: 7,37 * * * * /opt/guards/codex_auth_guard.sh\n*/30 * * * * /opt/other.sh\n"


def write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def make_adapter(root: Path, sdk_version: str) -> Path:
    """Install layout of `npm i --prefix root @agentclientprotocol/claude-agent-acp`."""
    nm = root / "node_modules"
    entry = nm / "@agentclientprotocol" / "claude-agent-acp" / "dist" / "index.js"
    entry.parent.mkdir(parents=True)
    entry.write_text("// adapter\n")
    sdk = nm / "@anthropic-ai" / "claude-agent-sdk"
    sdk.mkdir(parents=True)
    (sdk / "package.json").write_text(json.dumps({"version": sdk_version}))
    return entry


@pytest.fixture
def host(tmp_path):
    """Fake gateway host; returns a dict of mutable fixture state + a run()."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fx = tmp_path / "fx"
    (fx / "config").mkdir(parents=True)

    new_adapter = make_adapter(tmp_path / "acp-adapters" / "claude-new", "0.3.284")
    pinned_adapter = make_adapter(tmp_path / "plugin-captures" / "pinned", "0.3.257")
    wrapper = tmp_path / "acpx" / "claude-agent-acp-wrapper.mjs"
    wrapper.parent.mkdir()
    wrapper.write_text(f'const installedBinPath = "{pinned_adapter}";\n')

    state = {
        "status": json.loads(json.dumps(HEALTHY_STATUS)),
        "systemd": SYSTEMD_OK,
        "cron": CRON_OK,
        "config": {
            "plugins.entries.acpx.config.agents.claude.command":
                f"/usr/bin/node {wrapper} --openclaw-run-configured /usr/bin/node {new_adapter}",
            "plugins.entries.acpx.config.permissionMode": "approve-all",
        },
        "paths": {"wrapper": wrapper, "new": new_adapter, "pinned": pinned_adapter},
    }

    write_exe(bin_dir / "openclaw", f"""
FX={fx}
case "$1 $2" in
  "--version "*) echo "OpenClaw 2026.9.6 (test)"; exit 0;;
  "config get") f="$FX/config/$3"; [ -f "$f" ] && {{ cat "$f"; exit 0; }}
                echo "Config path is valid but unset: $3."; exit 1;;
  "models status") cat "$FX/status.json"; exit 0;;
esac
echo "unexpected: $*" >&2; exit 2
""")
    write_exe(bin_dir / "systemctl", f'cat {fx}/systemd.txt\n')
    write_exe(bin_dir / "crontab", f'cat {fx}/cron.txt\n')

    def run(**env_overrides):
        (fx / "status.json").write_text(json.dumps(state["status"]))
        (fx / "systemd.txt").write_text(state["systemd"])
        (fx / "cron.txt").write_text(state["cron"])
        for f in (fx / "config").iterdir():
            f.unlink()
        for key, value in state["config"].items():
            if value is not None:
                (fx / "config" / key).write_text(value + "\n")
        env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "OPENCLAW_BIN": str(bin_dir / "openclaw"),
            "MIN_CLAUDE_CODE": "2.1.280",
            **env_overrides,
        }
        proc = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
        lines = {}
        for line in proc.stdout.splitlines():
            parts = line.split(maxsplit=2)
            if len(parts) >= 2 and parts[0] in ("PASS", "FAIL", "INFO"):
                lines[parts[1]] = parts[0]
        return proc, lines

    state["run"] = run
    return state


def fails(lines):
    return sorted(k for k, v in lines.items() if v == "FAIL")


def test_healthy_host_passes_every_check(host):
    proc, lines = host["run"]()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert fails(lines) == []
    assert {"service-restart-policy", "openai-single-refresher", "no-custom-openai-provider",
            "o-agent-auth-route", "c-acp-adapter", "c-acp-permission-mode"} <= set(lines)


def test_restart_policy_reset_by_upgrade_fails(host):
    host["systemd"] = SYSTEMD_OK.replace("StartLimitIntervalUSec=0", "StartLimitIntervalUSec=10s")
    proc, lines = host["run"]()
    assert proc.returncode == 1
    assert fails(lines) == ["service-restart-policy"]


def test_second_imported_openai_profile_fails(host):
    host["status"]["auth"]["oauth"]["profiles"].append(
        {"profileId": "openai:account-y", "provider": "openai", "type": "oauth"})
    _, lines = host["run"]()
    assert fails(lines) == ["openai-single-refresher"]


def test_active_external_codex_refresh_cron_fails(host):
    host["cron"] = CRON_OK + "7,37 * * * * /opt/guards/codex_auth_guard.sh\n"
    _, lines = host["run"]()
    assert fails(lines) == ["openai-single-refresher"]


def test_openai_model_without_any_openai_profile_fails(host):
    host["status"]["auth"]["oauth"]["profiles"] = [
        p for p in host["status"]["auth"]["oauth"]["profiles"] if p["provider"] != "openai"]
    _, lines = host["run"]()
    assert "openai-single-refresher" in fails(lines)


def test_agent_added_openai_provider_block_fails(host):
    host["config"]["models.providers.openai"] = '{"auth": "oauth"}'
    _, lines = host["run"]()
    assert fails(lines) == ["no-custom-openai-provider"]


def test_route_issue_for_o_model_fails(host):
    host["status"]["auth"]["modelRouteIssues"] = [{"model": "openai/gpt-6-sol", "detail": "requires api-key auth"}]
    _, lines = host["run"]()
    assert fails(lines) == ["o-agent-auth-route"]


def test_adapter_override_reset_falls_back_to_pinned_old_runtime(host):
    host["config"]["plugins.entries.acpx.config.agents.claude.command"] = (
        f"/usr/bin/node {host['paths']['wrapper']}")
    proc, lines = host["run"]()
    assert fails(lines) == ["c-acp-adapter"]
    assert "2.1.257" in proc.stdout


def test_old_pinned_runtime_passes_when_no_minimum_given(host):
    host["config"]["plugins.entries.acpx.config.agents.claude.command"] = (
        f"/usr/bin/node {host['paths']['wrapper']}")
    _, lines = host["run"](MIN_CLAUDE_CODE="")
    assert "c-acp-adapter" not in fails(lines)


def test_override_pointing_at_missing_adapter_fails(host):
    host["paths"]["new"].unlink()
    _, lines = host["run"]()
    assert fails(lines) == ["c-acp-adapter"]


def test_permission_mode_unset_fails(host):
    host["config"]["plugins.entries.acpx.config.permissionMode"] = None
    _, lines = host["run"]()
    assert fails(lines) == ["c-acp-permission-mode"]


def test_permission_mode_expectation_is_configurable(host):
    host["config"]["plugins.entries.acpx.config.permissionMode"] = "approve-reads"
    _, lines = host["run"](EXPECT_PERMISSION_MODE="approve-reads")
    assert fails(lines) == []
