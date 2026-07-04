"""DCGM-style telemetry model — DESIGN.md §2.3.

`TelemetrySource` is the swappable interface: `SimTelemetrySource`
synthesizes believable per-GPU samples from load; `DcgmTelemetrySource` is
a documented stub for swapping in a real NVIDIA DCGM feed later without
touching the prediction layer (PRD NG5 / DESIGN §2.3 "Real DCGM later").
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

from sentinel.models import GpuTelemetrySample

# Per-model thermal/power profiles — DESIGN.md §7 (grounded in the real
# model mix from the node CSV: G2/G3 dominate and run hottest, T4 is the
# natural cool/high-headroom migration target).
MODEL_PROFILES: Dict[str, Dict[str, float]] = {
    "G2": {"idle_temp": 35.0, "throttle_temp": 83.0, "max_temp": 92.0, "tdp": 300.0, "idle_w": 45.0, "base_clock_mhz": 1410},
    "G3": {"idle_temp": 36.0, "throttle_temp": 83.0, "max_temp": 92.0, "tdp": 350.0, "idle_w": 50.0, "base_clock_mhz": 1410},
    "P100": {"idle_temp": 32.0, "throttle_temp": 84.0, "max_temp": 90.0, "tdp": 250.0, "idle_w": 30.0, "base_clock_mhz": 1328},
    "V100M32": {"idle_temp": 32.0, "throttle_temp": 85.0, "max_temp": 91.0, "tdp": 300.0, "idle_w": 35.0, "base_clock_mhz": 1380},
    "V100M16": {"idle_temp": 32.0, "throttle_temp": 85.0, "max_temp": 91.0, "tdp": 300.0, "idle_w": 35.0, "base_clock_mhz": 1380},
    "T4": {"idle_temp": 30.0, "throttle_temp": 87.0, "max_temp": 94.0, "tdp": 70.0, "idle_w": 12.0, "base_clock_mhz": 1590},
    "A10": {"idle_temp": 31.0, "throttle_temp": 85.0, "max_temp": 92.0, "tdp": 150.0, "idle_w": 20.0, "base_clock_mhz": 1695},
}

DEFAULT_PROFILE = MODEL_PROFILES["G2"]

# Thermal inertia: fraction of the gap to the load-driven target temperature
# closed per tick. Smaller = slower/steadier, larger = twitchier.
THERMAL_ALPHA = 0.12
RACK_COUPLING_C = 6.0  # extra degrees added at full rack utilization (neighbor heat)


class TelemetrySource(Protocol):
    def sample(self, gpu_id: str, t: int) -> GpuTelemetrySample: ...


@dataclass
class _GpuState:
    temp_c: float
    sm_clock_mhz: int


class SimTelemetrySource:
    """Synthesizes per-GPU telemetry as a function of load (DESIGN §2.3).

    `util_provider(gpu_id) -> float in [0,1]` and `rack_util_provider(rack_id)
    -> float in [0,1]` decouple this from any particular scheduler/replayer
    implementation — the simulator just needs to answer "how loaded is this
    GPU/rack right now".
    """

    def __init__(
        self,
        gpu_models: Dict[str, str],  # gpu_id -> model
        gpu_node: Dict[str, str],  # gpu_id -> node_sn
        gpu_rack: Dict[str, str],  # gpu_id -> rack_id
        util_provider,
        rack_util_provider,
        seed: int = 42,
    ) -> None:
        self._gpu_models = gpu_models
        self._gpu_node = gpu_node
        self._gpu_rack = gpu_rack
        self._util_provider = util_provider
        self._rack_util_provider = rack_util_provider
        self._rng = random.Random(seed)
        self._state: Dict[str, _GpuState] = {
            gpu_id: _GpuState(temp_c=MODEL_PROFILES.get(model, DEFAULT_PROFILE)["idle_temp"], sm_clock_mhz=MODEL_PROFILES.get(model, DEFAULT_PROFILE)["base_clock_mhz"])
            for gpu_id, model in gpu_models.items()
        }

    @property
    def gpu_ids(self) -> List[str]:
        return list(self._gpu_models.keys())

    def sample(self, gpu_id: str, t: int) -> GpuTelemetrySample:
        model = self._gpu_models[gpu_id]
        profile = MODEL_PROFILES.get(model, DEFAULT_PROFILE)
        state = self._state[gpu_id]

        util = max(0.0, min(1.0, self._util_provider(gpu_id) + self._rng.uniform(-0.02, 0.02)))
        rack_id = self._gpu_rack[gpu_id]
        rack_util = self._rack_util_provider(rack_id)

        temp_target = profile["idle_temp"] + (profile["max_temp"] - profile["idle_temp"]) * util
        temp_target += RACK_COUPLING_C * rack_util
        state.temp_c += (temp_target - state.temp_c) * THERMAL_ALPHA

        power_w = profile["idle_w"] + (profile["tdp"] - profile["idle_w"]) * util

        throttle_reasons: List[str] = []
        if state.temp_c > profile["throttle_temp"]:
            over = state.temp_c - profile["throttle_temp"]
            ratio = max(0.55, 1.0 - over / 25.0)
            state.sm_clock_mhz = int(profile["base_clock_mhz"] * ratio)
            throttle_reasons.append("SW_THERMAL" if over < 8 else "HW_THERMAL")
        else:
            state.sm_clock_mhz = profile["base_clock_mhz"]

        # Rare, load/age-weighted XID/ECC events (node-instability signal, FR-4).
        instability_score = 0.0005 + 0.004 * util + (0.01 if state.temp_c > profile["throttle_temp"] else 0.0)
        xid_errors = 1 if self._rng.random() < instability_score else 0
        ecc_aggregate = 1 if self._rng.random() < instability_score * 0.5 else 0

        return GpuTelemetrySample(
            gpu_id=gpu_id,
            node_sn=self._gpu_node[gpu_id],
            rack_id=rack_id,
            model=model,
            t=t,
            util=util,
            temp_c=round(state.temp_c, 2),
            power_w=round(power_w, 1),
            sm_clock_mhz=state.sm_clock_mhz,
            mem_clock_mhz=int(0.65 * state.sm_clock_mhz),
            mem_used_mib=int(util * 16000),
            throttle_reasons=throttle_reasons,
            xid_errors=xid_errors,
            ecc_errors={"volatile": 0, "aggregate": ecc_aggregate},
        )


class DcgmTelemetrySource:
    """Stub for a real NVIDIA DCGM feed (PRD NG5 / DESIGN §2.3).

    Intended field mapping from `dcgmi`/DCGM-exporter once wired up:
        GPU_TEMP                -> temp_c
        POWER_USAGE             -> power_w
        SM_CLOCK                -> sm_clock_mhz
        MEM_CLOCK                -> mem_clock_mhz
        FB_USED                  -> mem_used_mib
        XID_ERRORS                -> xid_errors
        ECC_SBE_VOL_TOTAL/DBE     -> ecc_errors
        CLOCK_THROTTLE_REASONS    -> throttle_reasons
    Not implemented in v1 — raises to make it obvious this seam isn't wired
    up yet, rather than silently returning fake data.
    """

    def sample(self, gpu_id: str, t: int) -> GpuTelemetrySample:  # pragma: no cover
        raise NotImplementedError("DcgmTelemetrySource is a documented seam for a future real DCGM feed.")
