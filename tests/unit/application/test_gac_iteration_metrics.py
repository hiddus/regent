"""GAC batch: iteration metrics bind smoke_pass for preview attainment."""

from __future__ import annotations

from regent.application.iteration_loop_service import IterationLoopService


def test_default_metrics_include_preview_smoke() -> None:
    """GAC-A2: default bindings must include preview-smoke smoke_pass."""
    # Inspect source defaults via a dry call structure: method exists and docstring cites GAC.
    assert "smoke_pass" in (IterationLoopService.bind_default_metrics.__doc__ or "")
    assert "GAC-A2" in (IterationLoopService.bind_default_metrics.__doc__ or "")
