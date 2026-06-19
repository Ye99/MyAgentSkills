---
name: azure-cool-to-cold
description: Safely convert one explicitly scoped Azure Blob container from the Cool access tier to Cold with rclone. Use when an operator asks to pilot, preflight, execute, verify, or repeat a Cool-to-Cold conversion on an rclone-configured Azure Blob container while preserving blob contents and avoiding account-wide or cross-container changes.
---

# Azure Cool to Cold

Convert exactly one Azure Blob container at a time. Treat tiering as a live mutation even though blob content is unchanged.

## Safety rules

- Never print or copy rclone credentials.
- Require a raw Azure Blob remote with exactly one container path, such as `REMOTE:container`.
- Reject account roots such as `REMOTE:`, multi-container scope, crypt overlays, and targets containing a slash.
- Keep discovery and preflight read-only.
- Require every file in the target to be Cool. Stop on mixed, missing, or unknown tiers.
- Show the target, count, bytes, tier inventory, expected effect, and command before requesting approval.
- Obtain explicit approval for the exact target immediately before applying.
- Do not infer approval for any other container.
- Preserve an aggregate pre-change state file; never store filenames or credentials in it.
- Stop on inventory drift between preflight and apply.
- Verify count, bytes, and tier inventory after applying.
- Never retier to Archive with this skill.

## Requirements

Require:

- `rclone`
- Python 3
- An rclone Azure Blob backend that exposes `SetTier: true`

Use the bundled helper at `scripts/azure_cool_to_cold.py`.

## Workflow

### 1. Identify the raw target

Resolve the encrypted user-facing remote to its underlying raw Azure container without revealing secrets. The helper independently rejects crypt remotes and unsafe scope.

Do not proceed when the raw target is uncertain.

### 2. Preflight

Choose a state path outside the skill directory:

```bash
python3 scripts/azure_cool_to_cold.py preflight REMOTE:container \
  --state /tmp/azure-tier-state.json
```

Report the aggregate results. Confirm:

- backend is raw Azure Blob;
- scope is one container;
- all objects are Cool;
- count and bytes are nonzero unless the operator explicitly expects an empty container.

Optionally validate rclone's native mutation path without changing tiers:

```bash
python3 scripts/azure_cool_to_cold.py dry-run REMOTE:container \
  --state /tmp/azure-tier-state.json
```

Warn that native `settier --dry-run` can be much slower than the actual tier operation.

### 3. Estimate impact

Use current official Azure prices for the account region and redundancy. Explain:

- Cold's 90-day minimum billing period starts at conversion;
- moving or deleting sooner may incur a prorated early-deletion charge;
- moving a blob out of Cool before its Cool minimum ends may also incur a remaining Cool charge;
- moving to a cooler tier does not require data download or internet egress;
- the operation changes access-tier metadata, not blob content.

Do not hardcode prices from a previous account or session.

### 4. Request approval

Present the exact target and preflight count. Ask for authorization to run only:

```text
rclone settier Cold REMOTE:container --fast-list
```

Do not apply unless the user explicitly approves that target.

### 5. Apply and verify

Run the guarded apply command using the preflight count:

```bash
python3 scripts/azure_cool_to_cold.py apply REMOTE:container \
  --state /tmp/azure-tier-state.json \
  --confirm-target REMOTE:container \
  --confirm-count OBJECT_COUNT
```

The helper rechecks inventory, applies the tier, and verifies the result.

Success requires:

- exit code zero;
- post-change count equals pre-change count;
- post-change bytes equal pre-change bytes;
- every file reports Cold.

If verification fails, do not retry mutation blindly. Re-run read-only verification and report the mismatch.

### 6. Record the pilot

Record:

- date;
- scoped target without credentials;
- pre/post aggregate count and bytes;
- pre/post tier counts;
- command used;
- estimated one-time operation cost and recurring savings;
- any warnings or errors;
- statement that no other container was targeted.

Do not record PII, credentials, encrypted filenames, or plaintext filenames.

## Read-only verification

Re-run verification at any time:

```bash
python3 scripts/azure_cool_to_cold.py verify REMOTE:container \
  --state /tmp/azure-tier-state.json
```

Do not use content-download checks merely to confirm a tier change. Tier, count, and byte metadata are sufficient.
