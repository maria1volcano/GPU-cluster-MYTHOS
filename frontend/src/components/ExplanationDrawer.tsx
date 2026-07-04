import { BookOpen, ChevronRight } from "lucide-react";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

const SECTIONS: [string, string][] = [
  [
    "Independent signals",
    "Every rack drifts on its own random walk. Stress mode nudges one rack's temperature trend and queue pressure upward.",
  ],
  [
    "Risk engine",
    "calculateRackRisk() blends temperature, trend, cooling, utilization, queue, power draw and neighbor heat into a 0–100 score.",
  ],
  [
    "Recommendation",
    "The agent picks the worst rack and searches for a safer destination with headroom before recommending migration.",
  ],
  [
    "Operator loop",
    "Accept applies the migration and the risk drops. Override records feedback the agent uses for future weighting.",
  ],
  [
    "Swap in real data",
    "The frontend calls /api/cluster/state and /api/agent/recommendation. Flip VITE_USE_MOCKS=false to point at a live backend.",
  ],
];

export function ExplanationDrawer() {
  return (
    <Sheet>
      <SheetTrigger className="inline-flex min-h-11 items-center gap-2 rounded-sm border border-line bg-surface px-4 py-1.5 font-mono text-xs uppercase tracking-wider text-ink-dim transition hover:border-heat/40 hover:text-ink">
        <BookOpen className="h-3 w-3" /> How the prediction works
      </SheetTrigger>
      <SheetContent side="right" className="w-full max-w-md overflow-y-auto border-line bg-surface">
        <SheetHeader>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-faint">
            Judge explainer
          </p>
          <SheetTitle className="mt-2 text-2xl font-semibold text-ink">
            How the prediction works
          </SheetTitle>
          <SheetDescription className="text-sm leading-relaxed text-ink-dim">
            This demo does not replay a scripted overheat. It simulates independent live signals for
            each rack — workload intensity, cooling efficiency, power draw, neighboring rack heat,
            and queue pressure — and the agent scores risk from how those signals change together.
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 space-y-3 text-sm text-ink">
          {SECTIONS.map(([title, body]) => (
            <div key={title} className="rounded-sm border border-line bg-surface-2 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                <ChevronRight className="h-3.5 w-3.5 text-heat" />
                {title}
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">{body}</p>
            </div>
          ))}
        </div>
      </SheetContent>
    </Sheet>
  );
}
