"""Central configuration for Sentinel.

Everything that needs to be reproducible (seeds, windows) or tunable
(thresholds, LLM endpoint) lives here. `Thresholds` is the one piece of
state that the learning-from-overrides loop (FR-9) is allowed to mutate
at runtime; it is persisted to `SENTINEL_STATE_PATH` so learned
adjustments survive across demo runs.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the repo root so CRUSOE_API_KEY and other env vars are
# available without requiring the user to manually export them.
load_dotenv(REPO_ROOT / ".env")

# --- Reproducibility -------------------------------------------------------
RNG_SEED = int(os.environ.get("SENTINEL_RNG_SEED", "42"))

# --- Topology ----------------------------------------------------------------
RACK_SIZE = int(os.environ.get("SENTINEL_RACK_SIZE", "32"))
HOMOGENEOUS_RACKS = os.environ.get("SENTINEL_HOMOGENEOUS_RACKS", "1") not in ("0", "false", "False")

# --- Replay ------------------------------------------------------------------
SPEEDUP = float(os.environ.get("SENTINEL_SPEEDUP", "240"))
TICK_SECONDS = int(os.environ.get("SENTINEL_TICK_SECONDS", "30"))

# --- Data paths ----------------------------------------------------------------
NODE_CSV = Path(os.environ.get("SENTINEL_NODE_CSV", REPO_ROOT / "openb_node_list_gpu_node.csv"))
POD_CSV = Path(os.environ.get("SENTINEL_POD_CSV", REPO_ROOT / "openb_pod_list_default.csv"))

# --- Crusoe Inference (agent LLM) --------------------------------------------
CRUSOE_BASE_URL = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1/")
CRUSOE_API_KEY = os.environ.get("CRUSOE_API_KEY", "")
CRUSOE_MODEL = os.environ.get("CRUSOE_MODEL", "nvidia/NVIDIA-Nemotron-3-Ultra-550B")
CRUSOE_TIMEOUT_SECONDS = float(os.environ.get("CRUSOE_TIMEOUT_SECONDS", "5"))

# --- Decision log / learning state paths -------------------------------------
DECISION_LOG_PATH = Path(os.environ.get("SENTINEL_DECISION_LOG", REPO_ROOT / "decision_log.jsonl"))
STATE_PATH = Path(os.environ.get("SENTINEL_STATE_PATH", REPO_ROOT / "sentinel_state.json"))

# --- Default (baseline) prediction thresholds --------------------------------
# These are the "factory" values. `Thresholds` below loads persisted
# overrides on top of them, so learning survives restarts but always has a
# documented, inspectable baseline to reset to.
DEFAULT_THERMAL_LEAD_TIME_SECONDS = 480.0  # fire THERMAL_THROTTLE when eta < this (PRD NFR-2: >=5min)
MIN_THERMAL_LEAD_TIME_SECONDS = 120.0
MAX_THERMAL_LEAD_TIME_SECONDS = 600.0

DEFAULT_BOTTLENECK_UTIL_THRESHOLD = 0.90
MIN_BOTTLENECK_UTIL_THRESHOLD = 0.90
MAX_BOTTLENECK_UTIL_THRESHOLD = 0.99

DEFAULT_BOTTLENECK_QUEUED_HEAVY = 3
MIN_BOTTLENECK_QUEUED_HEAVY = 3
MAX_BOTTLENECK_QUEUED_HEAVY = 8

# Learning tuning knobs (FR-9)
LEARNING_MIN_SAMPLES = int(os.environ.get("SENTINEL_LEARNING_MIN_SAMPLES", "3"))
# Only the most recent LEARNING_WINDOW decisions per alert class are
# considered, so the loop adapts to *recent* operator behavior (a class
# that was over-alerting last week but has been fine since should relax
# back down) rather than being dominated by all-time history.
LEARNING_WINDOW = int(os.environ.get("SENTINEL_LEARNING_WINDOW", "3"))
LEARNING_OVERRIDE_RATE_HIGH = float(os.environ.get("SENTINEL_LEARNING_OVERRIDE_RATE_HIGH", "0.5"))
LEARNING_TIGHTEN_STEP = float(os.environ.get("SENTINEL_LEARNING_TIGHTEN_STEP", "0.15"))
LEARNING_RELAX_STEP = float(os.environ.get("SENTINEL_LEARNING_RELAX_STEP", "0.05"))


@dataclass
class ClassThresholds:
    """Tunable thresholds for one prediction class (e.g. THERMAL_THROTTLE)."""

    lead_time_seconds: float = DEFAULT_THERMAL_LEAD_TIME_SECONDS
    util_threshold: float = DEFAULT_BOTTLENECK_UTIL_THRESHOLD
    queued_heavy_threshold: int = DEFAULT_BOTTLENECK_QUEUED_HEAVY


@dataclass
class Thresholds:
    """Mutable, persisted threshold state for every prediction class.

    Loaded once at process start via `Thresholds.load()`, mutated in place
    by `sentinel.learning.OverrideLearner`, and saved back to disk so the
    next run starts from the learned values (FR-9).
    """

    thermal_throttle: ClassThresholds = field(
        default_factory=lambda: ClassThresholds(lead_time_seconds=DEFAULT_THERMAL_LEAD_TIME_SECONDS)
    )
    scheduling_bottleneck: ClassThresholds = field(
        default_factory=lambda: ClassThresholds(
            util_threshold=DEFAULT_BOTTLENECK_UTIL_THRESHOLD,
            queued_heavy_threshold=DEFAULT_BOTTLENECK_QUEUED_HEAVY,
        )
    )
    # History of adjustments made by the learner, kept for auditability.
    adjustment_log: list = field(default_factory=list)

    @classmethod
    def load(cls, path: Path = STATE_PATH) -> "Thresholds":
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                return cls(
                    thermal_throttle=ClassThresholds(**raw.get("thermal_throttle", {})),
                    scheduling_bottleneck=ClassThresholds(**raw.get("scheduling_bottleneck", {})),
                    adjustment_log=raw.get("adjustment_log", []),
                )
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()

    def save(self, path: Path = STATE_PATH) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "thermal_throttle": asdict(self.thermal_throttle),
            "scheduling_bottleneck": asdict(self.scheduling_bottleneck),
            "adjustment_log": self.adjustment_log,
        }

    def reset_to_defaults(self) -> None:
        self.thermal_throttle = ClassThresholds(lead_time_seconds=DEFAULT_THERMAL_LEAD_TIME_SECONDS)
        self.scheduling_bottleneck = ClassThresholds(
            util_threshold=DEFAULT_BOTTLENECK_UTIL_THRESHOLD,
            queued_heavy_threshold=DEFAULT_BOTTLENECK_QUEUED_HEAVY,
        )
        self.adjustment_log = []
