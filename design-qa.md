# Provider Config Bounded Layout - Design QA

## Comparison Target

- Source visual truth path: `E:/Cache/Temp/codex-clipboard-268cd8bc-ad5f-473b-985b-b84b6dd0dc65.png`
- Implementation screenshot path: `E:/Cache/Temp/angemedia-providers-reference-size-drawer-final.png`
- Additional responsive evidence:
  - `E:/Cache/Temp/angemedia-providers-light-list.png`
  - `E:/Cache/Temp/angemedia-providers-mobile-list.png`
  - `E:/Cache/Temp/angemedia-providers-mobile-drawer.png`
- Viewport: 1487 x 1058 desktop comparison; 390 x 844 mobile verification.
- State: Providers route, dark theme, OpenAI-compatible Image configuration drawer open. Light theme and default closed state were also verified.

## Full-View Comparison Evidence

The source and implementation use the same product direction: a bounded provider registry remains visible on the left while exactly one full-height configuration drawer opens on the right. The implementation preserves the existing AngeMedia shell and design tokens instead of replacing the application navigation. The registry is capped at 430px with internal scrolling, and Catalog/Reserved remain collapsed summaries.

The first comparison pass found that the implementation backdrop over-darkened and blurred the registry. The final CSS reduces the backdrop to `rgba(2, 8, 18, 0.22)`, removes blur, and uses an opaque `--bg-2` drawer surface so the provider rows remain readable without showing ghost text through the editor.

## Focused Region Comparison Evidence

The drawer header and close control were reviewed separately after the user reported the stretched button. The control now measures 49.6 x 34px at both desktop and 390px, aligns to the top-right, and no longer stretches to the header height. The drawer itself measures from viewport top `0` to viewport bottom and is mounted under `document.body`, avoiding the page animation containing block.

## Required Fidelity Surfaces

- Fonts and typography: Existing AngeMedia font stack, weights, truncation, and hierarchy are preserved. Registry headers, provider names, IDs, metadata, and drawer section labels remain readable at desktop and mobile widths.
- Spacing and layout rhythm: Registry rows use a compact grid, the list has a fixed maximum height, the drawer is full-height, and footer actions remain stable. At 390px rows stack without overlap and the two footer buttons remain fully visible.
- Colors and visual tokens: Existing dark/light tokens are reused. Semantic enabled, disabled, configured, warning, and connection states remain intact. The drawer is opaque in both themes and the desktop backdrop is intentionally light.
- Image quality and asset fidelity: This screen contains no content imagery. No fake provider logos, handcrafted SVGs, emoji, or CSS-drawn assets were introduced.
- Copy and content: Existing runtime configuration and secret-handling copy is retained. New search, registry, drawer, close, cancel, and status labels are localized in Chinese and English.
- Interaction and accessibility: Search, configure, close, backdrop close, Escape close, row test, save, clear, enabled toggle, focus return, and route-change cleanup are implemented. The dialog has an accessible label and API Key remains an empty password input.

## Findings

No actionable P0, P1, or P2 findings remain.

- Acceptable deviation: The source concept uses provider logos and an icon-only close affordance. The existing product has no matching provider icon asset system, so the implementation deliberately uses provider text identity and a compact localized text close button instead of fake assets.
- Residual test gap: Browser screenshot capture became intermittent late in the session. The final opaque drawer surface was additionally verified from computed style as `rgb(8, 24, 47)` after the last screenshot; interaction, geometry, responsive, and console checks all ran against the final code.

## Patches Made Since Previous QA Pass

1. Disabled scroll anchoring so the registry opens at the first provider instead of clipping the first row.
2. Portaled the drawer to `document.body` so it spans the viewport rather than the animated page container.
3. Added explicit hidden-row CSS so provider search actually removes non-matching rows visually.
4. Constrained the close button to a compact 34px height.
5. Reduced and deblurred the desktop backdrop, then made the drawer surface opaque.

## Implementation Checklist

- [x] Bounded provider registry with internal scrolling.
- [x] Exactly one on-demand configuration drawer.
- [x] Desktop and 390px layouts without horizontal overflow.
- [x] Light and dark theme readability.
- [x] Write-only API Key input and existing security behavior preserved.
- [x] Browser console has no warnings or errors.

final result: passed
