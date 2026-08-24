import datetime as dt

from copilot.chart import chart_descriptor, chart_title


def ts(day: int) -> dt.date:
    return dt.date(2026, 1, day)


def test_timeseries_becomes_a_line():
    d = chart_descriptor(
        ["stat_date", "gmv_inr"],
        [[ts(1), 100.0], [ts(2), 150.0], [ts(3), 130.0]],
    )
    assert d == {"type": "line", "x": "stat_date", "y": ["gmv_inr"]}
    assert "over time" in (chart_title(d, "gmv by day") or "")


def test_low_cardinality_category_becomes_a_bar():
    rows = [["upi", 10], ["cod", 5], ["card", 3]]
    d = chart_descriptor(["payment_mode", "orders"], rows)
    assert d == {"type": "bar", "x": "payment_mode", "y": ["orders"]}


def test_high_cardinality_text_refuses_to_be_a_bar():
    rows = [[f"customer-{i}", i] for i in range(40)]
    assert chart_descriptor(["full_name", "orders"], rows) is None


def test_two_numerics_become_scatter():
    d = chart_descriptor(
        ["distance_km", "tip_inr"], [[1.2, 5], [3.4, 12], [0.4, 0]]
    )
    assert d and d["type"] == "scatter"


def test_no_chart_for_pure_ids():
    assert chart_descriptor(["order_id"], [[1], [2]]) is None
    assert chart_descriptor([], []) is None


def test_null_leading_columns_still_classify():
    d = chart_descriptor(["delivered_at", "tips"], [[None, None], [ts(2), 7]])
    assert d and d["type"] == "line"


def test_title_variants():
    assert "by" in chart_title({"type": "bar", "x": "city_name", "y": ["gmv"]}, "")
    assert "against" in chart_title({"type": "scatter", "x": "a", "y": ["b"]}, "")
    assert chart_title(None, "") is None
