from agent.charts import should_chart, validate_spec

CATS = ["sector", "usd"]
CAT_ROWS = [["Energy", 100.0], ["Health", 50.0], ["Transport", 30.0]]


def spec(**kw):
    base = {"type": "bar", "x": "sector", "y": "usd", "title": "t"}
    base.update(kw)
    return base


# --- should_chart hard rules ---------------------------------------------

def test_single_value_never_charts():
    assert not should_chart(["n"], [[42]])


def test_single_row_never_charts():
    assert not should_chart(CATS, [["Energy", 100.0]])


def test_too_many_rows_never_chart():
    rows = [[f"c{i}", float(i)] for i in range(26)]
    assert not should_chart(CATS, rows)


def test_bare_list_never_charts():
    assert not should_chart(["agreement_ref"], [["EAD/1"], ["EAD/2"], ["EAD/3"]])


def test_no_numeric_column_never_charts():
    assert not should_chart(["a", "b"], [["x", "y"], ["p", "q"]])


def test_reasonable_result_charts():
    assert should_chart(CATS, CAT_ROWS)


# --- validate_spec: the model proposes, code disposes ---------------------

def test_spec_on_single_value_dropped_silently():
    assert validate_spec(spec(), ["n"], [[42]]) is None


def test_spec_with_unknown_column_dropped():
    assert validate_spec(spec(x="nope"), CATS, CAT_ROWS) is None
    assert validate_spec(spec(y="nope"), CATS, CAT_ROWS) is None


def test_spec_with_non_numeric_y_dropped():
    assert validate_spec(spec(x="usd", y="sector"), CATS, CAT_ROWS) is None


def test_spec_with_bad_type_dropped():
    assert validate_spec(spec(type="scatter3d"), CATS, CAT_ROWS) is None


def test_pie_with_too_many_slices_dropped():
    rows = [[f"c{i}", float(i + 1)] for i in range(7)]
    assert validate_spec(spec(type="pie"), CATS, rows) is None


def test_grouped_bar_requires_two_measures():
    cols = ["fy", "principal", "interest"]
    rows = [["FY2024", 10.0, 5.0], ["FY2025", 12.0, 4.0]]
    ok = validate_spec(spec(type="grouped_bar", x="fy", y="principal,interest"), cols, rows)
    assert ok == {"type": "grouped_bar", "x": "fy", "y": "principal,interest", "title": "t"}
    assert validate_spec(spec(type="grouped_bar", x="fy", y="principal"), cols, rows) is None


def test_valid_bar_spec_passes_through():
    out = validate_spec(spec(), CATS, CAT_ROWS)
    assert out == {"type": "bar", "x": "sector", "y": "usd", "title": "t"}


def test_none_spec_is_none():
    assert validate_spec(None, CATS, CAT_ROWS) is None
