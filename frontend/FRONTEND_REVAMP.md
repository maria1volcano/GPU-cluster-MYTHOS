# Frontend Revamp Plan — Mythos 6 (RAISE AI Hackathon, Paris)

This doc is the working prompt/plan for the frontend pass. Two tracks: **(A) audit
fixes** from the best-practices review, and **(B) a design de-slop** so the UI reads
as intentional infra tooling instead of a generic AI-generated dashboard.

Owner: frontend pair. Backend is a separate workstream — the API contract in
`INTEGRATION.md` does not change.

---

## B. Design direction — "Control-Room Amber"

The current look is textbook AI-slop: dark slate, cyan/emerald glassmorphism,
`backdrop-blur` on everything, a rainbow `bg-clip-text` headline, generic system
font. We replace the _skin_, not the layout or features.

**Concept:** a thermal instrument. It monitors heat, so the accent _is_ heat.
Hard edges, hairline keylines, mono numerics, glow reserved for CRITICAL only.
Reads like real ops hardware in a demo room / on a projector.

### Design tokens (source of truth: `src/styles.css`)

| Token         | Value     | Use                                     |
| ------------- | --------- | --------------------------------------- |
| `--bg`        | `#0a0a0b` | app base (warm near-black)              |
| `--surface`   | `#141416` | panels                                  |
| `--surface-2` | `#1c1c1f` | inset rows                              |
| `--ink`       | `#f5f3ee` | primary text (warm off-white, NOT #fff) |
| `--ink-dim`   | `#a7a29a` | secondary text (AA on --bg)             |
| `--ink-faint` | `#726d65` | tertiary/labels (min AA large)          |
| `--line`      | `#2a2a2e` | hairline keylines / borders             |
| `--heat`      | `#ff6b1a` | PRIMARY accent (orange)                 |
| `--warn`      | `#ffb200` | warning severity (amber)                |
| `--good`      | `#34d0a8` | healthy (the one cool color we keep)    |
| `--watch`     | `#7f8794` | watch severity (neutral steel)          |
| `--crit`      | `#ff3b3b` | critical (glow allowed here only)       |

### Typography (loaded via Google Fonts in `__root.tsx`)

- **Display** — `Archivo Expanded` (700/800), tight tracking. Hero, section titles.
- **Body/UI** — `Archivo` (400/500/600).
- **Mono/data** — `IBM Plex Mono` (400/500). All numbers, metric values, labels,
  telemetry, risk scores. Give the dashboard its "instrument readout" character.

### Texture rules

- **Kill the glass soup.** Panels = flat `--surface` + 1px `--line` border + a
  faint inner top highlight. Keep `backdrop-blur` only where a panel floats over
  the hero image, and dial it way down.
- **Hard corners.** Radius `2px`–`4px` max (was `rounded-2xl/3xl`). Square = control room.
- **Instrument grid** replaces the cyan/rose radial glows in `BackgroundLayer`:
  faint 1px grid + vignette over a darkened, desaturated cluster photo.
- **No gradient text.** Hero headline is solid `--ink`; "Predict · Explain ·
  Rebalance" gets `--heat`, not a rainbow clip.
- **Glow/pulse only on CRITICAL** (rack map + risk badges). Everything else is calm.

Files touched: `styles.css`, `glassStyles.ts` → panel utils, `riskStyles.ts`
(new hex), `BackgroundLayer`, `LiquidLogo`, `MetricCard`, `routes/index.tsx`
(hero + section headers), the three panels.

---

## A. Audit fixes — execution order

Done in this order so the cheap wins land first and the architecture fix lands last.

### 1. Tooling (5 min)

- [ ] `npm run format` → clears the 81 Prettier lint errors (repo didn't pass its own config).
- [ ] Confirm `npm run lint` + `npx tsc --noEmit` are green (the 6 react-refresh warnings are acceptable).

### 2. Content

- [ ] Replace footer copy `Built for Crusoe Managed Inference Track` → RAISE Paris track.
- [ ] Sanity-check meta/OG titles in `__root.tsx` + `routes/index.tsx`.

### 3. Accessibility + theming

- [ ] Wrap dashboard content in a `<main>` landmark.
- [ ] `OverrideModal` → rebuild on the shipped Radix `ui/dialog` (focus trap, Esc, `role=dialog`, labelled).
- [ ] `ExplanationDrawer` → rebuild on the shipped Radix `ui/sheet`.
- [ ] `aria-label` on all icon-only buttons (close `X`, integration toggle).
- [ ] Telemetry feed = `aria-live="polite"` region.
- [ ] Contrast: retire `text-white/30`–`/40` micro-labels for `--ink-dim`/`--ink-faint` that pass AA.
- [ ] Theme: add `class="dark"` to `<html>` and make the `.dark` CSS vars the
      control-room palette, so the 404 + error boundaries stop rendering in light theme.

### 4. Data layer — adopt React Query (the real fix)

- [ ] `QueryClientProvider` is already mounted but unused; replace the manual
      `setInterval(tick, 1500)` + `useState` in `routes/index.tsx`.
- [ ] One combined query preserves mock ordering (state advances the sim → then
      recommendation → then telemetry read):
      `useQuery({ queryKey: ['dashboard'], queryFn: fetchDashboard, refetchInterval: running ? 1500 : false })`.
- [ ] `accept` / `override` / `askWhy` / `stress` → `useMutation` with
      `invalidateQueries(['dashboard'])` on success.
- [ ] Pause = flip `refetchInterval` to `false`. Selected rack stays local state.
- [ ] Standardize live-mode fetch error handling (check `res.ok`) — mostly free once RQ owns retries.

---

## Verify

- [ ] `npm run lint`, `npx tsc --noEmit` green.
- [ ] `npm run dev` → localhost:8080: hero, metrics, 3D map, rack detail on click,
      live telemetry ticking, accept/override/why, pause, stress scenario.
- [ ] Console clean (the dev-only Lovable `data-tsd-source` hydration notice is expected noise).
- [ ] Screenshot desktop + narrow width → share with team for feedback before we commit.

> Nothing here is committed until the team signs off on the new look.
