"""DcgmTelemetrySource — stub documenting the real-DCGM swap-in (M8+, NG5).

When real hardware arrives, implement `sample()` against dcgmi / the DCGM
exporter and construct GpuTelemetrySample from these fields — prediction and
dashboard need zero changes:

  DCGM field (dcgmi dmon / exporter)        -> GpuTelemetrySample
  ----------------------------------------------------------------
  DCGM_FI_DEV_GPU_UTIL                      -> util (/100)
  DCGM_FI_DEV_GPU_TEMP                      -> temp_c
  DCGM_FI_DEV_POWER_USAGE                   -> power_w
  DCGM_FI_DEV_SM_CLOCK                      -> sm_clock_mhz
  DCGM_FI_DEV_MEM_CLOCK                     -> mem_clock_mhz
  DCGM_FI_DEV_FB_USED                       -> mem_used_mib
  DCGM_FI_DEV_CLOCK_THROTTLE_REASONS        -> throttle_reasons (decode bitmask)
  DCGM_FI_DEV_XID_ERRORS                    -> xid_errors
  DCGM_FI_DEV_ECC_SBE_VOL_TOTAL / _AGG_     -> ecc_errors {volatile, aggregate}

gpu_id/node_sn/rack_id come from the deployment's own inventory mapping.
"""
from __future__ import annotations

from sentinel.telemetry.sample import GpuTelemetrySample
from sentinel.telemetry.source import TelemetrySource


class DcgmTelemetrySource(TelemetrySource):
    def sample(self, gpu_id: str, t: int) -> GpuTelemetrySample:
        raise NotImplementedError(
            "Real DCGM feed is post-demo (M8+). See module docstring for the "
            "field mapping; SimTelemetrySource is the v1 source."
        )
