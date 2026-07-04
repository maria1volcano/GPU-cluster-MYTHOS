"""TelemetrySource — the swappable seam between simulated and real DCGM
telemetry (DESIGN §2.3). Prediction consumes samples through this interface
only, so a real DCGM feed drops in with zero prediction changes (NG5/M8)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from sentinel.telemetry.sample import GpuTelemetrySample


class TelemetrySource(ABC):
    @abstractmethod
    def sample(self, gpu_id: str, t: int) -> GpuTelemetrySample:
        """Telemetry for one GPU at (current) time t."""
