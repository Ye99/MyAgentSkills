---
name: locationiq-nearby-poi
description: Use when implementing or documenting LocationIQ Nearby POI lookups from latitude/longitude, including API key setup and plan-aware rate limit handling.
---

# LocationIQ Nearby POI

Use this skill when building scripts or apps that call LocationIQ's Nearby POI endpoint (`/v1/nearby`).

## API Key Setup

1. Create an access token in the LocationIQ dashboard.
2. Store it in an environment variable (recommended):

```bash
export LOCATIONIQ_API_KEY="your_token_here"
```

3. Use the variable in code/CLI calls. Do not hardcode tokens.

```bash
curl "https://us1.locationiq.com/v1/nearby?key=${LOCATIONIQ_API_KEY}&lat=40.68917&lon=-74.04444&tag=all&radius=1000&format=json"
```

## Nearby Endpoint

- US endpoint: `https://us1.locationiq.com/v1/nearby`
- EU endpoint: `https://eu1.locationiq.com/v1/nearby`
- Required params: `key`, `lat`, `lon`
- Common params: `tag` (default `all`), `radius` (1..30000), `format=json`

## us1 vs eu1 Semantics

- In LocationIQ docs, `us1`/`eu1` are presented as region endpoints chosen mainly for lower latency (closer to users), not as documented separate coverage datasets.
- Nearby POI docs list both endpoints but do not explicitly promise identical output across regions.
- Nearby POI is explicitly marked Public Beta; response behavior may change.
- Practical guidance: for reproducible pipelines, pick one region per dataset/batch and keep it fixed for reruns.

Source references:
- Search docs (endpoint selection for speed): `https://docs.locationiq.com/docs/search-forward-geocoding`
- Reverse docs (endpoint selection for speed): `https://docs.locationiq.com/docs/reverse-geocoding`
- Timezone docs (choose endpoint closer to users): `https://docs.locationiq.com/docs/timezone`
- Nearby docs (public beta + dual endpoints): `https://docs.locationiq.com/docs/nearby-poi`
- Nearby API ref: `https://docs.locationiq.com/reference/nearby-poi-api`

## Rate Limits (Service/Plan)

Nearby POI uses your account plan limits (not a separate public Nearby-only quota in docs).

- Free plan (pricing page): `5000/day`, `2 req/sec`, and `60 req/min`
- Paid plans: per-second limit depends on tier (for example, Developer `20 req/sec`, Growth Plus `30 req/sec`, Business Plus `40 req/sec`)
- If limits are exceeded, expect HTTP `429`

Source references:
- Nearby docs: `https://docs.locationiq.com/docs/nearby-poi`
- Nearby API ref: `https://docs.locationiq.com/reference/nearby-poi-api`
- Pricing/rate tiers: `https://locationiq.com/pricing`

## Implementation Guidance

- Prefer server-side calls for private workloads.
- For browser/mobile tokens, use referrer/IP restrictions where supported and rotate tokens.
- On HTTP `429`, inspect the error message and branch:
  - `Rate Limited Second` / `Rate Limited Minute`: retry with bounded exponential backoff + jitter.
  - `Rate Limited Day`: non-retryable for the current run; stop gracefully and resume after quota reset.
  - Unknown/ambiguous `429`: optionally use Balance API as a secondary signal, but treat balance as potentially lagging under continuous calls.
- For conservative batch safety on unknown `429`, treat `balance.day < 100` as exhausted and stop gracefully.
- Log request IDs and status codes, not full secrets.

Balance API references:
- Guide: `https://docs.locationiq.com/docs/balance-api`
- API ref: `https://docs.locationiq.com/reference/balance-api-ref`

## Secret and PII Safety Checklist

- Never commit real access tokens (`pk.` or `sk.` style values).
- Use placeholders like `your_token_here` in docs/examples.
- Keep `.env` out of version control.
- Do not log full tokens; if needed, log only a short masked suffix.
