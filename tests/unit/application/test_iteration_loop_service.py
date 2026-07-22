"""IterationLoopService unit tests."""


from regent.application.iteration_loop_service import IterationLoopService


def test_iteration_loop_service_can_be_created() -> None:
    """IterationLoopService can be instantiated with sessions."""
    service = IterationLoopService(sessions=None)
    assert service is not None


def test_iteration_loop_service_has_bind_default_metrics() -> None:
    """IterationLoopService has bind_default_metrics method."""
    service = IterationLoopService(sessions=None)
    assert hasattr(service, "bind_default_metrics")
    assert callable(service.bind_default_metrics)


def test_iteration_loop_service_has_handle_revise() -> None:
    """IterationLoopService has handle_revise method."""
    service = IterationLoopService(sessions=None)
    assert hasattr(service, "handle_revise")
    assert callable(service.handle_revise)
