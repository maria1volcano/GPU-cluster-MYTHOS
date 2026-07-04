"""Central configuration for Sentinel (single source of truth for knobs).

Everything here is deterministic: same config + same seed => same replay,
same telemetry, same demo. (NFR-3 / NFR-6)
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_CSV = REPO_ROOT / "openb_node_list_gpu_node.csv"
POD_CSV = REPO_ROOT / "openb_pod_list_default.csv"
FIXTURES_DIR = REPO_ROOT / "fixtures"

SEED = 42

# --- Rack derivation (DESIGN §2.1) -------------------------------------------
# "model_homogeneous": sort nodes by sn within each GPU model, bucket into racks
# of RACK_SIZE. Racks are homogeneous => clean thermal profiles. (approved rule)
# "contiguous": sort all nodes by sn, bucket into RACK_SIZE — the doc default.
RACK_RULE = "model_homogeneous"
RACK_SIZE = 32

# --- Replay (DESIGN §2.2) -----------------------------------------------------
# deletion_time == TRACE_END_S marks censored pods (still running at trace end).
TRACE_END_S = 12_902_960
TICK_TRACE_S = 30          # 1 tick = 30 trace-seconds (uniform prediction sampling)
SPEEDUP = 240.0            # trace-seconds per wall-second (M5 may tune live)

# Demo window: day 148.38–148.84. Verified against the trace: queue depth peaks
# at 8 heavy jobs at t=12,824,105 (day 148.43) and the densest 2h event windows
# all fall in day 148.4–148.7. (Chosen over the day-137 concurrency peak, where
# the queue is momentarily empty.)
DEMO_WINDOW = (12_820_000, 12_860_000)

# --- Prediction handshake (consumed by M3) ------------------------------------
LEAD_TIME_S = 480          # alert when predicted time-to-throttle < this
# A pod is "heavy" when it demands at least one full GPU-equivalent
# (num_gpu >= 2, or a whole-GPU request). DESIGN §2.4 "heavy jobs queued".
HEAVY_GPU_DEMAND = 1.0

# Optional placement guardrail: defer racks at >= this fraction of GPU capacity
# unless nothing else fits (spreads the map across 2+ racks if the demo wants
# it). None = pure busiest-rack-first packing.
RACK_FILL_CAP = None

# --- Telemetry model (DESIGN §2.3) --------------------------------------------
# A GPU's target temp = idle + (util_temp_max - idle)*util + coupling. Profiles
# keep util_temp_max BELOW throttle_temp, so a lone busy GPU never throttles:
# only node/rack thermal coupling (dense packing of real heavy jobs) pushes it
# over the line. Throttling is an emergent crowding effect, not scripted.
TEMP_TAU_S = 300.0         # thermal time constant; alpha = 1 - exp(-tick/tau)
BURN_IN_S = 1800           # seek pre-roll (6*tau): real events evolve thermal
                           # state to the window start, erasing warm-start
                           # approximation (shared-GPU ages, cooling GPUs)
NODE_COUPLING_C = 5.0      # max °C from a fully-loaded node (same enclosure)
RACK_COUPLING_C = 6.0      # max °C from a fully-loaded rack (shared airflow)
UTIL_NOISE = 0.02          # +/- uniform noise on util (seeded, reproducible)

# --- Agent / Crusoe Inference (M4) --------------------------------------------
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

CRUSOE_BASE_URL = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1/")
CRUSOE_API_KEY = os.environ.get("CRUSOE_API_KEY", "")
CRUSOE_MODEL = os.environ.get("CRUSOE_MODEL", "nvidia/NVIDIA-Nemotron-3-Ultra-550B")
CRUSOE_TIMEOUT_SECONDS = float(os.environ.get("CRUSOE_TIMEOUT_SECONDS", "5"))

# --- Gradium TTS (operator alerts) --------------------------------------------
GRADIUM_API_KEY = os.environ.get("GRADIUM_API_KEY", "")
GRADIUM_VOICE_ID = os.environ.get("GRADIUM_VOICE_ID", "LFZvm12tW_z0xfGo")
GRADIUM_TTS_SPEED = float(os.environ.get("GRADIUM_TTS_SPEED", "1.8"))
GRADIUM_OUTPUT_WAV = Path(os.environ.get("GRADIUM_OUTPUT_WAV", REPO_ROOT / "alert.wav"))

# --- Decision log / learning state (M6/M7) ------------------------------------
DECISION_LOG_PATH = Path(os.environ.get("SENTINEL_DECISION_LOG", REPO_ROOT / "decision_log.jsonl"))
STATE_PATH = Path(os.environ.get("SENTINEL_STATE_PATH", REPO_ROOT / "sentinel_state.json"))

DEFAULT_THERMAL_LEAD_TIME_SECONDS = float(LEAD_TIME_S)
MIN_THERMAL_LEAD_TIME_SECONDS = 120.0

DEFAULT_BOTTLENECK_UTIL_THRESHOLD = 0.05   # matches sentinel.predict.config BOTTLENECK_RACK_UTIL_MIN
DEFAULT_BOTTLENECK_QUEUED_HEAVY = 3
MAX_BOTTLENECK_UTIL_THRESHOLD = 0.99
MAX_BOTTLENECK_QUEUED_HEAVY = 8

LEARNING_MIN_SAMPLES = int(os.environ.get("SENTINEL_LEARNING_MIN_SAMPLES", "3"))
LEARNING_WINDOW = int(os.environ.get("SENTINEL_LEARNING_WINDOW", "3"))
LEARNING_OVERRIDE_RATE_HIGH = float(os.environ.get("SENTINEL_LEARNING_OVERRIDE_RATE_HIGH", "0.5"))
LEARNING_TIGHTEN_STEP = float(os.environ.get("SENTINEL_LEARNING_TIGHTEN_STEP", "0.15"))
LEARNING_RELAX_STEP = float(os.environ.get("SENTINEL_LEARNING_RELAX_STEP", "0.05"))


@dataclass
class ClassThresholds:
    lead_time_seconds: float = DEFAULT_THERMAL_LEAD_TIME_SECONDS
    util_threshold: float = DEFAULT_BOTTLENECK_UTIL_THRESHOLD
    queued_heavy_threshold: int = DEFAULT_BOTTLENECK_QUEUED_HEAVY


@dataclass
class Thresholds:
    """Mutable, persisted threshold state for M7 learning."""

    thermal_throttle: ClassThresholds = field(
        default_factory=lambda: ClassThresholds(lead_time_seconds=DEFAULT_THERMAL_LEAD_TIME_SECONDS)
    )
    scheduling_bottleneck: ClassThresholds = field(
        default_factory=lambda: ClassThresholds(
            util_threshold=DEFAULT_BOTTLENECK_UTIL_THRESHOLD,
            queued_heavy_threshold=DEFAULT_BOTTLENECK_QUEUED_HEAVY,
        )
    )
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
