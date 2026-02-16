---
description: Add or update Google provider models in the opencode.json configuration
---

# Add Google Models to OpenCode

This skill adds or updates Google AI model definitions in the OpenCode configuration file (`~/.config/opencode/opencode.json`), under the `provider.google` key.

## Usage

Trigger this skill when the user asks to "add Google models to opencode" or similar.

## Steps

1. **Read Current Config**:
   - Read `~/.config/opencode/opencode.json`.
   - Parse the JSON and inspect the `provider.google` key.

2. **Identify Models to Add/Update**:
   - The user will provide a JSON snippet with model definitions.
   - Each model lives under `provider.google.models.<model-id>`.
   - Common fields per model: `id`, `name`, `reasoning`, `release_date`, `limit` (`context`, `output`), `cost` (`input`, `output`, `cache_read`), `modalities`, and `variants` (thinking-level presets).

3. **Merge Strategy**:
   - If `provider.google` does not exist, create it with `"npm": "@ai-sdk/google"` and an empty `models` object first.
   - For each model the user provides:
     - If the model key already exists, **replace** its entire value with the new definition.
     - If the model key does not exist, **add** it.
   - Do **not** remove existing models the user did not mention.

4. **Write Back**:
   - Write the updated JSON back to `~/.config/opencode/opencode.json`, pretty-printed with 2-space indent.
   - Validate the JSON is syntactically correct before writing.

5. **Verify**:
   - Re-read the file and confirm the new models appear under `provider.google.models`.
   - Report which models were added or updated.

## Reference: Current Google Provider Block

The canonical Google provider structure looks like this (more info at https://github.com/shekohex/opencode-google-antigravity-auth?tab=readme-ov-file#examples)

```json
{
  "provider": {
    "google": {
      "npm": "@ai-sdk/google",
      "models": {
        "<model-id>": {
          "id": "<model-id>",
          "name": "Human-Readable Name",
          "reasoning": true,
          "limit": { "context": 1000000, "output": 64000 },
          "cost": { "input": 2, "output": 12, "cache_read": 0.2 },
          "modalities": {
            "input": ["text", "image", "video", "audio", "pdf"],
            "output": ["text"]
          },
          "variants": {
            "low": {
              "options": {
                "thinkingConfig": {
                  "thinkingLevel": "low",
                  "includeThoughts": true
                }
              }
            }
          }
        }
      }
    }
  }
}
```

## Notes

- The `npm` field must always be `"@ai-sdk/google"`.
- `variants` define thinking-level presets (`minimal`, `low`, `medium`, `high`). Not all models support all levels.
- Models without reasoning (e.g. `gemini-2.5-flash-lite`) can omit `limit`, `cost`, `modalities`, and `variants`.
