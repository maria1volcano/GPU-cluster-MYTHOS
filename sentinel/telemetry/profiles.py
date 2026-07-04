"""Per-model thermal/power profiles (DESIGN §7).

Real datacenter parts use public spec values (TDP, base clocks). G2/G3 are
Alibaba-anonymized models: G2 is assigned a V100-class profile, G3 an
A100-class profile (DESIGN assigns them ~300W / ~350W on 8-GPU dense nodes —
the fleet's natural hotspots). T4's low ceiling is what makes T4 racks the
cool migration targets.

`util_temp_max` is the steady temp of a lone GPU at util=1.0 — deliberately
below `throttle_temp` (see sentinel/config.py: throttling needs coupling).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GpuProfile:
    idle_temp: float
    util_temp_max: float     # steady temp at util=1.0, no neighbors
    throttle_temp: float     # SW_THERMAL slowdown threshold
    idle_w: float
    tdp_w: float
    base_sm_mhz: int
    mem_clock_mhz: int
    mem_total_mib: int


PROFILES = {
    "G2":      GpuProfile(34.0, 80.0, 84.0, 42.0, 300.0, 1530, 877, 16384),
    "G3":      GpuProfile(33.0, 81.0, 85.0, 52.0, 350.0, 1410, 1215, 40960),
    "V100M16": GpuProfile(34.0, 79.0, 84.0, 40.0, 300.0, 1530, 877, 16384),
    "V100M32": GpuProfile(34.0, 79.0, 84.0, 40.0, 300.0, 1530, 877, 32768),
    "P100":    GpuProfile(33.0, 80.0, 85.0, 32.0, 250.0, 1328, 715, 16384),
    "T4":      GpuProfile(28.0, 62.0, 85.0, 12.0, 70.0, 1590, 5001, 16384),
    "A10":     GpuProfile(30.0, 70.0, 86.0, 20.0, 150.0, 1695, 6251, 24576),
}
