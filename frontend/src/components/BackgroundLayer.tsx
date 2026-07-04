// A generated control-room canvas — no stock photo, minimal and on-theme.
// The motif is a thermal isotherm field: concentric hairline rings radiating from
// a heat focus (upper-right), echoing "thermal spread / risk radar" for the track.
// Layers: optional opt-in photo, isotherm rings, fine instrument grid, an aligned
// heat bloom, a vignette to focus the console, and a whisper of anti-banding grain.
const GRAIN =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='140' height='140'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E";

// Concentric isotherms centered on the heat focus, fading out from it.
function Isotherms() {
  const rings = [9, 18, 28, 39, 51, 64, 78, 93];
  return (
    <div
      className="absolute inset-0 text-ink opacity-[0.08]"
      style={{
        WebkitMaskImage: "radial-gradient(85% 85% at 78% 6%, #000 26%, transparent 78%)",
        maskImage: "radial-gradient(85% 85% at 78% 6%, #000 26%, transparent 78%)",
      }}
    >
      <svg
        className="h-full w-full"
        viewBox="0 0 100 100"
        preserveAspectRatio="xMidYMid slice"
        aria-hidden
      >
        {rings.map((r) => (
          <circle
            key={r}
            cx="78"
            cy="6"
            r={r}
            fill="none"
            stroke="currentColor"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        ))}
      </svg>
    </div>
  );
}

export function BackgroundLayer() {
  const bg = import.meta.env.VITE_BACKGROUND_IMAGE as string | undefined;

  return (
    <div aria-hidden className="fixed inset-0 -z-10 overflow-hidden bg-bg">
      {bg && (
        <img
          src={bg}
          alt=""
          className="absolute inset-0 h-full w-full object-cover object-center opacity-20 [filter:grayscale(0.6)_contrast(1.05)]"
        />
      )}

      {/* Thermal isotherm field — minimal, on-theme */}
      <Isotherms />

      {/* Fine instrument grid */}
      <div className="instrument-grid absolute inset-0 opacity-50" />

      {/* Heat bloom at the isotherm focus — the single load-bearing accent */}
      <div className="absolute -top-48 right-[6%] h-[560px] w-[560px] rounded-full bg-[radial-gradient(circle,rgba(255,107,26,0.13),transparent_70%)]" />

      {/* Edge vignette to focus the console */}
      <div className="absolute inset-0 bg-[radial-gradient(125%_95%_at_50%_30%,transparent_48%,rgba(10,10,11,0.94)_100%)]" />

      {/* Grain — subtle, prevents banding on the near-black gradients */}
      <div
        className="absolute inset-0 opacity-[0.045] mix-blend-soft-light"
        style={{ backgroundImage: `url("${GRAIN}")` }}
      />
    </div>
  );
}
