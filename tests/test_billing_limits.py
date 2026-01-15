"""Tests for plan limits enforcement."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def disable_metrics(monkeypatch):
    """Disable metrics before any import."""
    monkeypatch.setenv("METRICS_ENABLED", "false")


class TestCheckPlanLimit:
    """Tests for check_plan_limit function."""

    def test_no_plan_returns_allowed(self):
        """No plan for tenant = no limits = allowed."""
        from app.billing.limits import check_plan_limit
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        # No plan found
        mock_conn.execute.return_value.fetchone.return_value = None
        
        result = check_plan_limit(
            tenant_id="t1",
            metric="documents",
            increment=1,
            period="2026-01",
            engine=mock_engine,
        )
        
        assert result.allowed is True
        assert result.reason == "no_limit"

    def test_no_quota_for_metric_returns_allowed(self):
        """Metric not in quotas = allowed."""
        from app.billing.limits import check_plan_limit
        import json
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        # Plan found, but metric not in quotas
        mock_conn.execute.return_value.fetchone.side_effect = [
            ("pro",),  # plan_id
            (json.dumps({"other_metric": 100}),),  # quotas
        ]
        
        result = check_plan_limit(
            tenant_id="t1",
            metric="documents",
            increment=1,
            period="2026-01",
            engine=mock_engine,
        )
        
        assert result.allowed is True
        assert result.reason == "no_limit"

    def test_below_limit_returns_allowed(self):
        """Usage below limit = allowed."""
        from app.billing.limits import check_plan_limit
        import json
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        mock_conn.execute.return_value.fetchone.side_effect = [
            ("pro",),  # plan_id
            (json.dumps({"documents": 100}),),  # quotas
        ]
        mock_conn.execute.return_value.scalar.return_value = 50  # used
        
        result = check_plan_limit(
            tenant_id="t1",
            metric="documents",
            increment=10,
            period="2026-01",
            engine=mock_engine,
        )
        
        assert result.allowed is True
        assert result.limit == 100
        assert result.used == 50
        assert result.remaining == 50

    def test_exact_limit_returns_allowed(self):
        """Usage exactly at limit (no increment) = allowed."""
        from app.billing.limits import check_plan_limit
        import json
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        mock_conn.execute.return_value.fetchone.side_effect = [
            ("pro",),
            (json.dumps({"documents": 100}),),
        ]
        mock_conn.execute.return_value.scalar.return_value = 99
        
        result = check_plan_limit(
            tenant_id="t1",
            metric="documents",
            increment=1,  # 99 + 1 = 100 = limit (allowed)
            period="2026-01",
            engine=mock_engine,
        )
        
        assert result.allowed is True
        assert result.limit == 100
        assert result.used == 99

    def test_over_limit_returns_rejected(self):
        """Usage over limit = rejected."""
        from app.billing.limits import check_plan_limit
        import json
        
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        
        mock_conn.execute.return_value.fetchone.side_effect = [
            ("pro",),
            (json.dumps({"documents": 100}),),
        ]
        mock_conn.execute.return_value.scalar.return_value = 100  # already at limit
        
        result = check_plan_limit(
            tenant_id="t1",
            metric="documents",
            increment=1,  # 100 + 1 = 101 > 100 (rejected)
            period="2026-01",
            engine=mock_engine,
        )
        
        assert result.allowed is False
        assert result.reason == "limit_exceeded"
        assert result.limit == 100
        assert result.used == 100
        assert result.remaining == 0

    def test_db_error_returns_rejected_with_reason(self):
        """DB error = rejected with check_failed reason."""
        from app.billing.limits import check_plan_limit
        
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("DB down")
        
        result = check_plan_limit(
            tenant_id="t1",
            metric="documents",
            increment=1,
            period="2026-01",
            engine=mock_engine,
        )
        
        assert result.allowed is False
        assert result.reason == "check_failed"


class TestPlanLimitExceededError:
    """Tests for PlanLimitExceededError."""

    def test_error_has_attributes(self):
        """Error has expected attributes."""
        from cyberplat.billing_service import PlanLimitExceededError
        
        error = PlanLimitExceededError(
            metric="documents",
            limit=100,
            used=100,
        )
        
        assert error.metric == "documents"
        assert error.limit == 100
        assert error.used == 100
        assert "documents" in str(error)
        assert "100" in str(error)


class TestLimitCheckResult:
    """Tests for LimitCheckResult dataclass."""

    def test_result_has_fields(self):
        """Result has expected fields."""
        from app.billing.limits import LimitCheckResult
        
        result = LimitCheckResult(
            allowed=False,
            limit=100,
            used=100,
            remaining=0,
            reason="limit_exceeded",
        )
        
        assert result.allowed is False
        assert result.limit == 100
        assert result.used == 100
        assert result.remaining == 0
        assert result.reason == "limit_exceeded"
