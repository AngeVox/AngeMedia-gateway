# Video Channel Adapter Contract

Video channels must not appear in Studio as selectable channels until this contract is complete.

## Candidate Order

- Runway
- Kling
- Vidu
- MiniMax
- Google

## Required Backend Work

- Add a real adapter that can submit tasks and poll status without exposing provider raw payloads.
- Add runtime configuration schema and safe connection test behavior.
- Add catalog provider and model entries only after the adapter exists.
- Add request hash fields that do not include API keys, signed URLs, raw provider responses, or local filesystem paths.
- Add provider error mapping into existing Jobs diagnostics categories.
- Import generated video through server-side controlled download and store only `/generated` or `/uploads` paths in UI summaries.

## Required Tests

- Unit tests for submit payload mapping.
- Unit tests for poll terminal status mapping.
- Mock contract tests for success, upstream timeout, authentication failure, rate limit, ambiguous submit, and provider error body sanitization.
- Queue worker tests proving submit, poll, and asset import use the formal job lifecycle.
- Jobs/Assets tests proving raw provider payloads, signed URLs, request hashes, and local paths are not returned.

## UI Rule

The Studio catalog may show only real, test-backed channel entries. Reserved or candidate channels must remain non-selectable and must not be presented as usable.
