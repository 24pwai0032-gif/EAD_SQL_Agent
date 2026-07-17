"""Conditional visualisation: the model proposes, this code disposes.

Charts appear only when they genuinely aid understanding. The model is
prompted with guidance, but the hard rules live here and are enforced
against the ACTUAL result set — if the model asks for a chart on a single
value, the spec is dropped silently.
"""
ALLOWED_CHART_TYPES = {"bar", "line", "pie", "grouped_bar"}

MAX_CHART_ROWS = 25   # beyond this a chart is noise; the table carries it
MAX_PIE_SLICES = 6


def _is_numeric_column(rows, idx) -> bool:
    values = [r[idx] for r in rows if r[idx] is not None]
    return bool(values) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in values
    )


def _numeric_columns(columns, rows):
    return [c for i, c in enumerate(columns) if _is_numeric_column(rows, i)]


def should_chart(columns, rows) -> bool:
    """Hard gate: can this result set be charted at all?"""
    if not rows or not columns:
        return False
    if len(rows) < 2:                      # single value / single row: never
        return False
    if len(rows) > MAX_CHART_ROWS:         # table only, chart is noise
        return False
    if len(columns) == 1:                  # bare list of names/refs, or one measure
        return False
    if not _numeric_columns(columns, rows):  # no measure to plot
        return False
    return True


def validate_spec(spec, columns, rows):
    """Validate a model-proposed chart spec against the actual dataframe.

    Returns a cleaned spec dict or None (drop silently — never error the
    user because the model over-eagerly asked for a chart).
    """
    if not spec or not should_chart(columns, rows):
        return None

    chart_type = str(spec.get("type", "")).strip().lower()
    if chart_type not in ALLOWED_CHART_TYPES:
        return None

    x = spec.get("x")
    if x not in columns:
        return None

    # grouped_bar compares two measures across categories: y may be a
    # comma-separated pair of columns.
    y_cols = [c.strip() for c in str(spec.get("y", "")).split(",") if c.strip()]
    if not y_cols:
        return None
    numeric = set(_numeric_columns(columns, rows))
    for y in y_cols:
        if y not in columns or y not in numeric:
            return None
    if chart_type == "grouped_bar":
        if len(y_cols) < 2:
            return None
    elif len(y_cols) != 1:
        return None

    if chart_type == "pie":
        if len(rows) > MAX_PIE_SLICES:
            return None
        y_idx = columns.index(y_cols[0])
        if any(r[y_idx] is not None and r[y_idx] < 0 for r in rows):
            return None                    # negative slices are meaningless

    return {
        "type": chart_type,
        "x": x,
        "y": ",".join(y_cols) if len(y_cols) > 1 else y_cols[0],
        "title": str(spec.get("title", "")).strip() or "Result",
    }
