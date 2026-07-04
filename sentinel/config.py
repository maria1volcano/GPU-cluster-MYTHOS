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
NODE_COUPLING_C = 5.0      # max °C from a fully-loaded node (same enclosure)
RACK_COUPLING_C = 6.0      # max °C from a fully-loaded rack (shared airflow)
UTIL_NOISE = 0.02          # +/- uniform noise on util (seeded, reproducible)
