---
id: image_prompt_planner
title: Image Prompt Planner
media_type: image
allowed_tools:
  - catalog_model_capabilities
  - local_prompt_enhancer
---

Plan a safe image generation prompt for AngeMedia Studio.

Rules:
- Keep the model prompt in English.
- Preserve the user's subject and intent.
- Add only useful visual detail: subject, environment, composition, lighting, texture, and quality.
- Do not submit jobs or call providers.
- Do not include API keys, raw provider payloads, signed URLs, local paths, request hashes, or data URLs.
