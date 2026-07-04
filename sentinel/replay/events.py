"""Event-time replay queue (DESIGN §2.2).

Each pod contributes up to three events:
  POD_CREATED   at creation_time   (enters the pending queue)
  POD_SCHEDULED at scheduled_time  (placed; consumes gpu_milli)  — absent for 897 Pending pods
  POD_DELETED   at deletion_time   (frees resources / leaves queue) — absent for 34 censored pods

Same-timestamp ordering: CREATED < DELETED < SCHEDULED, then pod name.
A pod created and deleted the same second (openb-pod-7285) exists before it
dies; frees apply before placements at the same instant; and a pod created
and scheduled the same second (2,046 of them) is created first. Deterministic.
"""
from __future__ import annotations

from typing import NamedTuple

from sentinel.data.models import Pod

POD_CREATED = "POD_CREATED"
POD_SCHEDULED = "POD_SCHEDULED"
POD_DELETED = "POD_DELETED"

_KIND_ORDER = {POD_CREATED: 0, POD_DELETED: 1, POD_SCHEDULED: 2}


class Event(NamedTuple):
    t: int
    kind_order: int
    pod_name: str
    kind: str


def build_event_queue(pods: list[Pod]) -> list[Event]:
    events = []
    for p in pods:
        events.append(Event(p.creation_time, _KIND_ORDER[POD_CREATED], p.name, POD_CREATED))
        if p.scheduled_time is not None:
            events.append(Event(p.scheduled_time, _KIND_ORDER[POD_SCHEDULED], p.name, POD_SCHEDULED))
        if not p.censored:
            events.append(Event(p.deletion_time, _KIND_ORDER[POD_DELETED], p.name, POD_DELETED))
    events.sort()
    return events
