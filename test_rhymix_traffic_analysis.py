from datetime import date

from rhymix_traffic_analysis import summarize_rows


def test_summarize_rows_keeps_raw_ips_out_and_builds_baseline():
    rows = [
        ["D", "20260801", "100", "160"],
        ["D", "20260802", "120", "180"],
        ["D", "20260803", "110", "170"],
        ["D", "20260804", "300", "720"],
        ["L", "20260804", "300", "299", "80", "60", "20", "5", "2", "10", "30"],
        ["H", "20260804", "12", "140"],
        ["H", "20260804", "18", "70"],
    ]
    result = summarize_rows(rows, date(2026, 8, 4), date(2026, 8, 4), date(2026, 8, 1))
    assert result["totals"]["unique_visitors"] == 300
    assert result["totals"]["suspicious_or_low_quality_estimate"] == 127
    assert result["baseline"]["daily_unique_visitor_median"] == 110
    assert result["top_hourly_spikes"][0]["hour"] == 12
    assert "ipaddress" not in str(result).lower()
