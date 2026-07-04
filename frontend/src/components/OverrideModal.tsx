import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const REASONS = [
  "Operator knows maintenance is scheduled",
  "Do not move priority workload",
  "Cooling issue already handled",
  "Other",
];

export function OverrideModal({
  open,
  onClose,
  onSubmit,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (reason: string) => void;
}) {
  const [selected, setSelected] = useState(REASONS[0]);
  const [other, setOther] = useState("");
  const finalReason = selected === "Other" ? other || "Other" : selected;

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="rounded-sm border-line bg-surface">
        <DialogHeader>
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-ink-faint">
            Override recommendation
          </p>
          <DialogTitle className="mt-2 text-lg font-semibold text-ink">
            Tell the agent why
          </DialogTitle>
          <DialogDescription className="text-xs text-ink-dim">
            Feedback is used to refine future recommendations for this cluster.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {REASONS.map((r) => (
            <label
              key={r}
              className={`flex cursor-pointer items-center gap-3 rounded-sm border px-3.5 py-2.5 text-sm transition ${
                selected === r
                  ? "border-heat/50 bg-heat/10 text-ink"
                  : "border-line bg-surface-2 text-ink-dim hover:border-line hover:bg-surface"
              }`}
            >
              <input
                type="radio"
                name="reason"
                checked={selected === r}
                onChange={() => setSelected(r)}
                className="accent-heat"
              />
              {r}
            </label>
          ))}
          {selected === "Other" && (
            <textarea
              value={other}
              onChange={(e) => setOther(e.target.value)}
              placeholder="Add a short note…"
              className="mt-2 h-20 w-full rounded-sm border border-line bg-bg p-3 text-sm text-ink placeholder:text-ink-faint focus:border-heat/40 focus:outline-none"
            />
          )}
        </div>

        <div className="mt-2 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="min-h-11 rounded-sm border border-line px-4 py-2 font-mono text-xs uppercase tracking-wider text-ink-dim transition hover:text-ink"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              onSubmit(finalReason);
              onClose();
            }}
            className="min-h-11 rounded-sm bg-heat px-4 py-2 font-mono text-xs font-semibold uppercase tracking-wider text-[#0a0a0b] transition hover:bg-heat/90"
          >
            Submit override
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
