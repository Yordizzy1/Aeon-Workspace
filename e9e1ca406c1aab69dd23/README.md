# Aeon Agent Output Anomaly Detector API

A live, machine-facing guardrail API for AI-agent workflows.

**Live service:** https://aeon-workspace.onrender.com/?src=github

**API docs:** https://aeon-workspace.onrender.com/docs?src=github

**OpenAPI 3.1:** https://aeon-workspace.onrender.com/openapi.json

## What it catches

The current deterministic detector flags:

- empty output;
- output longer than 10,000 characters;
- output containing `ERROR` (case-insensitive).

It is intentionally narrow, cheap, deterministic, and designed to sit before another automated step.

## Pricing

`$0.003` per detection event through prepaid API credits.

## Quick start

```bash
curl -X POST https://aeon-workspace.onrender.com/detect \
  -H "Authorization: Bearer YOUR_AEON_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"output":"Agent response here","event_id":"run-001"}'
```

A repeated `event_id` is idempotent and does not consume another credit.

## Machine discovery

- `GET /openapi.json`
- `GET /llms.txt`
- `GET /agents.txt`
- `GET /agents.json`
- `GET /.well-known/api-catalog`
- `GET /sitemap.xml`

## Authentication

Use `Authorization: Bearer <AEON_API_KEY>` or `X-Aeon-API-Key`.

## Buy credits

https://aeon-workspace.onrender.com/go/github

## Health

https://aeon-workspace.onrender.com/health

## Privacy / telemetry

Public telemetry is aggregate-only. It does not expose customer emails, API keys, Stripe secrets, database credentials, or IP addresses.
