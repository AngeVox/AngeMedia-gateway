# AngeMedia Assistant and Generation Experience Execution Plan

This plan keeps assistant, pet UI, channel expansion, and operations cleanup as separate batches. Each batch must keep the current queue/job/asset foundation intact and avoid fake controls.

## P0: Generation Experience Stabilization

- Prompt Copilot returns a safe structured route and suggested parameters.
- Image and video generation pages apply prompt, channel, model, and size only when the suggestion matches catalog data.
- LLM settings use unsaved form values for model fetch and connection test, while saved empty keys preserve existing secrets unless explicitly cleared.
- Job result preview must resolve linked assets after success and must not keep a fake loading animation.
- Global image/video timeout settings remain outside individual channel configuration.
- Jobs cleanup keeps hide and destructive clean semantics separate.
- Validation includes text-to-image, image-to-image, text-to-video, and image-to-video.

## P1: Assistant Productization

- Add `assistant_sessions`, `assistant_messages`, and `assistant_runs` storage.
- Store user confirmation state, selected skill, safe tool timeline, and final assistant output.
- Build a Web Studio assistant entry that can discuss AngeMedia tasks, assets, channel status, and common failures.
- Add a bundled AngeMedia knowledge base for FAQ, channel application links, supported sizes, agent integration notes, and common diagnostics.
- Enforce AngeMedia-only scope; unrelated general chat must be refused.
- Keep web search disabled by default. Future search must use explicit enablement, allowlisted domains, and sanitized summaries.

## P2: Ange Pet Widget

- Build a global fixed draggable `ange-pet` widget after the assistant session model exists.
- Clicking the widget opens the same scoped AngeMedia assistant, not a separate bypass path.
- Prepare transparent product assets in multiple sizes before shipping the widget.
- On mobile, degrade to a fixed compact entry to avoid covering generation and Jobs controls.

## P3: Video Channel Expansion

- Use litegen, v0.1.0, and MoviePilot only as architecture references.
- Add channels one adapter at a time: Runway, Kling, Vidu, MiniMax, and Google.
- Each channel requires config schema, capability catalog, mock contract tests, error mapping, safe local asset import, and Jobs/Assets summaries.
- Do not expose a selectable channel until it has a real adapter and test contract.
- Signed/provider URLs must stay server-side and be localized before UI display.

## P4: Operations and Retention

- Add retention policies for jobs, events, attempts, dispatches, and app logs by days and/or row count.
- Add Diagnostics summaries for log size, database size, generated media size, and upload size.
- Destructive cleanup actions require double confirmation and return deleted counts plus estimated freed size when available.
- Dashboard can show health suggestions, but should not become a complex charting dashboard.
