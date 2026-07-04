"""GPU Cluster Sentinel — data, replay, and telemetry foundation (M0–M2).

Layers owned here:
  - sentinel.data       CSV loading, Node/Pod models, derived rack topology, stats gate
  - sentinel.replay     event-time stream replayer, cluster state, bin-packing placement
  - sentinel.telemetry  synthesized DCGM-style telemetry, rack aggregates, frame contract
  - sentinel.engine     wires replayer + telemetry into per-tick frames

See CONTRACTS.md for the interfaces exposed to prediction (M3), agent (M4),
and dashboard (M5).
"""

__version__ = "0.1.0"
