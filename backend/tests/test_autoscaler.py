"""Decision logic of infrastructure/autoscale/compose_autoscaler.py (design DS-23, P4-03)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "infrastructure/autoscale/compose_autoscaler.py"
_spec = importlib.util.spec_from_file_location("compose_autoscaler", _path)
assert _spec and _spec.loader
autoscaler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(autoscaler)


def test_parse_metric() -> None:
    text = "# HELP omnisend_workers_desired x\n# TYPE omnisend_workers_desired gauge\nomnisend_workers_desired 7\n"
    assert autoscaler.parse_desired(text) == 7
    assert autoscaler.parse_desired("omnisend_open_jobs 3\n") is None


def test_scale_up_now_down_after_cooldown() -> None:
    decide = autoscaler.decide
    assert decide(2, 6, None, 100.0, 300) == (6, None), "scale up immediately"
    target, since = decide(6, 2, None, 100.0, 300)
    assert (target, since) == (6, 100.0), "lower value starts the cooldown"
    assert decide(6, 2, since, 250.0, 300) == (6, 100.0), "still cooling down"
    assert decide(6, 2, since, 400.0, 300) == (2, None), "scale down once the lower value held"
    assert decide(6, 6, since, 200.0, 300) == (6, None), "demand came back: cooldown reset"
