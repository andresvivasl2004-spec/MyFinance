"""
interactive.py — Altair (Vega-Lite) versions of the time-series charts.

Why these exist alongside charts.py
-----------------------------------
The matplotlib charts in charts.py are rendered on the server into a PNG: the
server spends ~200ms per chart drawing pixels, ships a ~77KB image, and what
you get back is a picture. You cannot ask a picture what your net worth was in
March 2025 — you have to eyeball it against the axis.

These build a Vega-Lite spec instead. The server sends ~50 rows of JSON and the
browser does the drawing, which moves the render off the server entirely and,
more importantly, makes the chart answer questions: hover any month to read the
exact figures, click the legend to isolate a series, drag to zoom into a period.

charts.py's matplotlib versions are deliberately kept — analysis.ipynb still
uses them, and they remain the right tool for a static export.

Altair ships as a hard dependency of Streamlit, so this adds nothing to install.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from .style import (
    DARK_BG, PANEL_BG, GRID_COL, TEXT_COL,
    ACCENT3, ACCENT5, CAT_COLORS, ASSET_CLASS_COLORS,
)

# Matches the matplotlib chart so the two don't look like different apps.
CASH_COL, INVEST_COL, NET_COL = "#3fb950", "#58a6ff", "#f0f6fc"

# d3-format (which Vega-Lite uses) has no syntax for a literal currency prefix:
# "€,.0f" is rejected outright as an invalid format, and the resulting error kills
# the whole mark's render — which is exactly how it failed, the areas silently
# vanishing while the net-worth line still drew. The euro sign goes in the label
# text instead, and the number gets a plain format.
_EURO = ",.0f"


def _theme(chart: alt.Chart, legend_orient: str = "top-left",
           legend_direction: str = "horizontal", legend_columns=None) -> alt.Chart:
    """
    Apply the app's dark palette. Streamlit's own Altair theme is bypassed
    (theme=None at the call site) so these match the surrounding panels.

    legend_orient matters more than it sounds: a 19-category legend floated over
    the top-left corner of a chart covers the data it is meant to explain, so the
    busy charts push theirs underneath instead.
    """
    legend_kwargs = dict(
        labelColor=TEXT_COL, titleColor=TEXT_COL,
        labelFontSize=11, titleFontSize=11,
        orient=legend_orient, direction=legend_direction,
        fillColor=PANEL_BG, strokeColor=GRID_COL, padding=8, cornerRadius=4,
    )
    if legend_columns is not None:
        legend_kwargs["columns"] = legend_columns
    if legend_orient == "bottom":
        # floating-panel styling looks wrong once it is no longer floating
        legend_kwargs.pop("fillColor"); legend_kwargs.pop("strokeColor")
    return (
        chart
        .configure(background=DARK_BG)
        .configure_view(fill=PANEL_BG, stroke=GRID_COL, strokeWidth=1)
        .configure_axis(
            labelColor=TEXT_COL, titleColor=TEXT_COL,
            gridColor=GRID_COL, gridOpacity=0.35,
            domainColor=GRID_COL, tickColor=GRID_COL,
            labelFontSize=11, titleFontSize=12,
        )
        .configure_legend(**legend_kwargs)
        .configure_title(color=TEXT_COL, fontSize=15, anchor="start")
    )


# Bars sit on a month band rather than a continuous date axis: "yearmonth"
# makes Vega-Lite treat each month as a discrete slot (so bars get a sensible
# width automatically instead of being hair-thin or overlapping), and
# labelOverlap thins the tick labels as history grows rather than smearing them
# together — the same problem _month_tick_interval solves by hand in charts.py,
# except here the browser recalculates it every time you resize the window.
def _month_x(title=None):
    return alt.X("month:T", timeUnit="yearmonth", title=title,
                 axis=alt.Axis(format="%b %Y", labelOverlap="greedy", labelAngle=0))


def _hover_layers(wide: pd.DataFrame, tooltip_fields, y_max_field: str):
    """
    The shared hover behaviour: an invisible full-height rule that follows the
    pointer to the nearest month, turning visible and carrying a tooltip with
    EVERY series' value at that month — not just whichever band the cursor
    happens to be over, which is what a per-mark tooltip would give you and is
    almost never the question you're asking.
    """
    hover = alt.selection_point(
        fields=["month"], nearest=True, on="pointerover",
        empty=False, clear="pointerout",
    )
    base = alt.Chart(wide).encode(x=alt.X("month:T"))
    # transparent wide-catch layer so the whole plot area is hoverable
    selectors = base.mark_rule(opacity=0).encode(
        tooltip=tooltip_fields,
    ).add_params(hover)
    rule = base.mark_rule(color=TEXT_COL, strokeWidth=1).encode(
        opacity=alt.condition(hover, alt.value(0.55), alt.value(0)),
    )
    dot = base.mark_point(color=NET_COL, size=60, filled=True).encode(
        y=alt.Y(f"{y_max_field}:Q"),
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
    )
    return selectors, rule, dot


def net_worth_chart(timeline: list[dict], height: int = 420):
    """
    Net worth over time — cash and invested stacked, total traced on top.

    Interactive additions over the static version: hover any month for the exact
    cash / invested / net-worth figures, click a legend entry to isolate that
    band, and drag horizontally to zoom into a period (double-click resets).
    """
    if not timeline:
        return None

    long = pd.DataFrame(
        [{"month": t["month"], "series": s, "value": t[k]}
         for t in timeline for s, k in (("Cash", "cash"), ("Invested", "invested"))]
    )
    long["month"] = pd.to_datetime(long["month"], format="%Y-%m")

    wide = pd.DataFrame(timeline)
    wide["month"] = pd.to_datetime(wide["month"], format="%Y-%m")

    legend_sel = alt.selection_point(fields=["series"], bind="legend")
    zoom = alt.selection_interval(bind="scales", encodings=["x"])

    area = (
        alt.Chart(long)
        .mark_area(opacity=0.75, line=False)
        .encode(
            x=alt.X("month:T", title=None, axis=alt.Axis(format="%b %Y")),
            y=alt.Y("value:Q", title="€", stack="zero",
                    axis=alt.Axis(format="~s")),
            color=alt.Color(
                "series:N", title=None,
                scale=alt.Scale(domain=["Cash", "Invested"],
                                range=[CASH_COL, INVEST_COL]),
            ),
            opacity=alt.condition(legend_sel, alt.value(0.75), alt.value(0.12)),
        )
        .add_params(legend_sel, zoom)
    )

    net_line = (
        alt.Chart(wide)
        .mark_line(color=NET_COL, strokeWidth=2.4)
        .encode(x=alt.X("month:T"), y=alt.Y("net_worth:Q"))
    )

    selectors, rule, dot = _hover_layers(
        wide,
        [
            alt.Tooltip("month:T", title="Month", format="%B %Y"),
            alt.Tooltip("net_worth:Q", title="Net worth (€)", format=_EURO),
            alt.Tooltip("cash:Q", title="Cash (€)", format=_EURO),
            alt.Tooltip("invested:Q", title="Invested (€)", format=_EURO),
        ],
        "net_worth",
    )

    chart = (
        alt.layer(area, net_line, selectors, rule, dot)
        .properties(height=height, title="Net Worth Over Time")
        .resolve_scale(y="shared")
    )
    return _theme(chart)


def allocation_chart(timeline: list[dict], height: int = 400, as_share: bool = False):
    """
    Asset-class mix over time.

    as_share=True normalises each month to 100%, which answers "is my mix
    drifting?" rather than "how much do I hold?" — the static chart could only
    ever show one of those, and only by being rebuilt.
    """
    if not timeline or all(t["total"] <= 0 for t in timeline):
        return None

    CLASS_ORDER = ["Equity", "Fixed income", "Commodities", "Unspecified"]
    present = [c for c in CLASS_ORDER
               if any(t["by_class"].get(c, 0) > 0 for t in timeline)]

    long = pd.DataFrame(
        [{"month": t["month"], "series": c, "value": t["by_class"].get(c, 0.0)}
         for t in timeline for c in present]
    )
    long["month"] = pd.to_datetime(long["month"], format="%Y-%m")

    wide = pd.DataFrame(
        [{"month": t["month"], "total": t["total"],
          **{c: t["by_class"].get(c, 0.0) for c in present}}
         for t in timeline]
    )
    wide["month"] = pd.to_datetime(wide["month"], format="%Y-%m")

    legend_sel = alt.selection_point(fields=["series"], bind="legend")
    zoom = alt.selection_interval(bind="scales", encodings=["x"])

    stack = "normalize" if as_share else "zero"
    y_axis = alt.Axis(format="%") if as_share else alt.Axis(format="~s")
    y_title = "share of invested" if as_share else "€ invested"

    area = (
        alt.Chart(long)
        .mark_area(opacity=0.85, line=False)
        .encode(
            x=alt.X("month:T", title=None, axis=alt.Axis(format="%b %Y")),
            y=alt.Y("value:Q", title=y_title, stack=stack, axis=y_axis),
            color=alt.Color(
                "series:N", title=None,
                scale=alt.Scale(domain=present,
                                range=[ASSET_CLASS_COLORS.get(c, "#8b949e") for c in present]),
            ),
            opacity=alt.condition(legend_sel, alt.value(0.85), alt.value(0.12)),
        )
        .add_params(legend_sel, zoom)
    )

    tips = [alt.Tooltip("month:T", title="Month", format="%B %Y"),
            alt.Tooltip("total:Q", title="Total invested (€)", format=_EURO)]
    tips += [alt.Tooltip(f"{c}:Q", title=f"{c} (€)", format=_EURO) for c in present]
    selectors, rule, _dot = _hover_layers(wide, tips, "total")

    title = "Asset Allocation Over Time" + (" — share of portfolio" if as_share else "")
    chart = (
        alt.layer(area, selectors, rule)
        .properties(height=height, title=title)
    )
    return _theme(chart)


# ══════════════════════════════════════════════════════════════════════════════
# The six spending panels
# ══════════════════════════════════════════════════════════════════════════════
# charts.py draws these as one 22x30in matplotlib figure — a single 221KB PNG,
# ~1.2s of server time, and six panels welded together so you cannot look at one
# without the others. Here each panel is its own Vega-Lite chart. The page lays
# them out in the same arrangement, but they are now independent, individually
# hoverable, and drawn by the browser.
#
# Every one of them sends only the rows that actually carry a value — the same
# insight that made the matplotlib version faster (two thirds of the bars in the
# stacked panel are zero) also keeps the JSON payload small here.

CAT_ORDER = [
    "Rent", "Investments", "Education", "Entertainment",
    "Dining & Food", "Groceries", "Electricity", "Gas",
    "Transport", "Health & Beauty", "Gym", "Personal Care", "Water",
    "Phone", "Social & Gifts", "Fees & Tax", "Shopping", "Other",
]


def _cat_scale(cats):
    return alt.Scale(domain=list(cats),
                     range=[CAT_COLORS.get(c, "#484f58") for c in cats])


def monthly_by_category_chart(d: dict, height: int = 380):
    """
    Panel 1 — monthly spending stacked by category.

    This is the panel that most needed to stop being an image. Nineteen stacked
    categories is exactly the case where you can see a band but cannot tell what
    it is or what it is worth: hovering now names the category, its amount and
    its share of that month, and clicking the legend isolates one category
    across the whole history.
    """
    months = d["all_months_str"]
    by_cat = d["monthly_by_cat"]
    if not months:
        return None

    totals = {m: sum(by_cat.get(m, {}).values()) for m in months}
    rows = [
        {"month": m, "category": c, "amount": v,
         "share": (v / totals[m]) if totals[m] else 0.0, "total": totals[m]}
        for m in months
        for c, v in ((c, by_cat.get(m, {}).get(c, 0.0)) for c in CAT_ORDER)
        if v            # zero categories carry no information and no pixels
    ]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
    present = [c for c in CAT_ORDER if c in set(df["category"])]

    legend_sel = alt.selection_point(fields=["category"], bind="legend")

    bars = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=_month_x(),
            y=alt.Y("amount:Q", title="€", stack="zero", axis=alt.Axis(format="~s")),
            color=alt.Color("category:N", title=None, scale=_cat_scale(present),
                            sort=present),
            order=alt.Order("color_category_sort_index:Q"),
            opacity=alt.condition(legend_sel, alt.value(0.92), alt.value(0.10)),
            tooltip=[
                alt.Tooltip("month:T", title="Month", format="%B %Y"),
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("amount:Q", title="Amount (€)", format=_EURO),
                alt.Tooltip("share:Q", title="Share of month", format=".1%"),
                alt.Tooltip("total:Q", title="Month total (€)", format=_EURO),
            ],
        )
        .add_params(legend_sel)
    )

    avg = d.get("avg_last12")
    layers = [bars]
    if avg:
        ref = (
            alt.Chart(pd.DataFrame({"y": [avg]}))
            .mark_rule(color=ACCENT3, strokeDash=[6, 4], strokeWidth=1.8)
            .encode(y="y:Q",
                    tooltip=[alt.Tooltip("y:Q", title="Avg last 12mo (€)", format=_EURO)])
        )
        layers.append(ref)

    chart = (alt.layer(*layers)
             .properties(height=height, title="Monthly spending by category")
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="bottom", legend_columns=6)


def _stacked_month_chart(pairs, colors, title, y_title, height,
                         avg=None, avg_label="Avg"):
    """Shared builder for the grocery and utilities panels: a stacked bar over
    months, with an optional dashed average line. `pairs` is [(name, {month: v})]."""
    rows = [{"month": m, "series": name, "amount": v}
            for name, monthly in pairs
            for m, v in monthly.items() if v]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
    present = [n for n, _ in pairs if n in set(df["series"])]
    month_totals = df.groupby("month", as_index=False)["amount"].sum() \
                     .rename(columns={"amount": "total"})
    df = df.merge(month_totals, on="month")

    legend_sel = alt.selection_point(fields=["series"], bind="legend")
    bars = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=_month_x(),
            y=alt.Y("amount:Q", title=y_title, stack="zero", axis=alt.Axis(format="~s")),
            color=alt.Color("series:N", title=None, sort=present,
                            scale=alt.Scale(domain=present,
                                            range=[colors[n] for n in present])),
            opacity=alt.condition(legend_sel, alt.value(0.92), alt.value(0.10)),
            tooltip=[
                alt.Tooltip("month:T", title="Month", format="%B %Y"),
                alt.Tooltip("series:N", title="Source"),
                alt.Tooltip("amount:Q", title="Amount (€)", format=_EURO),
                alt.Tooltip("total:Q", title="Month total (€)", format=_EURO),
            ],
        )
        .add_params(legend_sel)
    )
    layers = [bars]
    if avg:
        layers.append(
            alt.Chart(pd.DataFrame({"y": [avg]}))
            .mark_rule(color=ACCENT3, strokeDash=[6, 4], strokeWidth=1.8)
            .encode(y="y:Q",
                    tooltip=[alt.Tooltip("y:Q", title=f"{avg_label} (€)", format=_EURO)])
        )
    chart = (alt.layer(*layers).properties(height=height, title=title)
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="bottom", legend_columns=4)


def grocery_chart(d: dict, height: int = 300):
    """Panel 2 — grocery spend per month, split by shop."""
    detail = d["grocery_detail"]
    order = ["LIDL", "Mercadona", "Alcampo", "Simply", "Fruteria", "Obrador", "Other Grocery"]
    colors = dict(zip(order, ["#56d364", "#3fb950", "#26a641", "#1a7f37",
                              "#6bc8f5", "#79c0ff", "#8b949e"]))
    pairs = [(name, {m: vals.get(name, 0.0) for m, vals in detail.items()})
             for name in order]
    monthly = d["monthly_grocery"]
    active = [v for v in monthly.values() if v > 0]
    avg = sum(monthly.values()) / len(active) if active else None
    return _stacked_month_chart(pairs, colors, "Grocery spend per month", "€",
                                height, avg=avg, avg_label="Average month")


def utilities_chart(d: dict, height: int = 300):
    """Panel 4 — electricity / gas / water / phone per month."""
    order = ["Electricity", "Gas", "Water", "Phone"]
    pairs = [
        ("Electricity", d["monthly_electricity"]), ("Gas", d["monthly_gas"]),
        ("Water", d["monthly_water"]), ("Phone", d["monthly_phone"]),
    ]
    colors = {n: CAT_COLORS[n] for n in order}
    return _stacked_month_chart(pairs, colors, "Utilities per month", "€", height)


def dining_chart(d: dict, height: int = 300):
    """Panel 3 — dining & food per month, against its own average."""
    monthly = d["monthly_dining"]
    rows = [{"month": m, "amount": v} for m, v in monthly.items() if v]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
    active = [v for v in monthly.values() if v > 0]
    avg = sum(monthly.values()) / len(active) if active else 0

    bars = (
        alt.Chart(df)
        .mark_bar(color=ACCENT5, opacity=0.9)
        .encode(
            x=_month_x(),
            y=alt.Y("amount:Q", title="€", axis=alt.Axis(format="~s")),
            tooltip=[alt.Tooltip("month:T", title="Month", format="%B %Y"),
                     alt.Tooltip("amount:Q", title="Spent (€)", format=_EURO)],
        )
    )
    ref = (alt.Chart(pd.DataFrame({"y": [avg]}))
           .mark_rule(color=ACCENT3, strokeDash=[6, 4], strokeWidth=1.8)
           .encode(y="y:Q",
                   tooltip=[alt.Tooltip("y:Q", title="Average month (€)", format=_EURO)]))
    chart = (alt.layer(bars, ref).properties(height=height, title="Dining & food per month")
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="bottom")


def category_totals_chart(d: dict, height: int = 340):
    """
    Panel 5 — total spend per category, ranked.

    Direct labels stay on this one. It is read at a glance as a ranking, so the
    numbers belong on the bars; the tooltip adds each category's share of total
    spending, which the static version had no room for.
    """
    totals = {c: v for c, v in d["cat_totals"].items() if c != "Self-transfer" and v}
    if not totals:
        return None
    grand = sum(totals.values())
    df = pd.DataFrame([{"category": c, "total": v, "share": v / grand}
                       for c, v in totals.items()]).sort_values("total", ascending=False)

    # An explicit domain, not sort="-x": inside a layered chart (bars + labels)
    # the relative sort silently falls back to alphabetical, which turns a
    # ranking into an index. The order is computed here and stated outright.
    order = list(df["category"])
    base = alt.Chart(df).encode(
        y=alt.Y("category:N", sort=order, title=None),
        x=alt.X("total:Q", title="€ total", axis=alt.Axis(format="~s")),
        tooltip=[alt.Tooltip("category:N", title="Category"),
                 alt.Tooltip("total:Q", title="Total (€)", format=_EURO),
                 alt.Tooltip("share:Q", title="Share of spending", format=".1%")],
    )
    bars = base.mark_bar(opacity=0.92).encode(
        color=alt.Color("category:N", scale=_cat_scale(order), legend=None))
    labels = base.mark_text(align="left", dx=4, color=TEXT_COL, fontSize=10).encode(
        text=alt.Text("total:Q", format=_EURO))
    chart = (alt.layer(bars, labels)
             .properties(height=height, title="Total spending by category"))
    return _theme(chart)


def income_vs_spending_chart(d: dict, height: int = 320):
    """
    Panel 6 — monthly income against total spending.

    The matplotlib version drew both series as bars at the same x with alpha,
    so they sat on top of each other and the taller one hid the shorter. Side by
    side is simply readable, and the tooltip carries the month's net.
    """
    months = d["all_months_str"]
    by_cat = d["monthly_by_cat"]
    income = d["monthly_income"]
    spend = {m: sum(by_cat.get(m, {}).values()) for m in months}
    rows = []
    for m in months:
        i, sp = income.get(m, 0.0), spend.get(m, 0.0)
        for name, v in (("Income", i), ("Spending", sp)):
            if v:
                rows.append({"month": m, "series": name, "amount": v, "net": i - sp})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")

    legend_sel = alt.selection_point(fields=["series"], bind="legend")
    chart = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=_month_x(),
            xOffset=alt.XOffset("series:N", sort=["Income", "Spending"]),
            y=alt.Y("amount:Q", title="€", axis=alt.Axis(format="~s")),
            color=alt.Color("series:N", title=None,
                            scale=alt.Scale(domain=["Income", "Spending"],
                                            range=["#56d364", "#ff7b72"])),
            opacity=alt.condition(legend_sel, alt.value(0.85), alt.value(0.12)),
            tooltip=[alt.Tooltip("month:T", title="Month", format="%B %Y"),
                     alt.Tooltip("series:N", title=""),
                     alt.Tooltip("amount:Q", title="Amount (€)", format=_EURO),
                     alt.Tooltip("net:Q", title="Net that month (€)", format=_EURO)],
        )
        .add_params(legend_sel)
        .properties(height=height, title="Monthly income vs total spending")
    )
    return _theme(chart, legend_orient="bottom")


def spending_specs(d: dict) -> dict:
    """All six panels as Vega-Lite spec dicts, ready to cache and render."""
    builders = {
        "by_category":  monthly_by_category_chart,
        "grocery":      grocery_chart,
        "dining":       dining_chart,
        "utilities":    utilities_chart,
        "cat_totals":   category_totals_chart,
        "income_spend": income_vs_spending_chart,
    }
    out = {}
    for key, build in builders.items():
        chart = build(d)
        out[key] = chart.to_dict() if chart is not None else None
    return out
