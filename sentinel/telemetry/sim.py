"""SimTelemetrySource — synthesized DCGM telemetry as a function of REAL load.

Per tick, for every GPU with load (plus GPUs still cooling — these relax
toward model idle; ambient coupling applies only under load, which keeps
idle GPUs out of the sparse frame by design):
  util   = clamp(placed gpu_milli/1000 + seeded noise, 0, 1)
  target = idle + (util_temp_max - idle)*util
           + NODE_COUPLING_C * node_load_fraction     (same-enclosure heating)
           + RACK_COUPLING_C * rack_load_fraction     (shared airflow)
  temp  += (target - temp) * (1 - exp(-dt/tau))       (thermal inertia)
  power  = idle_w + (tdp - idle_w)*util
  clocks = base until temp >= throttle_temp, then reduced ~4%/°C over
           => throttle_reasons = SW_THERMAL (+HW_THERMAL when 3°C past)
  XID/ECC: rare Poisson-style draws, weighted by a static per-node
           instability factor and thermal stress (node-instability signal, P1)

Determinism: noise and event draws are keyed on blake2s(seed, gpu_id, t) —
independent of call order. Temperatures are stateful (inertia), so a given
seek + tick sequence always reproduces the same frames bit-for-bit.
"""
from __future__ import annotations

import hashlib
import math

from sentinel.config import (NODE_COUPLING_C, RACK_COUPLING_C, SEED,
                             TEMP_TAU_S, UTIL_NOISE)
from sentinel.data.racks import Topology
from sentinel.replay.state import ClusterState
from sentinel.telemetry.profiles import PROFILES
from sentinel.telemetry.sample import GpuTelemetrySample
from sentinel.telemetry.source import TelemetrySource

_XID_RATE_PER_TICK = 2e-5      # per 30 trace-second tick, before weighting
_ECC_RATE_PER_TICK = 4e-4


def _hash_unit(*parts) -> float:
    """Deterministic uniform in [0,1) from the parts — call-order independent."""
    digest = hashlib.blake2s(":".join(str(p) for p in parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2 ** 64


class SimTelemetrySource(TelemetrySource):
    def __init__(self, topology: Topology, seed: int = SEED):
        self.topology = topology
        self.seed = seed
        self._profile_of: dict = {}
        self._node_of: dict = {}
        for node in topology.nodes:
            for g in node.gpu_ids():
                self._profile_of[g] = PROFILES[node.model]
                self._node_of[g] = node
        # Static per-node instability factor: most nodes ~calm, a few flaky.
        self._node_instability = {
            n.sn: 0.25 + 4.0 * _hash_unit(seed, "instability", n.sn) ** 3
            for n in topology.nodes
        }
        self.reset()

    def reset(self) -> None:
        self.t = 0
        self._temps: dict = {}           # gpu_id -> temp, only GPUs off ambient
        self._ecc_aggregate: dict = {}   # gpu_id -> lifetime ECC count
        self._last: dict = {}            # gpu_id -> last computed sample

    # --- internals ---------------------------------------------------------
    def _node_loads(self, state: ClusterState) -> dict:
        loads: dict = {}
        for g in state.active_gpus:
            sn = self._node_of[g].sn
            loads[sn] = loads.get(sn, 0) + state.load_milli[g]
        return loads

    def _util_and_target(self, g: str, t: int, state: ClusterState, node_load: dict):
        """(util, target temp) for one GPU given current real load."""
        prof = self._profile_of[g]
        load = state.load_milli[g]
        if load == 0:
            return 0.0, prof.idle_temp
        node = self._node_of[g]
        rack_id = self.topology.rack_of[node.sn]
        rack = self.topology.rack_by_id[rack_id]
        noise = (_hash_unit(self.seed, "util", g, t) * 2 - 1) * UTIL_NOISE
        util = min(max(load / 1000.0 + noise, 0.0), 1.0)
        target = (prof.idle_temp
                  + (prof.util_temp_max - prof.idle_temp) * util
                  + NODE_COUPLING_C * (node_load.get(node.sn, 0) / (node.gpu * 1000.0))
                  + RACK_COUPLING_C * (state.rack_demand_milli[rack_id]
                                       / (rack.capacity_gpus * 1000.0)))
        return util, target

    # --- per-tick batch ------------------------------------------------------
    def step(self, t: int, state: ClusterState) -> list:
        """Advance to time t; return samples for all interesting GPUs.
        dt == 0 (same-instant re-step) is a pure emission: alpha = 0 leaves
        temps untouched and the event weight is 0, so nothing can re-fire."""
        dt = max(t - self.t, 0)
        self.t = t
        alpha = 1.0 - math.exp(-dt / TEMP_TAU_S)
        node_load = self._node_loads(state)

        samples = []
        self._last = {}
        for g in sorted(set(state.active_gpus) | set(self._temps)):
            prof = self._profile_of[g]
            node = self._node_of[g]
            load = state.load_milli[g]
            util, target = self._util_and_target(g, t, state, node_load)

            temp = self._temps.get(g, prof.idle_temp)
            temp += (target - temp) * alpha
            if load == 0 and abs(temp - prof.idle_temp) < 0.2:
                self._temps.pop(g, None)
            else:
                self._temps[g] = temp

            power = prof.idle_w + (prof.tdp_w - prof.idle_w) * util
            reasons = []
            sm_clock = prof.base_sm_mhz
            if temp >= prof.throttle_temp:
                factor = max(0.6, 1.0 - 0.04 * (temp - prof.throttle_temp + 1.0))
                sm_clock = int(prof.base_sm_mhz * factor)
                reasons.append("SW_THERMAL")
                if temp >= prof.throttle_temp + 3.0:
                    reasons.append("HW_THERMAL")
            if power >= prof.tdp_w * 0.99:
                # A power cap really caps clocks — keeps the frozen contract
                # invariant: throttle_reasons non-empty <=> sm_clock < base.
                reasons.append("SW_POWER_CAP")
                power = min(power, prof.tdp_w)
                sm_clock = min(sm_clock, int(prof.base_sm_mhz * 0.97))

            xid = ecc_v = 0
            if load > 0:
                stress = 1.0 + 3.0 * max(0.0, (temp - prof.throttle_temp) / 10.0)
                weight = self._node_instability[node.sn] * stress * (dt / 30.0)
                if _hash_unit(self.seed, "xid", g, t) < _XID_RATE_PER_TICK * weight:
                    xid = 1
                if _hash_unit(self.seed, "ecc", g, t) < _ECC_RATE_PER_TICK * weight * (0.2 + 0.8 * util):
                    ecc_v = 1
                    self._ecc_aggregate[g] = self._ecc_aggregate.get(g, 0) + 1

            sample = GpuTelemetrySample(
                gpu_id=g, node_sn=node.sn, rack_id=self.topology.rack_of[node.sn],
                model=node.model, t=t, util=util, temp_c=temp, power_w=power,
                sm_clock_mhz=sm_clock, mem_clock_mhz=prof.mem_clock_mhz,
                mem_used_mib=int(prof.mem_total_mib * util * 0.85),
                throttle_reasons=tuple(reasons), xid_errors=xid,
                ecc_errors={"volatile": ecc_v, "aggregate": self._ecc_aggregate.get(g, 0)},
            )
            self._last[g] = sample
            if load > 0 or temp > prof.idle_temp + 0.5 or reasons or xid or ecc_v:
                samples.append(sample)
        return samples

    def warm_start(self, t: int, state: ClusterState) -> None:
        """After a seek: initialize each loaded GPU's temp according to how long
        its load has actually been in place (the replayer's load_changed_t).
        A GPU packed hours ago is at steady state; one packed 90 seconds ago is
        still climbing. Honest thermal churn — median job lifetime is ~10 min,
        so most of a busy rack is perpetually mid-climb, and the rack mean only
        crosses the throttle line when a burst piles on and STAYS."""
        self.reset()
        self.t = t
        node_load = self._node_loads(state)
        for g in sorted(state.active_gpus):
            _, target = self._util_and_target(g, t, state, node_load)
            prof = self._profile_of[g]
            age = max(t - state.load_changed_t.get(g, t), 0)
            progress = 1.0 - math.exp(-age / TEMP_TAU_S)
            self._temps[g] = prof.idle_temp + (target - prof.idle_temp) * progress
        # dt == 0 emission: populates _last (so sample() is honest immediately)
        # without moving temps or drawing any XID/ECC events.
        self.step(t, state)

    # --- TelemetrySource interface (DESIGN §2.3 / §4) --------------------------
    def sample(self, gpu_id: str, t: int) -> GpuTelemetrySample:
        """Sample as of the last completed tick. `t` is advisory — the returned
        sample's own `.t` says when it was computed. A GPU absent from the last
        tick is genuinely idle at its last known (or ambient) temperature."""
        got = self._last.get(gpu_id)
        if got is not None:
            return got
        node = self._node_of[gpu_id]
        prof = self._profile_of[gpu_id]
        return GpuTelemetrySample(
            gpu_id=gpu_id, node_sn=node.sn, rack_id=self.topology.rack_of[node.sn],
            model=node.model, t=self.t, util=0.0,
            temp_c=self._temps.get(gpu_id, prof.idle_temp),
            power_w=prof.idle_w, sm_clock_mhz=prof.base_sm_mhz,
            mem_clock_mhz=prof.mem_clock_mhz, mem_used_mib=0,
        )
