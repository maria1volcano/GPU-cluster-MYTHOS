// Control-room panels: flat surfaces, hairline keylines, hard corners, a faint
// inner top highlight. No glass soup — blur is used sparingly, only where a panel
// floats over the hero photograph. Export names are kept stable so callers don't
// need to change.

export const glassPanel =
  "rounded-sm border border-line bg-surface shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]";

export const strongGlassPanel =
  "rounded-sm border border-line bg-surface shadow-[0_24px_70px_-40px_rgba(0,0,0,0.9),inset_0_1px_0_rgba(255,255,255,0.05)]";

export const softGlassPanel =
  "rounded-sm border border-line bg-surface/85 backdrop-blur-sm shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]";

export const innerGlassPanel =
  "rounded-sm border border-line/80 bg-surface-2 shadow-[inset_0_1px_0_rgba(255,255,255,0.03)]";
