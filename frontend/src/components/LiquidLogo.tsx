export function LiquidLogo() {
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-11 w-11">
        <svg viewBox="0 0 100 100" className="h-full w-full">
          <defs>
            <radialGradient id="lg-a" cx="50%" cy="40%" r="60%">
              <stop offset="0%" stopColor="#ffd29a" />
              <stop offset="45%" stopColor="#ff6b1a" />
              <stop offset="100%" stopColor="#1c1c1f" />
            </radialGradient>
            <linearGradient id="lg-b" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#ffb200" stopOpacity="0.95" />
              <stop offset="100%" stopColor="#ff3b3b" stopOpacity="0.9" />
            </linearGradient>
            <filter id="lg-blur" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="1.4" />
            </filter>
          </defs>
          <circle cx="50" cy="50" r="46" fill="url(#lg-a)" />
          <g filter="url(#lg-blur)" className="origin-center animate-[spin_18s_linear_infinite]">
            <path
              d="M50 12 C 72 22, 82 40, 76 60 C 70 80, 46 86, 30 74 C 14 62, 18 36, 34 22 C 40 17, 46 14, 50 12 Z"
              fill="url(#lg-b)"
              opacity="0.85"
            />
          </g>
          <circle cx="50" cy="50" r="46" fill="none" stroke="white" strokeOpacity="0.12" />
          <circle cx="36" cy="34" r="7" fill="white" fillOpacity="0.35" filter="url(#lg-blur)" />
        </svg>
      </div>
      <div className="flex flex-col leading-none">
        <span className="font-display text-2xl font-bold uppercase tracking-tight text-ink">
          Mythos 6
        </span>
        <span className="mt-1.5 font-mono text-[11px] uppercase tracking-[0.22em] text-ink-dim">
          GPU Cluster Ops Agent
        </span>
        <span className="mt-1 font-mono text-[10px] uppercase tracking-[0.28em] text-heat">
          Predict · Explain · Rebalance
        </span>
      </div>
    </div>
  );
}
