---
description: Add or update Google provider models in the opencode.json configuration
---

# Add Google Models to OpenCode

This skill adds or updates Google AI model definitions in the OpenCode configuration file (`~/.config/opencode/opencode.json`), under the `provider.google` key.

## Usage

Trigger this skill when the user asks to "add Google models to opencode" or similar.

## Prerequisites

To use Google models with OpenCode, you must enable the Gemini API in your Google Cloud project:
1. Go to [Google Cloud Console API Library](https://console.cloud.google.com/apis/library).
2. Search for **Gemini for Google Cloud API** (`cloudaicompanion.googleapis.com`) and click **Enable**.

## Steps

1. **Read Current Config**:
   - Read `~/.config/opencode/opencode.json`.
   - Parse the JSON and inspect the `provider.google` key.

2. **Fetch Latest Reference Configuration**:
   - Read the latest configuration from [GitHub](https://github.com/shekohex/opencode-google-antigravity-auth?tab=readme-ov-file#example-opencode-config-with-providermodels).
   - Extract the JSON configuration block from the content. This is the **source of truth** for model definitions.

3. **Identify Models to Add/Update**:
   - Use the models defined in the fetched JSON under `provider.google.models`.
   - Each model lives under `provider.google.models.<model-id>`.
   - Common fields per model: `id`, `name`, `reasoning`, `release_date`, `limit` (`context`, `output`), `cost` (`input`, `output`, `cache_read`), `modalities`, and `variants` (thinking-level presets).

4. **Merge Strategy**:
   - If `provider.google` does not exist, create it with `"npm": "@ai-sdk/google"` and an empty `models` object first.
   - **Merge** the model definitions from the **fetched JSON configuration**.
   - **IMPORTANT**: Skip the `release_date` field for all models during the merge.
   - For each model:
     - If the model key already exists, **merge** the new definition (excluding `release_date`).
     - If the model key does not exist, **add** it.
   - Do **not** remove existing models.

5. **Write Back**:
   - Write the updated JSON back to `~/.config/opencode/opencode.json`, pretty-printed with 2-space indent.
   - Validate the JSON is syntactically correct before writing.

6. **Verify**:
   - Re-read the file and confirm the new models appear under `provider.google.models`.
   - Report which models were added or updated.

## Reference: Current Google Provider Block

The canonical Google provider structure based on [this example](https://github.com/shekohex/opencode-google-antigravity-auth?tab=readme-ov-file#example-opencode-config-with-providermodels):

```json
{
  "plugin": ["opencode-google-antigravity-auth"],
  "provider": {
    "google": {
      "npm": "@ai-sdk/google",
      "models": {
        "gemini-3-pro-preview": {
          "id": "gemini-3-pro-preview",
          "name": "Gemini 3 Pro",
          "reasoning": true,
          "limit": { "context": 1000000, "output": 64000 },
          "cost": { "input": 2, "output": 12, "cache_read": 0.2 },
          "modalities": {
            "input": ["text", "image", "video", "audio", "pdf"],
            "output": ["text"]
          },
          "variants": { 
             "low": { ... },
             "medium": { ... },
             "high": { ... }
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
