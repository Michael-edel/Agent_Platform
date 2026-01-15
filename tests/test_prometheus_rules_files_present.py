from __future__ import annotations

from pathlib import Path


def test_prometheus_slo_rules_files_present():
    root = Path(__file__).resolve().parents[1]

    rec = root / "docs" / "alerts" / "prometheus_billing_slo_recording_rules.yml"
    assert rec.exists()
    rec_text = rec.read_text(encoding="utf-8", errors="ignore")
    assert "billing:sli_time_to_paid_good_rate5m" in rec_text
    assert "billing:sli_time_to_paid_total_rate5m" in rec_text
    assert "billing:sli_time_to_paid_good_rate30m" in rec_text
    assert "billing:sli_time_to_paid_total_rate30m" in rec_text
    assert "billing:sli_time_to_paid_good_rate1h" in rec_text
    assert "billing:sli_time_to_paid_total_rate1h" in rec_text
    assert "billing:sli_time_to_paid_good_rate6h" in rec_text
    assert "billing:sli_time_to_paid_total_rate6h" in rec_text

    sla = root / "docs" / "alerts" / "prometheus_billing_sla_rules_v2.yml"
    assert sla.exists()
    sla_text = sla.read_text(encoding="utf-8", errors="ignore")
    assert "BillingSLOTimeToPaidFastBurn" in sla_text
    assert "BillingSLOTimeToPaidSlowBurn" in sla_text

