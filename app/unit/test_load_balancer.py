"""
Unit tests for the round-robin load balancer.

TC-10: Round-robin distributes requests evenly across backends
TC-11: Unhealthy backends are skipped after threshold failures
"""

import pytest

from app.services.load_balancer import FAILURE_THRESHOLD, LoadBalancer


@pytest.fixture
def lb():
    """Fresh load balancer with two backends for each test."""
    return LoadBalancer(["http://backend-a:8001", "http://backend-b:8002"])


class TestRoundRobin:
    def test_alternates_between_two_backends(self, lb):
        """TC-10: 10 requests should be split 5/5 across two healthy backends."""
        results = [lb.get_next() for _ in range(10)]
        a_count = results.count("http://backend-a:8001")
        b_count = results.count("http://backend-b:8002")
        assert a_count == 5
        assert b_count == 5

    def test_returns_none_with_no_backends(self):
        """Empty backend list returns None."""
        empty_lb = LoadBalancer([])
        assert empty_lb.get_next() is None

    def test_single_backend_always_selected(self):
        """With one backend, every request goes to it."""
        single_lb = LoadBalancer(["http://only:8001"])
        for _ in range(5):
            assert single_lb.get_next() == "http://only:8001"

    def test_index_increments_across_calls(self, lb):
        """Internal index advances so distribution is truly round-robin."""
        first = lb.get_next()
        second = lb.get_next()
        assert first != second


class TestHealthTracking:
    def test_record_success_keeps_backend_healthy(self, lb):
        """Successful requests don't affect healthy status."""
        lb.record_success("http://backend-a:8001")
        assert "http://backend-a:8001" in lb.healthy

    def test_single_failure_does_not_remove_backend(self, lb):
        """One failure is below the threshold — backend stays in pool."""
        lb.record_failure("http://backend-a:8001")
        assert "http://backend-a:8001" in lb.healthy

    def test_backend_removed_after_threshold_failures(self, lb):
        """TC-11: After FAILURE_THRESHOLD consecutive failures, backend is removed."""
        for _ in range(FAILURE_THRESHOLD):
            lb.record_failure("http://backend-a:8001")
        assert "http://backend-a:8001" not in lb.healthy

    def test_traffic_routes_to_healthy_backend_only(self, lb):
        """TC-11: After backend-a fails, all requests go to backend-b."""
        for _ in range(FAILURE_THRESHOLD):
            lb.record_failure("http://backend-a:8001")

        results = {lb.get_next() for _ in range(10)}
        assert results == {"http://backend-b:8002"}

    def test_success_after_failure_resets_count(self, lb):
        """Recording a success clears the failure counter and restores health."""
        for _ in range(FAILURE_THRESHOLD - 1):
            lb.record_failure("http://backend-a:8001")

        lb.record_success("http://backend-a:8001")
        assert lb._failure_counts["http://backend-a:8001"] == 0
        assert "http://backend-a:8001" in lb.healthy

    def test_fallback_to_all_backends_when_all_unhealthy(self, lb):
        """
        If ALL backends are unhealthy, the LB falls back to the full list
        rather than returning None — better to try than fail every request.
        """
        for backend in lb.all_backends:
            for _ in range(FAILURE_THRESHOLD):
                lb.record_failure(backend)

        assert len(lb.healthy) == 0
        # Should still return a backend (not None)
        assert lb.get_next() is not None

    def test_status_reflects_health_state(self, lb):
        """status() correctly reports healthy/unhealthy state."""
        for _ in range(FAILURE_THRESHOLD):
            lb.record_failure("http://backend-a:8001")

        status = lb.status()
        backend_map = {b["url"]: b for b in status["backends"]}

        assert backend_map["http://backend-a:8001"]["healthy"] is False
        assert backend_map["http://backend-a:8001"]["failure_count"] == FAILURE_THRESHOLD
        assert backend_map["http://backend-b:8002"]["healthy"] is True