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

from datetime import date

import altair as alt
import pandas as pd

from .style import (
    DARK_BG, PANEL_BG, GRID_COL, TEXT_COL,
    ACCENT3, ACCENT5, CAT_COLORS, cat_color, ASSET_CLASS_COLORS,
    ACCOUNT_COLORS, FUND_COLORS,
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
        # Vega truncates legend labels at 200px by default, which clips fund
        # names to "GLOBAL EQUITY INDEX FU…" — a legend that can't be read is
        # worse than no legend.
        labelLimit=320,
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
def _month_x(title=None, order: list[str] | None = None):
    """
    The month axis. Temporal by default, which handles a long history well.

    Pass `order` (the month labels, in order) for a short fixed window and it
    switches to an ordinal axis instead. That is not cosmetic: on a temporal
    axis a month occupies the span from its own start to the next month's, so
    every bar is drawn to the RIGHT of its tick and the axis grows one extra
    tick past the end — over 53 months nobody notices, but over 8 it reads as
    if each bar belonged to the following month and as if there were a ninth.
    Neither `tickBand="extent"` nor `bandPosition` moves them; an ordinal scale
    centres each bar under its own label by construction.
    """
    if order is not None:
        # The domain is pinned to the whole window, not inferred from the rows.
        # A month with no spending has no rows at all, and without this it would
        # simply be missing from the axis — seven bars labelled Jan…Jun, Aug with
        # July silently closed up, hiding exactly the fact you would want to see.
        # Pinned, an empty month shows as an empty slot.
        return alt.X("label:N", title=title, sort=order,
                     scale=alt.Scale(domain=order),
                     axis=alt.Axis(labelAngle=0, labelOverlap="greedy"))
    return alt.X("month:T", timeUnit="yearmonth", title=title,
                 axis=alt.Axis(format="%b %Y", labelOverlap="greedy", labelAngle=0))


def _month_labels(window: list[str]) -> list[str]:
    """["2026-01", ...] -> ["Jan 2026", ...], the ordinal axis domain."""
    return [date(int(m[:4]), int(m[5:]), 1).strftime("%b %Y") for m in window]


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

# The preferred STACKING ORDER, biggest and most structural at the bottom, so
# the bands don't reshuffle from month to month. It is a preference, not a
# whitelist: this list used to be iterated directly, which meant any category
# missing from it — Travel, or anything added later in Settings — was silently
# dropped from the chart while still counting towards every total elsewhere in
# the app. Ordering now comes from here; membership comes from the data.
CAT_ORDER = [
    "Rent", "Investments", "Education", "Entertainment",
    "Dining & Food", "Groceries", "Electricity", "Natural Gas",
    "Transport and Gasoline", "Travel", "Health and Personal Care", "Gym", "Water",
    "Phone", "Social & Gifts", "Fees & Tax", "Shopping", "Other",
]


def order_categories(cats) -> list[str]:
    """CAT_ORDER first, then anything else alphabetically, with "Other" last —
    a catch-all bucket reads wrong anywhere but the end of the stack."""
    cats = set(cats)
    known = [c for c in CAT_ORDER if c in cats and c != "Other"]
    rest  = sorted(c for c in cats - set(CAT_ORDER))
    tail  = ["Other"] if "Other" in cats else []
    return known + rest + tail


def _cat_scale(cats):
    return alt.Scale(domain=list(cats), range=[cat_color(c) for c in cats])


def monthly_by_category_chart(d: dict, height: int = 380,
                              months: int | None = None, exclude=()):
    """
    Panel 1 — monthly spending stacked by category.

    This is the panel that most needed to stop being an image. Nineteen stacked
    categories is exactly the case where you can see a band but cannot tell what
    it is or what it is worth: hovering now names the category, its amount and
    its share of that month, and clicking the legend isolates one category
    across the whole history.

    `months=None` shows the whole history and compares each month against the
    app's headline twelve-month average, which is a considered statistic — it
    drops July and August, and any month flagged anomalous, so a summer abroad
    doesn't set the bar for a normal month.

    `months=N` narrows to the last N complete months and recomputes the average
    and σ over exactly the months on screen. That is the point of asking for a
    window: an average imported from a different period would describe months
    you can no longer see.
    """
    all_months = d["all_months_str"]
    by_cat = d["monthly_by_cat"]
    if not all_months:
        return None
    window = _recent_window(d, months, exclude) if months else None
    shown = window if window is not None else all_months
    if not shown:
        return None

    totals = {m: sum(by_cat.get(m, {}).values()) for m in shown}
    rows = [
        {"month": m, "category": c, "amount": v,
         "share": (v / totals[m]) if totals[m] else 0.0, "total": totals[m]}
        for m in shown
        for c, v in by_cat.get(m, {}).items()
        if v            # zero categories carry no information and no pixels
    ]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    order = _month_labels(window) if window else None
    if order:
        df["label"] = df["month"].map(dict(zip(window, order)))
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
    present = order_categories(df["category"])

    legend_sel = alt.selection_point(fields=["category"], bind="legend")

    bars = (
        alt.Chart(df)
        .mark_bar()
        .encode(
            x=_month_x(order=order),
            y=alt.Y("amount:Q", title="€", stack="zero", axis=alt.Axis(format="~s")),
            color=alt.Color("category:N", title=None, scale=_cat_scale(present),
                            sort=present),
            order=alt.Order("color_category_sort_index:Q"),
            opacity=alt.condition(legend_sel, alt.value(0.92), alt.value(0.10)),
            tooltip=[
                alt.Tooltip("label:N", title="Month") if order
                else alt.Tooltip("month:T", title="Month", format="%B %Y"),
                alt.Tooltip("category:N", title="Category"),
                alt.Tooltip("amount:Q", title="Amount (€)", format=_EURO),
                alt.Tooltip("share:Q", title="Share of month", format=".1%"),
                alt.Tooltip("total:Q", title="Month total (€)", format=_EURO),
            ],
        )
        .add_params(legend_sel)
    )

    if window:
        avg, std = _monthly_stats({m: totals[m] for m in window})
        label = "Average month"
        title = _window_title("Monthly spending by category", window, exclude)
    else:
        avg, std = d.get("avg_last12"), d.get("std_last12")
        label = "Average of the last 12 months"
        title = "Monthly spending by category" + _excl_note(shown, exclude)
    bands, rule = _avg_layers(avg, label, std)
    chart = (alt.layer(*bands, bars, *rule)
             .properties(height=height,
                         title=_avg_title(title, avg, label, std))
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="bottom", legend_columns=6)


def _avg_layers(avg, label, std=None):
    """
    The dashed average line, and — when a spread is known — a soft ±1σ band
    behind it.

    The bands are the point of showing σ at all. As a number it is abstract;
    drawn as nested bands you can see at a glance which months are ordinary
    variation and which are genuinely unusual. Two of them, because they answer
    different questions: ±1σ is the normal range a month lands in, while a bar
    escaping ±2σ is the one worth actually investigating — for roughly bell-ish
    data that is about a 1-in-20 month. They are drawn first so they sit behind
    the bars, and clamped at zero because a negative spending floor is not a
    thing.

    The VALUES go in the chart subtitle via `_avg_title`, not floated next to the
    line: an in-plot label has to sit somewhere, and wherever that is, some
    month's bar eventually grows into it — both the grocery and utilities panels
    put tall bars exactly where the line reaches the edge. A subtitle can never
    collide and never gets clipped.
    """
    if not avg:
        return [], []
    layers = []
    if std:
        # Boundary lines, not filled bands. A translucent coral fill over this
        # dark panel composites to a muddy brown across most of the plot and
        # makes the bars harder to read, for no gain: what you actually look for
        # is whether a month crosses a threshold, and a line marks a threshold
        # more precisely than the edge of a wash does. Dash and weight carry the
        # hierarchy instead — dashed for ±1σ, finely dotted for the rarer ±2σ.
        for k, dash, width, opacity in ((1, [5, 4], 1.2, 0.60),
                                        (2, [2, 5], 1.0, 0.40)):
            edges = pd.DataFrame({"y": [max(0.0, avg - k * std), avg + k * std],
                                  "edge": [f"−{k}σ", f"+{k}σ"]})
            layers.append(
                alt.Chart(edges)
                .mark_rule(color=ACCENT3, strokeDash=dash,
                           strokeWidth=width, opacity=opacity)
                .encode(y=alt.Y("y:Q", title=None),
                        tooltip=[alt.Tooltip("edge:N", title="Band"),
                                 alt.Tooltip("y:Q", title="€", format=",.2f")])
            )
    rule = (
        alt.Chart(pd.DataFrame({"y": [avg]}))
        .mark_rule(color=ACCENT3, strokeDash=[6, 4], strokeWidth=1.8)
        .encode(y="y:Q",
                tooltip=[alt.Tooltip("y:Q", title=f"{label} (€)", format=",.2f")])
    )
    # returned separately because the bands go BEHIND the bars and the line goes
    # in front: layers = bands + [bars] + [rule]
    return layers, [rule]


def _avg_title(title: str, avg, label: str, std=None):
    """Chart title carrying the average — and its spread — underneath it, in the
    same coral as the line and band, so the numbers and the marks read as one
    thing rather than as an unexplained shaded stripe."""
    if not avg:
        return title
    sub = f"{label}: €{avg:,.2f}"
    if std:
        sub += (f"   ·   σ €{std:,.2f} — {_within_hint(std, avg)}"
                f"   ·   ±1σ €{max(0, avg - std):,.0f}–{avg + std:,.0f}"
                f"   ·   ±2σ €{max(0, avg - 2 * std):,.0f}–{avg + 2 * std:,.0f}")
    return alt.TitleParams(
        title, subtitle=sub,
        subtitleColor=ACCENT3, subtitleFontSize=12, subtitleFontWeight=600,
        anchor="start",
    )


def _within_hint(std, avg) -> str:
    """A one-word reading of how spread out the months are, because σ in euros
    means nothing without knowing what it is a fraction of."""
    ratio = std / avg if avg else 0
    return ("very steady" if ratio < 0.15 else
            "fairly steady" if ratio < 0.30 else
            "variable" if ratio < 0.60 else "very variable")


# How many months the three household panels look back over. These are the
# recurring bills — utilities, groceries, dining — and for them the whole history
# is the wrong question to ask. Bills start, stop and change
# price, and averaging across every year
# answers "what did this used to cost" when what you want to know is "what does
# this cost now". The other panels keep their full history, where the long view
# is the point.
RECENT_MONTHS = 8


def _recent_window(d: dict, n: int = RECENT_MONTHS, exclude=()) -> list[str]:
    """
    The last `n` COMPLETE calendar months in the data.

    The month in progress is deliberately left out. It is not a small effect: a
    third of the way through September it contributes a third of a month's bills
    to an eight-month average, pulling every figure on these panels down by
    7-9% — and it would do so every time you opened the app mid-month, which is
    most of the time. A partial month is not a cheap month.

    Anchored to the data (not to today) beyond that, so a date filter ending in
    the past still gets the eight months before ITS end rather than an empty
    window.
    """
    months = d.get("all_months_str") or []
    if not months:
        return []
    today = date.today()
    in_progress = f"{today.year:04d}-{today.month:02d}"
    exclude = set(exclude or ())
    # Reaching back one further month for each exclusion is the point: dropping
    # two months from an eight-month window would otherwise leave six, quietly
    # weakening every average just when you were trying to clean it up.
    usable = [m for m in months if m < in_progress and m not in exclude]
    return usable[-n:]


def _in_window(monthly: dict, window) -> dict:
    keep = set(window)
    return {m: v for m, v in monthly.items() if m in keep}


def _window_title(base: str, window: list[str], exclude=()) -> str:
    """"Grocery spend — last 8 months (Jan–Aug 2026)". Spelling out the actual
    months matters more than it looks: without them there is no way to tell, from
    the chart alone, whether the month in progress is in there or not."""
    if not window:
        return base
    a, b = window[0], window[-1]
    ya, ma = a.split("-"); yb, mb = b.split("-")
    name = lambda m: date(2000, int(m), 1).strftime("%b")
    if a == b:
        span = f"{name(ma)} {ya}"
    elif ya == yb:
        span = f"{name(ma)}–{name(mb)} {yb}"
    else:
        span = f"{name(ma)} {ya}–{name(mb)} {yb}"
    n = len(window)
    # Only exclusions that fall INSIDE the span are worth naming — one from two
    # years ago is not being left out of this chart, it is simply not in it.
    listed = _excl_list([a, b], exclude)
    note = f", excluding {listed}" if listed else ""
    return f"{base} — last {n} month{'' if n == 1 else 's'} ({span}{note})"


def _excl_note(shown: list[str], exclude=()) -> str:
    """", excluding Feb 2026 and Jul 2026" — appended to a chart title whenever
    months have been left out. A statistic with silent exclusions is worse than
    no statistic: the reader has no way to know the bar they are looking for was
    removed on purpose rather than never existing."""
    listed = _excl_list(shown, exclude)
    return f" (excluding {listed})" if listed else ""


def _excl_list(shown: list[str], exclude=()) -> str:
    """"Feb 2026 and Jul 2026" — the excluded months that fall inside `shown`."""
    skipped = sorted(m for m in (exclude or ()) if shown and shown[0] <= m <= shown[-1])
    if not skipped:
        return ""
    name = lambda m: date(int(m[:4]), int(m[5:]), 1).strftime("%b %Y")
    names = [name(m) for m in skipped]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " and " + names[-1]


def _monthly_stats(monthly: dict) -> tuple[float | None, float | None]:
    """Mean and sample standard deviation over the months that actually had
    spending. Months with nothing in them are absent from the data rather than
    zero, and averaging them in would understate a bill you only pay quarterly.

    ddof=1 (sample, not population) because these months are a sample of your
    spending habit, not the complete population of months you will ever have.
    With few months the difference is not academic: over 8 months it inflates σ
    by ~7%, which is the honest direction to err in."""
    active = [v for v in monthly.values() if v > 0]
    if not active:
        return None, None
    mean = sum(active) / len(active)
    if len(active) < 2:
        return mean, None
    var = sum((v - mean) ** 2 for v in active) / (len(active) - 1)
    return mean, var ** 0.5


def _stacked_month_chart(pairs, colors, title, y_title, height,
                         avg=None, avg_label="Avg", std=None, window=None):
    """Shared builder for the grocery and utilities panels: a stacked bar over
    months, with an optional dashed average line. `pairs` is [(name, {month: v})]."""
    rows = [{"month": m, "series": name, "amount": v}
            for name, monthly in pairs
            for m, v in monthly.items() if v]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    order = _month_labels(window) if window else None
    if order:
        df["label"] = df["month"].map(
            dict(zip(window, order)))
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
            x=_month_x(order=order),
            y=alt.Y("amount:Q", title=y_title, stack="zero", axis=alt.Axis(format="~s")),
            color=alt.Color("series:N", title=None, sort=present,
                            scale=alt.Scale(domain=present,
                                            range=[colors[n] for n in present])),
            opacity=alt.condition(legend_sel, alt.value(0.92), alt.value(0.10)),
            tooltip=[
                alt.Tooltip("label:N", title="Month") if order
                else alt.Tooltip("month:T", title="Month", format="%B %Y"),
                alt.Tooltip("series:N", title="Source"),
                alt.Tooltip("amount:Q", title="Amount (€)", format=_EURO),
                alt.Tooltip("total:Q", title="Month total (€)", format=_EURO),
            ],
        )
        .add_params(legend_sel)
    )
    bands, rule = _avg_layers(avg, avg_label, std)
    chart = (alt.layer(*bands, bars, *rule)
             .properties(height=height,
                         title=_avg_title(title, avg, avg_label, std))
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="bottom", legend_columns=4)


def grocery_chart(d: dict, height: int = 300, exclude=()):
    """Panel 2 — grocery spend per shop, over the recent window."""
    window = _recent_window(d, exclude=exclude)
    if not window:
        return None
    detail = _in_window(d["grocery_detail"], window)
    order = ["LIDL", "Mercadona", "Alcampo", "Simply", "Fruteria", "Obrador", "Other Grocery"]
    colors = dict(zip(order, ["#56d364", "#3fb950", "#26a641", "#1a7f37",
                              "#6bc8f5", "#79c0ff", "#8b949e"]))
    pairs = [(name, {m: vals.get(name, 0.0) for m, vals in detail.items()})
             for name in order]
    avg, std = _monthly_stats(_in_window(d["monthly_grocery"], window))
    return _stacked_month_chart(pairs, colors,
                                _window_title("Grocery spend", window, exclude), "€",
                                height, avg=avg, std=std,
                                avg_label="Average month", window=window)


def utilities_chart(d: dict, height: int = 300, exclude=()):
    """Panel 4 — electricity / gas / water / phone per month."""
    window = _recent_window(d, exclude=exclude)
    if not window:
        return None
    order = ["Electricity", "Natural Gas", "Water", "Phone"]
    pairs = [(n, _in_window(d[k], window)) for n, k in (
        ("Electricity", "monthly_electricity"), ("Natural Gas", "monthly_gas"),
        ("Water", "monthly_water"), ("Phone", "monthly_phone"))]
    colors = {n: CAT_COLORS[n] for n in order}
    # The average of the whole utilities bill, not of any one supply: these four
    # arrive on different cycles (water quarterly, phone monthly), so what you
    # actually want to compare a month against is the combined total.
    combined: dict[str, float] = {}
    for _, monthly in pairs:
        for m, v in monthly.items():
            combined[m] = combined.get(m, 0.0) + v
    avg, std = _monthly_stats(combined)
    return _stacked_month_chart(pairs, colors,
                                _window_title("Utilities", window, exclude), "€", height,
                                avg=avg, std=std, avg_label="Average month",
                                window=window)


def dining_chart(d: dict, height: int = 300, exclude=()):
    """Panel 3 — dining & food per month, against its own average."""
    window = _recent_window(d, exclude=exclude)
    if not window:
        return None
    monthly = _in_window(d["monthly_dining"], window)
    rows = [{"month": m, "amount": v} for m, v in monthly.items() if v]
    if not rows:
        return None
    df = pd.DataFrame(rows)
    order = _month_labels(window)
    df["label"] = df["month"].map(dict(zip(window, order)))
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
    bars = (
        alt.Chart(df)
        .mark_bar(color=ACCENT5, opacity=0.9)
        .encode(
            x=_month_x(order=order),
            y=alt.Y("amount:Q", title="€", axis=alt.Axis(format="~s")),
            tooltip=[alt.Tooltip("label:N", title="Month"),
                     alt.Tooltip("amount:Q", title="Spent (€)", format=_EURO)],
        )
    )
    avg, std = _monthly_stats(monthly)
    bands, rule = _avg_layers(avg, "Average month", std)
    chart = (alt.layer(*bands, bars, *rule)
             .properties(height=height,
                         title=_avg_title(_window_title("Dining & food", window, exclude),
                                          avg, "Average month", std))
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="bottom")


def category_totals_chart(d: dict, height: int = 340, exclude=()):
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
             .properties(height=height,
                         title="Total spending by category"
                               + _excl_note(d["all_months_str"], exclude)))
    return _theme(chart)


def income_vs_spending_chart(d: dict, height: int = 320, exclude=()):
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
        .properties(height=height,
                    title="Monthly income vs total spending"
                          + _excl_note(months, exclude))
    )
    return _theme(chart, legend_orient="bottom")


ALL_PANELS = ("by_category", "grocery", "dining", "utilities",
              "cat_totals", "income_spend")


def spending_specs(d: dict, keys=None, by_category_months: int | None = None,
                   exclude=()) -> dict:
    """
    The spending panels as Vega-Lite spec dicts, ready to cache and render.

    `keys` limits which are built. The app uses it to keep the by-category panel
    on its own cache entry: that panel has a window control, and rebuilding all
    six specs every time the control moves costs ~400ms for five charts that did
    not change.
    """
    builders = {
        "by_category":  lambda dd: monthly_by_category_chart(
            dd, months=by_category_months, exclude=exclude),
        "grocery":      lambda dd: grocery_chart(dd, exclude=exclude),
        "dining":       lambda dd: dining_chart(dd, exclude=exclude),
        "utilities":    lambda dd: utilities_chart(dd, exclude=exclude),
        "cat_totals":   lambda dd: category_totals_chart(dd, exclude=exclude),
        "income_spend": lambda dd: income_vs_spending_chart(dd, exclude=exclude),
    }
    out = {}
    for key in (keys if keys is not None else ALL_PANELS):
        chart = builders[key](d)
        out[key] = chart.to_dict() if chart is not None else None
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Investment donuts
# ══════════════════════════════════════════════════════════════════════════════
# The last server-rendered images in the app. A donut is the one chart type where
# a tooltip earns its keep twice over: the wedge geometry answers "roughly how
# big", and only a label or a hover can answer "how big exactly". The static
# version could print the euro amount on wedges above ~2.5% and had to leave the
# small ones to the legend; here every slice answers on hover, however thin.

def _donut(df, name_col, colors, total, title, height=360, label_min_pct=2.5,
           legend_columns=2, compare=None):
    """
    Shared donut builder: arc + on-wedge euro labels + centre total.

    `compare` turns this into a BEFORE/AFTER ring. Pass {name: contributed} and
    each wedge additionally reports how that holding has moved against what was
    paid into it.

    Two different "changes" are possible here and they do not agree, so both are
    surfaced rather than one being passed off as "the" change:

      return   — value against what YOU paid into that holding. This is the
                 money question, and it is what the wedge marker shows.
      share    — that holding's slice of the whole ring, in percentage points
                 against its slice of the contributions ring. A fund can gain
                 value and still LOSE share, if the rest of the portfolio gained
                 more; showing only the arrow would make that look like a loss.
                 It lives in the tooltip, where there is room to name it.
    """
    df = df.copy()
    df["share"] = df["amount"] / total if total else 0.0

    if compare:
        # A two-line label is both taller and wider than a bare euro figure, so
        # thin neighbouring wedges whose single-line labels merely sat close now
        # collide outright. Two mitigations: the marker is kept terse (the arrow
        # already carries the sign, so "+" and a decimal place are redundant),
        # and the threshold for printing anything at all is raised. Anything
        # dropped is still on hover and in the legend.
        label_min_pct = max(label_min_pct, 5.0)
        contrib_total = sum(compare.get(n, 0.0) for n in df[name_col]) or 0.0
        df["contributed"] = [float(compare.get(n, 0.0)) for n in df[name_col]]
        df["gain"] = df["amount"] - df["contributed"]
        df["ret"] = [
            (g / c) if c > 0 else None
            for g, c in zip(df["gain"], df["contributed"])
        ]
        df["share_before"] = [
            (compare.get(n, 0.0) / contrib_total) if contrib_total else 0.0
            for n in df[name_col]
        ]
        df["share_delta"] = df["share"] - df["share_before"]

        def _mark(amount, share, ret, dshare):
            """
            Three stacked lines: the amount, then BOTH changes, deliberately
            given different glyph families and different units so they can never
            be read as the same thing.

                ▲ ▼  ...%    return — value against what you paid in
                ↗ ↘  ...pp   share  — this slice's share of the ring, in
                             percentage points against the contributions ring

            They genuinely disagree: a holding can return +2% and still shed 3pp
            of share because the rest of the portfolio grew faster. Solid
            triangles read as "the money did this"; line arrows as "the slice
            did this". Stacked rather than side by side because a single wide
            line overflows a narrow wedge into its neighbour.
            """
            if (share * 100) < label_min_pct:
                return ""
            lines = [f"€{amount:,.0f}"]
            if ret is not None:
                a = "▲" if ret > 0 else ("▼" if ret < 0 else "▬")
                lines.append(f"{a}{abs(ret) * 100:.0f}%")
            if dshare is not None:
                a = "↗" if dshare > 0.00005 else ("↘" if dshare < -0.00005 else "→")
                lines.append(f"{a}{abs(dshare) * 100:.1f}pp")
            return "\n".join(lines)

        df["label"] = [_mark(a, s, r, ds) for a, s, r, ds in
                       zip(df["amount"], df["share"], df["ret"], df["share_delta"])]
    else:
        # Only slices with room get a printed label; the rest rely on hover and
        # the legend, exactly as in the matplotlib version.
        df["label"] = [
            f"€{a:,.0f}" if (s * 100) >= label_min_pct else ""
            for a, s in zip(df["amount"], df["share"])
        ]
    names = list(df[name_col])

    _tips = [
        alt.Tooltip(f"{name_col}:N", title=""),
        alt.Tooltip("amount:Q", title="Amount (€)", format=_EURO),
        alt.Tooltip("share:Q", title="Share now", format=".1%"),
    ]
    if compare:
        _tips += [
            alt.Tooltip("contributed:Q", title="Contributed (€)", format=_EURO),
            alt.Tooltip("gain:Q", title="Gain (€)", format=_EURO),
            alt.Tooltip("ret:Q", title="Return", format="+.1%"),
            alt.Tooltip("share_before:Q", title="Share contributed", format=".1%"),
            alt.Tooltip("share_delta:Q", title="Share change (pp)", format="+.1%"),
        ]

    highlight = alt.selection_point(fields=[name_col], bind="legend")
    base = alt.Chart(df).encode(
        theta=alt.Theta("amount:Q", stack=True),
        color=alt.Color(name_col, type="nominal", title=None, sort=names,
                        scale=alt.Scale(domain=names,
                                        range=[colors.get(n, "#8b949e") for n in names])),
        order=alt.Order("amount:Q", sort="descending"),
        tooltip=_tips,
    )
    # A three-line label needs a thicker band than a one-line one: at the
    # standard 80/140 the bottom line overflowed the inner edge and landed on
    # the dark centre, where dark text is invisible. Widening the ring and
    # dropping a point of type keeps the whole block inside the colour.
    _inner, _outer, _fs = (72, 150, 9) if compare else (80, 140, 10)
    arc = base.mark_arc(innerRadius=_inner, outerRadius=_outer,
                        stroke=PANEL_BG, strokeWidth=2).encode(
        opacity=alt.condition(highlight, alt.value(0.95), alt.value(0.25)),
    ).add_params(highlight)

    labels = base.mark_text(radius=(_inner + _outer) / 2, fontSize=_fs,
                            fontWeight="bold", lineBreak="\n",
                            lineHeight=11, fill=DARK_BG).encode(
        text=alt.Text("label:N"))

    centre = (
        alt.Chart(pd.DataFrame({"t": [f"€{total:,.0f}", "total"]}))
        .mark_text(fontSize=15, fontWeight="bold", color=TEXT_COL,
                   dy=alt.expr("datum.t === 'total' ? 10 : -8"))
        .encode(text="t:N")
    )
    # No Vega title on donuts. Vega draws a title above the plot origin, but the
    # SVG is sized to the declared plot height, so with a tall bottom legend the
    # title is laid out ABOVE the canvas and clipped away — measured at 32px
    # above the container for the 8-entry fund rings, i.e. invisible. The three
    # -entry rings only overflowed by 5px, which is why this looked like it
    # worked. The caller renders the heading in Streamlit instead, where it
    # cannot be clipped and every ring is styled identically.
    chart = alt.layer(arc, labels, centre).properties(height=height)
    return _theme(chart, legend_orient="bottom", legend_columns=legend_columns)


def fund_donut(d: dict, height: int = 380):
    """Contributions by fund. Uses fund_totals, so a fund reached through two
    accounts is one slice rather than two identically-labelled ones."""
    totals = d.get("fund_totals") or {}
    totals = {k: v for k, v in totals.items() if v > 0}
    if not totals:
        return None
    df = pd.DataFrame({"Fund": list(totals), "amount": list(totals.values())})
    # One legend column, not two: fund names run long and a two-column legend
    # truncates them mid-name, which defeats the point of having a legend.
    return _donut(df, "Fund", FUND_COLORS, sum(totals.values()), "By fund", height,
                  legend_columns=1)


def account_donut(d: dict, height: int = 380):
    """Contributions by the account they were bought through."""
    totals = {k: v for k, v in (d.get("account_totals") or {}).items() if v > 0}
    if not totals:
        return None
    df = pd.DataFrame({"Account": list(totals), "amount": list(totals.values())})
    return _donut(df, "Account", ACCOUNT_COLORS, sum(totals.values()), "By account", height)


def asset_class_donut(d: dict, height: int = 380):
    """Contributions by asset class, after any per-fund overrides you've set."""
    totals: dict[str, float] = {}
    for cls, amt in zip(d.get("fund_asset_classes", []), d.get("fund_amounts", [])):
        if amt > 0:
            totals[cls] = totals.get(cls, 0.0) + amt
    if not totals:
        return None
    totals = dict(sorted(totals.items(), key=lambda kv: -kv[1]))
    df = pd.DataFrame({"Asset class": list(totals), "amount": list(totals.values())})
    return _donut(df, "Asset class", ASSET_CLASS_COLORS, sum(totals.values()),
                  "By asset class", height)


def investment_donut_specs(d: dict) -> dict:
    """All three donuts as Vega-Lite spec dicts, ready to cache and render."""
    out = {}
    for key, build in (("fund", fund_donut), ("account", account_donut),
                       ("asset_class", asset_class_donut)):
        c = build(d)
        out[key] = c.to_dict() if c is not None else None
    return out


def value_vs_contributions_chart(timeline: list[dict], height: int = 380):
    """
    What you put in against what it became.

    The cost basis is a continuous filled area — it is known for every month.
    Value is drawn only where you recorded a snapshot, as a line through those
    points: months you never measured stay empty instead of being guessed at.
    The vertical distance between the two is the gain, which is the whole point
    of the chart, so the tooltip names it outright at every measured month.
    """
    if not timeline:
        return None
    df = pd.DataFrame(timeline)
    df["month"] = pd.to_datetime(df["month"], format="%Y-%m")
    valued = df[df["value"].notna()]
    if valued.empty:
        return None

    base = alt.Chart(df).encode(
        x=alt.X("month:T", title=None, axis=alt.Axis(format="%b %Y")))

    contributed = base.mark_area(
        color=INVEST_COL, opacity=0.28, line={"color": INVEST_COL, "strokeWidth": 1.5},
    ).encode(y=alt.Y("contributed:Q", title="€", axis=alt.Axis(format="~s")))

    value_line = (
        alt.Chart(valued)
        .mark_line(color=NET_COL, strokeWidth=2.4, point=alt.OverlayMarkDef(
            color=NET_COL, size=55, filled=True))
        .encode(x=alt.X("month:T"), y=alt.Y("value:Q"))
    )

    hover = alt.selection_point(fields=["month"], nearest=True, on="pointerover",
                                empty=False, clear="pointerout")
    tips = [
        alt.Tooltip("month:T", title="Month", format="%B %Y"),
        alt.Tooltip("contributed:Q", title="Contributed (€)", format=_EURO),
        alt.Tooltip("value:Q", title="Value (€)", format=_EURO),
        alt.Tooltip("gain:Q", title="Gain (€)", format=_EURO),
    ]
    selectors = base.mark_rule(opacity=0).encode(tooltip=tips).add_params(hover)
    rule = base.mark_rule(color=TEXT_COL, strokeWidth=1).encode(
        opacity=alt.condition(hover, alt.value(0.5), alt.value(0)))

    legend_df = pd.DataFrame({
        "series": ["Contributed", "Value"],
        "y": [df["contributed"].max(), df["contributed"].max()],
    })
    key = (
        alt.Chart(legend_df)
        .mark_point(opacity=0)
        .encode(y=alt.Y("y:Q"),
                color=alt.Color("series:N", title=None,
                                scale=alt.Scale(domain=["Contributed", "Value"],
                                                range=[INVEST_COL, NET_COL])))
    )

    chart = (alt.layer(contributed, value_line, key, selectors, rule)
             .properties(height=height, title="Value vs contributions")
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="top-left")


def fund_value_donut(values: dict[str, float],
                     contributed: dict[str, float] | None = None,
                     height: int = 380):
    """
    Current value by fund — the same ring as fund_donut, but weighted by what
    each holding is worth today rather than by what was paid into it.

    Read against the contributions donut, the difference between the two IS the
    performance: a fund whose slice is fatter here than above has grown faster
    than the rest of the portfolio, and one that is thinner has lagged. Neither
    ring alone can show that, which is why this belongs directly underneath the
    other rather than replacing it.

    Colours come from the same FUND_COLORS map, so a given fund is the same
    colour in both rings and the comparison is a matter of arc width alone.
    """
    vals = {k: float(v) for k, v in (values or {}).items() if v and float(v) > 0}
    if not vals:
        return None
    vals = dict(sorted(vals.items(), key=lambda kv: -kv[1]))
    df = pd.DataFrame({"Fund": list(vals), "amount": list(vals.values())})
    return _donut(df, "Fund", FUND_COLORS, sum(vals.values()),
                  "By fund — current value", height, legend_columns=1,
                  compare=contributed)


def asset_class_value_donut(values: dict[str, float],
                            fund_to_class: dict[str, str],
                            contributed: dict[str, float] | None = None,
                            height: int = 380):
    """
    Asset-class mix weighted by current value rather than by contributions.

    This is the version that answers the question people think the allocation
    donut is answering. A contributions-based split says how you INTENDED to
    allocate; it cannot move unless you buy something. The real mix drifts on its
    own — gold running up or equities falling changes your exposure with no
    transaction at all — and only a valuation can see that.

    Funds that were valued but whose class isn't known in the current view fall
    into "Unspecified" rather than being dropped, so the ring always sums to the
    valuation total and can be trusted against the headline figure.
    """
    vals = {k: float(v) for k, v in (values or {}).items() if v and float(v) > 0}
    if not vals:
        return None
    totals: dict[str, float] = {}
    for fund, amount in vals.items():
        cls = fund_to_class.get(fund) or "Unspecified"
        totals[cls] = totals.get(cls, 0.0) + amount
    totals = dict(sorted(totals.items(), key=lambda kv: -kv[1]))
    # Roll the per-fund contributions up the same way, so the comparison is
    # class-against-class rather than class-against-fund.
    contrib_by_class: dict[str, float] = {}
    for fund, amount in (contributed or {}).items():
        cls = fund_to_class.get(fund) or "Unspecified"
        contrib_by_class[cls] = contrib_by_class.get(cls, 0.0) + float(amount)
    df = pd.DataFrame({"Asset class": list(totals), "amount": list(totals.values())})
    return _donut(df, "Asset class", ASSET_CLASS_COLORS, sum(totals.values()),
                  "By asset class — current value", height,
                  compare=contrib_by_class or None)


# ══════════════════════════════════════════════════════════════════════════════
# Forecast
# ══════════════════════════════════════════════════════════════════════════════

def forecast_chart(history: list[dict], projection: list[dict],
                   height: int = 340):
    """
    Recent cash, then where it goes if nothing changes.

    The past months are drawn solid and the projection dashed, sharing one line,
    because the join is the whole point: a forecast that starts from a detached
    origin invites you to read the trend without checking it against what
    actually happened. The shaded fan is ±1σ of monthly spending widened by √k
    (see forecast.project) — genuinely a range of outcomes, so here a filled
    band is the right mark rather than the boundary lines the spending panels
    use for a fixed threshold.

    Future income is held at the small recurring trickle only. The line
    therefore slopes down, and is meant to: it answers "how long does this last
    if nothing arrives", not "what will happen".
    """
    if not projection:
        return None

    hist = [{"month": h["month"], "balance": float(h["cash"]),
             "kind": "Actual"} for h in (history or [])]
    proj = [{"month": p["month"], "balance": float(p["balance"]),
             "lo": float(p["lo"]), "hi": float(p["hi"]), "kind": "Projected"}
            for p in projection]
    # repeat the last actual month as the projection's first point so the two
    # line segments touch instead of leaving a one-month gap
    if hist:
        j = dict(hist[-1]); j.update(kind="Projected",
                                     lo=hist[-1]["balance"], hi=hist[-1]["balance"])
        proj = [j] + proj

    order = _month_labels([r["month"] for r in hist + proj[(1 if hist else 0):]])
    lab = dict(zip([r["month"] for r in hist + proj[(1 if hist else 0):]], order))
    for r in hist + proj:
        r["label"] = lab.get(r["month"], r["month"])

    df_h, df_p = pd.DataFrame(hist), pd.DataFrame(proj)
    x = _month_x(order=order)

    layers = []
    band = (alt.Chart(df_p)
            .mark_area(color=CASH_COL, opacity=0.16)
            .encode(x=x, y=alt.Y("lo:Q", title="€", axis=alt.Axis(format="~s")),
                    y2="hi:Q"))
    layers.append(band)
    layers.append(
        alt.Chart(pd.DataFrame({"y": [0.0]}))
        .mark_rule(color="#f85149", strokeWidth=1.4, opacity=0.8)
        .encode(y="y:Q")
    )
    if not df_h.empty:
        layers.append(
            alt.Chart(df_h).mark_line(color=CASH_COL, strokeWidth=2.4)
            .encode(x=x, y="balance:Q",
                    tooltip=[alt.Tooltip("label:N", title="Month"),
                             alt.Tooltip("balance:Q", title="Cash (€)", format=",.2f")])
        )
    layers.append(
        alt.Chart(df_p).mark_line(color=CASH_COL, strokeWidth=2.4,
                                  strokeDash=[6, 4], opacity=0.95)
        .encode(x=x, y="balance:Q",
                tooltip=[alt.Tooltip("label:N", title="Month"),
                         alt.Tooltip("balance:Q", title="Projected cash (€)", format=",.2f"),
                         alt.Tooltip("lo:Q", title="−1σ (€)", format=",.2f"),
                         alt.Tooltip("hi:Q", title="+1σ (€)", format=",.2f")])
    )
    layers.append(
        alt.Chart(df_p).mark_point(color=CASH_COL, size=45, filled=True, opacity=0.9)
        .encode(x=x, y="balance:Q")
    )
    chart = (alt.layer(*layers).properties(height=height)
             .resolve_scale(y="shared"))
    return _theme(chart, legend_orient="bottom")


# ══════════════════════════════════════════════════════════════════════════════
# Period comparison
# ══════════════════════════════════════════════════════════════════════════════

# Diverging, not categorical: the quantity being drawn has a natural zero and a
# direction, so it gets two hues with a neutral middle rather than one colour
# per bar. Warm = spending went up, cool = it came down.
_UP, _DOWN = "#f85149", "#3fb950"


def comparison_chart(cmp: dict, height: int = 360, top: int = 12):
    """
    What moved, ranked by how much — biggest mover at the top.

    Ranked by the SIZE of the change in euros, not by how large the category is
    or by percentage. Sorting by category size just reproduces the spending
    chart with extra steps, and sorting by percentage puts a €4 coffee habit
    that doubled above a €90 rise in the electricity bill. Euros moved is the
    ordering that matches what you would actually do something about.
    """
    cats = [c for c in cmp.get("categories", []) if abs(c["delta"]) >= 0.005][:top]
    if not cats:
        return None
    df = pd.DataFrame([{
        "category": c["category"],
        "delta": c["delta"],
        "before": c["before"],
        "after": c["after"],
        "pct": c["pct"],
        "note": ("new" if c["is_new"] else "stopped" if c["stopped"]
                 else f"{c['pct']:+.0%}" if c["pct"] is not None else ""),
    } for c in cats])
    order = list(df["category"])

    base = alt.Chart(df).encode(
        y=alt.Y("category:N", sort=order, title=None),
        x=alt.X("delta:Q", title="change in € per month",
                axis=alt.Axis(format="+~s")),
        tooltip=[
            alt.Tooltip("category:N", title="Category"),
            alt.Tooltip("before:Q", title="Before (€/mo)", format=",.2f"),
            alt.Tooltip("after:Q", title="After (€/mo)", format=",.2f"),
            alt.Tooltip("delta:Q", title="Change (€/mo)", format="+,.2f"),
            alt.Tooltip("note:N", title="Relative"),
        ],
    )
    bars = base.mark_bar(opacity=0.9).encode(
        color=alt.condition(alt.datum.delta > 0, alt.value(_UP), alt.value(_DOWN)))
    # Labels sit on the OUTSIDE of each bar so they never sit on top of the
    # mark. The anchor has to flip with the sign, and `align` is a mark
    # property here rather than an encoding channel, so this is two filtered
    # layers rather than one conditional encoding.
    labels = alt.layer(*[
        base.transform_filter(flt)
            .mark_text(fontSize=10, color=TEXT_COL, align=align, dx=dx)
            .encode(text=alt.Text("note:N"))
        for flt, align, dx in ((alt.datum.delta > 0, "left", 5),
                               (alt.datum.delta <= 0, "right", -5))
    ])
    zero = (alt.Chart(pd.DataFrame({"x": [0.0]}))
            .mark_rule(color=GRID_COL, strokeWidth=1).encode(x="x:Q"))

    a, b = cmp["prior"], cmp["recent"]
    span = lambda w: (f"{date(int(w[0][:4]), int(w[0][5:]), 1):%b %Y}"
                      f"–{date(int(w[-1][:4]), int(w[-1][5:]), 1):%b %Y}") if w else "—"
    t = cmp["totals"]
    sub = (f"{span(a)} → {span(b)}   ·   "
           f"€{t['before']:,.0f} → €{t['after']:,.0f} per month   ·   "
           f"{t['delta']:+,.0f}"
           + (f" ({t['pct']:+.0%})" if t["pct"] is not None else ""))
    chart = (alt.layer(zero, bars, labels)
             .properties(height=height,
                         title=alt.TitleParams(
                             "What changed, per month",
                             subtitle=sub, subtitleColor=TEXT_COL,
                             subtitleFontSize=12, anchor="start")))
    return _theme(chart)
