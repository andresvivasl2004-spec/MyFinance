"""
app.py — Streamlit personal finance dashboard.
Run with:  streamlit run app.py
"""

import sys, tempfile, io, json
import calendar as pycalendar
from datetime import date, timedelta
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from pathlib import Path

st.set_page_config(page_title="Finance", page_icon="💰",
                   layout="wide", initial_sidebar_state="expanded")

sys.path.insert(0, str(Path(__file__).parent))

from finance.auto_parser import detect_and_parse, reparse
import finance.banklink as bl
from finance.categories  import load_categories, save_categories, add_category, delete_category, reset_to_defaults
from finance.categorizer import find_category, load_known_merchants, save_known_merchants
from finance.storage     import (save_to_global, load_from_global, GLOBAL_FILE,
                         find_duplicate_groups, group_key,
                         describe_backups, restore_backup, BACKUP_KEEP,
                         transaction_rows, update_categories)
from finance.duplicates  import load_dismissed, dismiss, undismiss
from finance.processor   import (
    process, daily_summary,
    compute_net_worth_timeline, compute_allocation_timeline,
)
from finance.investments import (
    ASSET_CLASSES, INTEREST_TYPES, CLASSIFICATION_FILE,
    get_classification, set_classification, clear_classification,
)
from finance.valuation  import (
    VALUES_FILE, load_snapshots, save_snapshot, delete_snapshot,
    latest_snapshot, compute_returns, value_timeline,
)
from finance.processor   import _detect_investment_fund
from finance.anomaly     import detect, adjusted_process, plot_anomaly_overview
# The Net Worth, Allocation-over-time and six spending panels are interactive
# Vega-Lite charts now, not matplotlib PNGs — see interactive.py. charts.py still
# holds the matplotlib versions of all of them (analysis.ipynb uses those, and
# they remain the right tool for a static export); the app just no longer does.
# Only the two investment donuts are still server-rendered images.
from finance.interactive import (
    net_worth_chart, allocation_chart, spending_specs, investment_donut_specs,
    value_vs_contributions_chart, fund_value_donut, asset_class_value_donut,
    forecast_chart, comparison_chart, ALL_PANELS,
)
import finance.forecast as fc
from finance.exclusions import (load_exclusions, save_exclusions, exclusions_key,
                        suspect_months)
import finance.subscriptions as subs_mod
import finance.compare as cmp_mod
from finance.style       import apply_dark_theme

apply_dark_theme()

# ── Cached wrappers ──────────────────────────────────────────────────────────
# Streamlit re-runs this whole script on every click (every "Confirm & next"
# while categorising, every widget change). Without caching, each of those
# clicks was silently re-reading the whole Excel file AND regenerating every
# chart on the Analysis/Anomaly tabs behind the scenes — even though those
# tabs aren't visible while you're on "Add New Month". These wrappers make a
# repeat call with the same inputs return instantly instead of redoing the
# work, so data entry stops paying for analysis it isn't looking at.

@st.cache_data(show_spinner=False)
def _load_all_data(mtime: float):
    return load_from_global()

def get_all_data():
    """load_from_global(), cached until gastos_global.xlsx actually changes on disk."""
    p = Path(GLOBAL_FILE)
    if not p.exists():
        return None
    return _load_all_data(p.stat().st_mtime)

# ── Cache keys: why the data itself never goes into one ──────────────────────
# Every st.cache_data lookup has to HASH its arguments to build the cache key
# before it can tell you "yes, I already have this". `filtered_data` is a list
# of 1,000+ (datetime, float, str, str, str) tuples, and hashing one costs
# ~21ms. That happened at ~13 call sites on every single rerun — because every
# tab's body runs on every rerun, visible or not. Profiled across all threads:
# roughly a SECOND per click spent hashing arguments, against ~7ms of actual
# work. The cache had become far more expensive than everything it avoided:
# process() takes 3.4ms but cost 21ms to look up, and detect() takes 0.1ms but
# cost 9.5ms to look up — a 95x net loss on that one.
#
# So two changes. First, anything that cheap isn't cached at all any more; it's
# just computed (see D_MAIN below — computed once per rerun and shared by the
# three tabs that used to each ask the cache for it). Only the chart PNGs, at
# 300-2000ms apiece, are actually worth caching.
#
# Second, those remaining caches never hash the data. Streamlit skips hashing
# any argument whose name starts with an underscore, so the rows go in as
# `_filtered_data` and a tiny `data_key` tuple carries their identity instead.
# `filtered_data` is fully determined by the contents of gastos_global.xlsx
# plus which accounts are ticked, so (file mtime, file size, row count,
# selected accounts) identifies it exactly — and hashes in microseconds rather
# than milliseconds, no matter how many years of transactions pile up. That's
# the same assumption _load_all_data() above already relies on for the file,
# made stricter here by also including size and row count.
def _file_stamp(path):
    """(mtime_ns, size) for a file — a cheap, exact 'has this changed?' token."""
    try:
        s = Path(path).stat()
        return (s.st_mtime_ns, s.st_size)
    except OSError:
        return None

# Charts: cache the RENDERED PNG BYTES, not the matplotlib Figure object.
# Caching the Figure (via cache_resource, the previous approach here) only
# skips re-drawing it — st.pyplot() still has to rasterise that Figure to an
# image on every single call to display it, and for the ~22x30in 6-panel
# spending chart that rasterisation alone measured ~5-6 SECONDS, on every
# rerun, regardless of whether the Figure came from cache. Since every tab's
# code runs on every Streamlit rerun (even ones you're not looking at — e.g.
# clicking "view" in Calendar), this was the single biggest source of the
# whole app feeling slow. Caching the finished PNG bytes with cache_data
# instead means that rasterisation happens once per unique data/period, and
# every subsequent rerun just displays the cached image — no Figure object,
# no re-draw. These are the only things left in the app worth caching, and
# they're keyed on the cheap DATA_KEY/CLASS_KEY tokens described above rather
# than on the data itself.
def _fig_to_png(fig) -> bytes:
    # No bbox_inches="tight" here on purpose: every chart function already sets
    # its own manual GridSpec margins and/or calls plt.tight_layout(), so the
    # "tight" bbox is a second, redundant full-figure layout pass — measured at
    # ~250ms extra per chart (e.g. 1512ms -> 1266ms on the 6-panel spending
    # chart alone), paid on every cache miss. Dropping it just adds a few
    # pixels of uniform dark-background margin around each figure (checked
    # side-by-side across every chart type: no cropped labels, no collisions),
    # which is invisible against the app's dark panels.
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


@st.cache_data(show_spinner=False)
def _cached_anomaly_png(_filtered_data, data_key, start_month, end_month, iqr_multiplier):
    d = process(_filtered_data, start_month=start_month, end_month=end_month)
    anomalies = detect(d, iqr_multiplier=iqr_multiplier)
    return _fig_to_png(plot_anomaly_overview(d, anomalies))


# The two "over time" charts are Vega-Lite specs rather than PNGs — see
# interactive.py. What gets cached is the finished spec dict: building one costs
# ~115ms (almost entirely Altair validating itself against the Vega-Lite schema),
# and these tabs re-render on every rerun, so rebuilding each time would hand
# back the cost we just removed elsewhere. Cached, a rerun costs nothing and the
# browser does the drawing.
#
# Still NOT keyed on start_month/end_month — see compute_net_worth_timeline()'s
# docstring. These always cover the full history for whichever accounts are
# selected, so the sidebar's Period filter doesn't apply to them.
# The six spending panels, as Vega-Lite specs. This replaces the single 22x30in
# matplotlib PNG that used to cost ~1.2s to draw and 221KB to ship; the six specs
# together are ~77KB of JSON and the browser draws them. Cached as one entry
# because they always change together.
# The five panels with no controls of their own.
_OTHER_PANELS = tuple(k for k in ALL_PANELS if k != "by_category")


@st.cache_data(show_spinner=False)
def _cached_spending_specs(_filtered_data, data_key, start_month, end_month, excl):
    # exclude_months goes into process() as well as into the chart builders.
    # process() drops those months from all_months_str, and every total and
    # per-category average is derived from that list — so one argument makes the
    # category totals, the income-vs-spending panel and the summary table agree
    # with the charts instead of quietly disagreeing with them.
    return spending_specs(process(_filtered_data, start_month=start_month,
                                  end_month=end_month, exclude_months=set(excl)),
                          keys=_OTHER_PANELS, exclude=excl)


# The by-category panel is cached on its own because it has a window control.
# Building all six specs costs ~420ms and five of them do not depend on the
# window, so sharing one cache entry would make every move of the slider pay
# for five charts that did not change; alone, this one is ~100ms and each
# window setting is cached after its first use.
@st.cache_data(show_spinner=False)
def _cached_by_category_spec(_filtered_data, data_key, start_month, end_month,
                             months, excl):
    d = process(_filtered_data, start_month=start_month, end_month=end_month,
                exclude_months=set(excl))
    return spending_specs(d, keys=("by_category",), by_category_months=months,
                          exclude=excl)["by_category"]


@st.cache_data(show_spinner=False)
def _cached_comparison(_filtered_data, data_key, n, excl):
    return cmp_mod.compare(_filtered_data, n=n, exclude=excl)


@st.cache_data(show_spinner=False)
def _cached_suspect_months(_filtered_data, data_key):
    return suspect_months(_filtered_data)


@st.cache_data(show_spinner=False)
def _cached_excluded_process(_filtered_data, data_key, start_month, end_month, excl):
    return process(_filtered_data, start_month=start_month, end_month=end_month,
                   exclude_months=set(excl))


@st.cache_data(show_spinner=False)
def _cached_subscriptions(_filtered_data, data_key):
    return subs_mod.detect_all(_filtered_data)


@st.cache_data(show_spinner=False)
def _cached_forecast(_filtered_data, data_key, horizon, keep_investing, cash, excl):
    return fc.summarise(_filtered_data, cash, months=horizon,
                        keep_investing=keep_investing, exclude=excl)


def _cat_table(d):
    """Per-category totals and monthly statistics."""
    cat_rows = [{"Category": cat, "Total (€)": total,
                 "Avg/month": d["cat_monthly_avg"].get(cat, 0),
                 "Std dev": d["cat_monthly_std"].get(cat, 0),
                 "95% CI (±)": d["cat_monthly_ci"].get(cat, 0)}
                for cat, total in sorted(d["cat_totals"].items(), key=lambda x: -x[1])]
    st.dataframe(
        pd.DataFrame(cat_rows).style.format(
            {"Total (€)": "€{:,.2f}", "Avg/month": "€{:,.2f}",
             "Std dev": "€{:,.2f}", "95% CI (±)": "€{:,.2f}"}),
        hide_index=True, width="stretch",
    )


# ── The whole spending-chart block, with its two controls ─────────────────────
# One fragment rather than one per control: the controls and the charts they
# affect have to live in the same fragment, or moving a control re-renders the
# control and leaves the charts showing the old state until something else
# triggers a full rerun.
@st.fragment
def _spending_charts(filtered_data, data_key, start_month, end_month, all_months):
    excluded = load_exclusions()

    # The "months look incomplete" offer is rendered BEFORE the multiselect, and
    # that ordering is load-bearing. Accepting it has to set the multiselect's
    # stored value as well as the file — a multiselect ignores its `default`
    # after the first run and would otherwise come back showing the old
    # selection and write it straight back over what we just saved. Streamlit
    # refuses to let session_state for a widget key be set once that widget
    # exists, so the button has to come first.
    # Seed the widget's state once, then never pass `default` again: mixing a
    # default with session-state writes makes Streamlit warn, and the state is
    # what actually survives a rerun anyway.
    if "excluded_months" not in st.session_state:
        st.session_state["excluded_months"] = sorted(excluded)
    # Read the widget's CURRENT value, not the file. The file is written after
    # the multiselect renders, so on the run where you deselect a month the
    # file still holds the old set — keying the banner off it would leave the
    # suggestion a run out of date.
    current = set(st.session_state.get("excluded_months", sorted(excluded)))
    sus = [x for x in _cached_suspect_months(filtered_data, data_key)
           if x["month"] not in current]
    if sus:
        st.markdown(
            f'<div style="padding:9px 14px;margin:2px 0 8px 0;background:#161b22;'
            f'border:1px solid #21262d;border-left:3px solid #d29922;'
            f'border-radius:6px;color:#c9d1d9;font-size:0.88rem">'
            f'<b>{len(sus)} month{"" if len(sus) == 1 else "s"} look incomplete</b>'
            f'&nbsp;— ' + ' &nbsp;·&nbsp; '.join(
                f'<b>{x["month"]}</b> {x["reasons"][0]}' for x in sus[:3])
            + ('' if len(sus) <= 3 else f' &nbsp;·&nbsp; and {len(sus) - 3} more')
            + '</div>', unsafe_allow_html=True)
        # Offered, never applied automatically: an odd-looking month can be
        # perfectly real, and silently dropping data because it was unusual is
        # the failure this whole feature exists to make visible.
        if st.button(f"Leave {'it' if len(sus) == 1 else 'them'} out too",
                     key="excl_suspects"):
            new_excl = sorted(current | {x["month"] for x in sus})
            save_exclusions(new_excl)
            st.session_state["excluded_months"] = new_excl
            st.rerun()

    _c_win, _c_exc = st.columns([2, 3])
    with _c_win:
        choice = st.select_slider(
            "Months shown in the by-category chart",
            options=list(range(1, 13)) + ["Full history"],
            value="Full history", key="by_cat_window",
            format_func=lambda v: v if isinstance(v, str)
            else f"{v} month{'' if v == 1 else 's'}",
        )
    with _c_exc:
        picked = st.multiselect(
            "Months to leave out of the averages",
            options=list(reversed(all_months)), key="excluded_months",
            help="For months that aren't representative — someone else paid, or "
                 "you weren't using the card. They are dropped from these charts "
                 "and the window reaches further back to keep the same number of "
                 "months, so the averages don't quietly get weaker.",
        )

    # Persist the choice, but keep rendering from `picked` — the widget already
    # holds the new value, so no rerun is needed to pick it up. (An
    # st.rerun(scope="fragment") here is also illegal: the fragment runs inside
    # a full script run the first time round, and a fragment-scoped rerun is
    # only allowed during a fragment rerun.)
    if set(picked) != excluded:
        save_exclusions(picked)

    excl = tuple(sorted(set(picked)))
    months = None if choice == "Full history" else int(choice)

    spec = _cached_by_category_spec(filtered_data, data_key, start_month,
                                    end_month, months, excl)
    if spec:
        st.vega_lite_chart(spec=spec, theme=None, width="stretch")
    else:
        st.caption("Nothing to show for this period.")

    # The per-category table belongs to the same set of numbers as the charts,
    # so it is rendered here from exclusion-aware data rather than from the
    # tab-level D_MAIN — otherwise the table and the chart beside it would
    # disagree the moment a month was excluded.
    if excl:
        d_ex = _cached_excluded_process(filtered_data, data_key, start_month,
                                        end_month, excl)
        st.markdown('<div class="section-title">By category</div>',
                    unsafe_allow_html=True)
        st.caption("Excluding " + ", ".join(sorted(excl)) + ".")
        _cat_table(d_ex)

    _sp = _cached_spending_specs(filtered_data, data_key, start_month,
                                 end_month, excl)

    def _panel(key, container=st):
        s = _sp.get(key)
        if s:
            container.vega_lite_chart(spec=s, theme=None, width="stretch")
        else:
            container.caption("Nothing to show for this period.")

    # Same arrangement the single figure had: the wide overview on top, the four
    # detail panels paired in the middle, income vs spending across the bottom.
    _c1, _c2 = st.columns(2)
    _panel("grocery", _c1)
    _panel("dining", _c2)
    _c3, _c4 = st.columns(2)
    _panel("utilities", _c3)
    _panel("cat_totals", _c4)
    _panel("income_spend")


# ── Period comparison ─────────────────────────────────────────────────────────
@st.fragment
def _comparison_section(filtered_data, data_key):
    n = st.select_slider("Compare the last N months against the N before",
                         options=[3, 6, 12], value=6, key="cmp_n",
                         format_func=lambda v: f"{v} months")
    excl = tuple(sorted(load_exclusions()))
    c = _cached_comparison(filtered_data, data_key, n, excl)
    if not c["recent"]:
        st.info("Not enough history yet to compare two periods.")
        return
    if not c["prior"]:
        st.info(f"Only {len(c['recent'])} usable months so far — "
                f"there's no earlier period to compare against yet.")
        return

    if len(c["prior"]) != len(c["recent"]):
        st.caption(
            f"The earlier window holds {len(c['prior'])} months, not {n} — "
            "that's all the history there is once the month in progress and any "
            "excluded months are set aside. The comparison is still fair because "
            "everything here is per month rather than a total."
        )
    spec = comparison_chart(c)
    if spec is None:
        st.caption("Nothing changed by more than a cent.")
        return
    st.vega_lite_chart(spec=spec.to_dict(), theme=None, width="stretch")

    ups = [x for x in c["categories"] if x["delta"] > 0][:3]
    if ups:
        st.caption(
            "Biggest increases: "
            + " · ".join(
                f"**{x['category']}** {x['delta']:+,.0f}/mo"
                + (" (new)" if x["is_new"] else
                   f" ({x['pct']:+.0%})" if x["pct"] is not None else "")
                for x in ups)
            + ". Investments are left out — moving cash into a fund isn't a cost."
        )

    with st.expander("Every category, side by side"):
        st.dataframe(
            pd.DataFrame([{
                "Category": x["category"],
                "Before (€/mo)": x["before"],
                "After (€/mo)": x["after"],
                "Change (€/mo)": x["delta"],
                "Change": ("new" if x["is_new"] else "stopped" if x["stopped"]
                           else f"{x['pct']:+.0%}" if x["pct"] is not None else "—"),
            } for x in c["categories"]]).style.format({
                "Before (€/mo)": "€{:,.2f}", "After (€/mo)": "€{:,.2f}",
                "Change (€/mo)": "{:+,.2f}"}),
            hide_index=True, width="stretch",
        )


# ── Recurring charges & price creep ───────────────────────────────────────────
def _subscriptions_section(filtered_data, data_key):
    subs = _cached_subscriptions(filtered_data, data_key)
    if not subs:
        st.caption("No recurring charges detected yet — this needs at least four "
                   "charges from the same place on a regular cadence.")
        return
    summary = subs_mod.creep_summary(subs)
    live = [s for s in subs if not s["lapsed"]]
    stopped = [s for s in subs if s["lapsed"]]

    m1, m2, m3 = st.columns(3)
    m1.metric("Recurring commitments", f"{summary['n_active']}")
    m2.metric("Costing you", f"€{summary['annual_now']:,.0f}/yr",
              help="Current prices, annualised, for everything still being charged.")
    if summary["annual_extra"] > 0:
        m3.metric("Price rises since", f"+€{summary['annual_extra']:,.0f}/yr",
                  delta=f"{summary['n_risen']} went up", delta_color="inverse",
                  help="What the increases add up to over a year, measured against "
                       "what each of these cost you two years ago.")
    else:
        m3.metric("Price rises", "None")

    def _table(items):
        return pd.DataFrame([
            {"What": s["merchant"][:34],
             "Category": s["category"],
             "Now": s["current"],
             "Was": s["baseline"],
             "Change": s["change_pct"],
             "Per year": s["annual_now"],
             "Last charged": s["last_seen"]}
            for s in items
        ])

    if live:
        st.dataframe(
            _table(live).style.format({
                "Now": "€{:,.2f}", "Was": "€{:,.2f}", "Per year": "€{:,.0f}",
                "Change": lambda v: "—" if v is None else f"{v:+.0%}",
            }),
            hide_index=True, width="stretch",
        )

    risen = [s for s in live if s["change_pct"] and s["change_pct"] > 0.01]
    if risen:
        st.markdown('<div class="section-title">What changed</div>',
                    unsafe_allow_html=True)
        for s in sorted(risen, key=lambda x: -x["change_pct"]):
            steps = " → ".join(
                [f"€{s['steps'][0]['from']:,.2f}"] +
                [f"€{st_['to']:,.2f} ({st_['when']:%b %Y})" for st_ in s["steps"]]
            ) if s["steps"] else f"€{s['baseline']:,.2f} → €{s['current']:,.2f}"
            st.markdown(
                f'<div style="padding:9px 14px;margin-bottom:6px;background:#161b22;'
                f'border:1px solid #21262d;border-left:3px solid #f78166;'
                f'border-radius:6px;color:#c9d1d9">'
                f'<b>{s["merchant"][:40]}</b> &nbsp;{steps}&nbsp; '
                f'<span style="color:#f78166">'
                f'{s["change_pct"]:+.0%} · €{s["annual_delta"]:+,.0f}/yr</span></div>',
                unsafe_allow_html=True)

    if stopped:
        with st.expander(f"No longer being charged ({len(stopped)})"):
            st.caption(
                "Nothing has come out for these in well over a billing cycle. "
                "Either you cancelled them, or a charge hasn't been imported yet — "
                "worth a glance, since a subscription you think you still pay "
                "distorts every forward estimate."
            )
            st.dataframe(
                _table(stopped).style.format({
                    "Now": "€{:,.2f}", "Was": "€{:,.2f}", "Per year": "€{:,.0f}",
                    "Change": lambda v: "—" if v is None else f"{v:+.0%}",
                }),
                hide_index=True, width="stretch",
            )


# ── Cash forecast ─────────────────────────────────────────────────────────────
# A fragment because both controls (horizon, keep-investing) only affect this
# section, and because the whole point of the toggle is to flip between the two
# runways quickly enough to compare them.
@st.fragment
def _forecast_section(filtered_data, data_key, timeline):
    cash = float(timeline[-1]["cash"])
    _c1, _c2 = st.columns([3, 2])
    with _c1:
        horizon = st.slider("Months ahead", 3, 24, 12, key="fc_horizon")
    with _c2:
        keep = st.toggle("Keep investing", value=True, key="fc_investing",
                         help="Contributions leave your current account, so they "
                              "shorten the runway — but unlike rent you can pause "
                              "them. Turn this off to see the runway without them.")

    excl = tuple(sorted(load_exclusions()))
    f = _cached_forecast(filtered_data, data_key, horizon, keep, cash, excl)
    o, r, inc = f["outlook"], f["runway"], f["income"]
    if excl:
        st.caption("Leaving out " + ", ".join(excl) +
                   " — set on the Expense Analysis tab. Excluding an unusually "
                   "cheap month raises the burn rate and shortens the runway.")

    burn = o["living_mean"] + (o["investing_mean"] if keep else 0.0)
    m1, m2, m3 = st.columns(3)
    as_of = f["as_of"]
    days_old = (date.today() - as_of).days
    if days_old > 10:
        st.warning(f"Your data ends on {as_of:%d %b %Y}, {days_old} days ago. Everything here "
                   "counts from that day; import newer transactions to bring it up to date.")
    m1.metric(f"Cash on {as_of:%d %b %Y}", f"€{cash:,.0f}")
    m2.metric("Monthly outgoings", f"€{burn:,.0f}",
              help=f"Living costs €{o['living_mean']:,.0f} "
                   f"(fixed €{o['fixed_mean']:,.0f} + variable €{o['variable_mean']:,.0f})"
                   + (f" plus €{o['investing_mean']:,.0f} invested" if keep else "")
                   + f". Recurring income of €{inc['trickle_per_month']:,.0f}/mo is "
                     "netted off in the runway.")
    if r["months"] is None:
        m3.metric("Runway", "No end in sight",
                  help="Recurring income covers your outgoings.")
    else:
        m3.metric("Runway", f"{r['months']:.0f} months",
                  delta=f"empty ~{r['empty_on']}", delta_color="off",
                  help=f"Between {r['months_lo']:.1f} and {r['months_hi']:.1f} months "
                       "once the spread of your monthly spending is allowed for. "
                       "Assumes no further lump sums arrive.")

    hist = [{"month": t["month"], "cash": t["cash"]} for t in timeline[-6:]]
    spec = forecast_chart(hist, f["projection"])
    if spec is not None:
        st.vega_lite_chart(spec=spec.to_dict(), theme=None, width="stretch")
    st.caption(
        "Solid is what happened, dashed is where it goes if nothing arrives. "
        "The shaded fan is ±1σ of your monthly spending, widened over time."
    )

    # The lump-sum cadence: the single most decision-relevant thing here, and
    # deliberately presented as history to read rather than as a prediction.
    if inc.get("last_lump") and inc.get("next_due"):
        gap_m = inc["typical_gap_days"] / 30.44
        st.markdown(
            f'<div style="padding:10px 14px;background:#161b22;border:1px solid #21262d;'
            f'border-left:3px solid #58a6ff;border-radius:6px;color:#c9d1d9">'
            f'Your last large deposit was <b>€{inc["last_lump"]["amount"]:,.0f}</b> on '
            f'<b>{inc["last_lump"]["date"]:%d %b %Y}</b>. The recent gaps between them '
            f'run about <b>{gap_m:.1f} months</b>, which would put the next one around '
            f'<b>{inc["next_due"]:%b %Y}</b>'
            + (f' — comfortably before the cash runs out (~{r["empty_on"]}).'
               if r["months"] is not None and f'{inc["next_due"]:%Y-%m}' < r["empty_on"]
               else f' — worth watching against the runway above.')
            + '</div>', unsafe_allow_html=True)

    with st.expander("What this is built from"):
        st.caption(
            "Rates are measured over the last 12 complete months — a month in progress "
            "or only partly imported is excluded, since it would look like a cheap one."
        )
        st.dataframe(
            pd.DataFrame([
                {"Category": c["category"], "Per month (€)": c["mean"],
                 "σ (€)": c["sd"], "Months seen": f"{c['months_present']}/12",
                 "Pattern": c["kind"]}
                for c in o["by_category"]
            ]).style.format({"Per month (€)": "€{:,.2f}", "σ (€)": "€{:,.2f}"}),
            hide_index=True, width="stretch",
        )
        if inc["lumps"]:
            st.caption(f"Large deposits on record (≥ €{inc['threshold']:,.0f}):")
            st.dataframe(
                pd.DataFrame([
                    {"Date": l["date"], "Amount (€)": l["amount"],
                     "Description": l["description"][:48]}
                    for l in reversed(inc["lumps"][-10:])
                ]).style.format({"Amount (€)": "€{:,.2f}"}),
                hide_index=True, width="stretch",
            )


# The three allocation donuts. class_key is in the key because the asset-class
# split is built from the per-fund overrides — without it, reclassifying a fund
# would leave that donut showing the old split (the exact bug the PNG version
# had before its key was fixed).
@st.cache_data(show_spinner=False)
def _cached_donut_specs(_filtered_data, data_key, class_key, start_month, end_month):
    return investment_donut_specs(process(_filtered_data,
                                          start_month=start_month, end_month=end_month))


# values_key is the mtime+size of fund_values.json, and it is recomputed INSIDE
# the fragment rather than at the top of the script: saving a snapshot triggers a
# fragment-scoped rerun, which never re-reads a module-level key, so a key taken
# at script level would leave this chart showing the state before your save.
@st.cache_data(show_spinner=False)
def _cached_asset_class_value_spec(_filtered_data, data_key, class_key, values_key,
                                   start_month, end_month):
    d = process(_filtered_data, start_month=start_month, end_month=end_month)
    fund_to_class = dict(zip(d["fund_names"], d["fund_asset_classes"]))
    chart = asset_class_value_donut(latest_snapshot()[1], fund_to_class,
                                    contributed=d["fund_totals"])
    return chart.to_dict() if chart is not None else None


# Takes the contributions alongside the valuation so each wedge can report how
# it has moved against what was paid into it.
@st.cache_data(show_spinner=False)
def _cached_value_donut_spec(_filtered_data, data_key, values_key,
                             start_month, end_month):
    d = process(_filtered_data, start_month=start_month, end_month=end_month)
    chart = fund_value_donut(latest_snapshot()[1], contributed=d["fund_totals"])
    return chart.to_dict() if chart is not None else None


@st.cache_data(show_spinner=False)
def _cached_value_timeline_spec(_filtered_data, data_key, values_key):
    chart = value_vs_contributions_chart(
        value_timeline(_filtered_data, _detect_investment_fund))
    return chart.to_dict() if chart is not None else None


@st.cache_data(show_spinner=False)
def _cached_networth_spec(_filtered_data, data_key):
    chart = net_worth_chart(compute_net_worth_timeline(_filtered_data))
    return chart.to_dict() if chart is not None else None


# Same reasoning as the net worth timeline above — an "over time" trend
# chart needs the complete history to mean anything, so this also ignores
# start_month/end_month and only respects which accounts are selected.
@st.cache_data(show_spinner=False)
def _cached_allocation_spec(_filtered_data, data_key, class_key, as_share):
    chart = allocation_chart(compute_allocation_timeline(_filtered_data), as_share=as_share)
    return chart.to_dict() if chart is not None else None


@st.cache_data(show_spinner=False)
def _cached_global_summary(_all_data, data_key):
    """Same output as storage.global_summary(), built from the data
    get_all_data() already loaded instead of re-reading gastos_global.xlsx
    from disk. The Categories tab renders on every single rerun regardless
    of which tab is visible, and global_summary() was a fresh, uncached
    pd.read_excel() + groupby every time — measured at ~80ms per call, paid
    on every click anywhere in the app, forever, for data already sitting in
    memory a few lines above."""
    if not _all_data:
        return pd.DataFrame()
    df = pd.DataFrame(_all_data, columns=["Date", "Amount", "Description", "Category", "Source"])
    df["Month"] = df["Date"].apply(lambda d: d.strftime("%Y-%m"))
    return (
        df.groupby(["Source", "Month"])
          .agg(Transactions=("Amount", "count"), Total=("Amount", "sum"))
          .reset_index()
    )


# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');
html,body,[class*="css"]{font-family:'DM Sans',sans-serif}
[data-testid="stSidebar"]{background:#0d1117;border-right:1px solid #21262d}
[data-testid="stSidebar"] *{color:#c9d1d9!important}
[data-testid="stSidebar"] label{color:#8b949e!important;font-size:.78rem;letter-spacing:.05em;text-transform:uppercase}
.stApp{background:#0d1117}
.stTabs [data-baseweb="tab-list"]{background:#161b22;border-radius:8px;padding:4px;gap:4px;border:1px solid #21262d}
.stTabs [data-baseweb="tab"]{color:#8b949e;background:transparent;border-radius:6px;font-weight:500;font-size:.88rem;padding:6px 18px}
.stTabs [aria-selected="true"]{background:#21262d!important;color:#58a6ff!important}
.fin-card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:18px 22px;margin-bottom:12px}
.fin-card h4{margin:0 0 4px 0;color:#8b949e;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;font-weight:500}
.fin-card .val{font-size:1.7rem;font-weight:600;color:#c9d1d9;font-family:'DM Mono',monospace}
.fin-card .val.positive{color:#3fb950}
.fin-card .val.negative{color:#ff7b72}
.fin-card .val.neutral{color:#58a6ff}
.section-title{color:#58a6ff;font-size:.7rem;font-weight:600;text-transform:uppercase;letter-spacing:.12em;margin:24px 0 10px 0;padding-bottom:6px;border-bottom:1px solid #21262d}
.txn-card{background:#161b22;border:1px solid #30363d;border-left:3px solid #ffa657;border-radius:6px;padding:14px 18px;margin-bottom:10px}
.txn-desc{font-size:1rem;color:#c9d1d9;font-weight:500}
.txn-meta{font-size:.78rem;color:#8b949e;margin-top:3px;font-family:'DM Mono',monospace}
.detect-box{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:14px 18px;margin:10px 0}
.conf-high{color:#3fb950;font-weight:600}
.conf-med{color:#e3b341;font-weight:600}
.conf-low{color:#ff7b72;font-weight:600}
.stButton>button{background:#238636;color:#fff;border:none;border-radius:6px;font-weight:600;padding:8px 20px;font-size:.88rem}
.stButton>button:hover{background:#2ea043}
/* Heading above a chart. The donut charts render their title here rather than
   inside the Vega spec, because Vega lays a title out above the plot origin and
   it gets clipped when the chart also carries a tall legend. */
.chart-title{color:#c9d1d9;font-size:.95rem;font-weight:600;margin:2px 0 -6px 2px}
/* Vega-Lite's hover tooltip (interactive.py charts). Its stock styling is a
   white card, which flashes against the dark panels, so it gets the same
   palette as .fin-card. Streamlit's own Altair theme would handle this, but
   these charts pass theme=None to keep their custom dark axes/legend. */
#vg-tooltip-element{background:#161b22!important;border:1px solid #30363d!important;
  border-radius:8px!important;color:#c9d1d9!important;font-family:'DM Sans',sans-serif!important;
  font-size:.82rem!important;box-shadow:0 6px 20px rgba(0,0,0,.45)!important;padding:8px 10px!important}
#vg-tooltip-element table tr td.key{color:#8b949e!important;font-weight:500!important;padding-right:10px}
#vg-tooltip-element table tr td.value{color:#f0f6fc!important;font-weight:600!important}
</style>
""", unsafe_allow_html=True)


# ── Session state ──────────────────────────────────────────────────────────────
for k, v in {
    "stage": "upload",
    "raw_rows": [], "cat_index": 0,
    "categorized": [], "pending": [],
    "known_snapshot": {},
    "source": "",
    "parse_result": None,
    "override_date": None, "override_amount": None,
    "override_desc": None, "override_desc2": None,
    "auto_count": 0,
    "confirm_batches": [],
}.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 💰 Finance")
    st.markdown("---")

    try:
        all_data_raw = get_all_data()
    except Exception:
        all_data_raw = None

    SELECTED_SOURCES = None

    if all_data_raw:
        sources_avail = sorted({r[4] for r in all_data_raw})
        st.markdown('<div class="section-title">Accounts</div>', unsafe_allow_html=True)
        if len(sources_avail) > 1:
            sel = st.multiselect("Show accounts", options=sources_avail, default=sources_avail)
            SELECTED_SOURCES = sel if sel else sources_avail
        else:
            SELECTED_SOURCES = sources_avail
            st.caption(f"Account: {sources_avail[0]}")

        filtered_data = [r for r in all_data_raw if r[4] in SELECTED_SOURCES] if SELECTED_SOURCES else all_data_raw
        months_avail  = sorted({r[0].strftime("%Y-%m") for r in filtered_data})

        st.markdown('<div class="section-title">Period</div>', unsafe_allow_html=True)
        start_sel = st.selectbox("From", ["All"] + months_avail, index=0)
        end_sel   = st.selectbox("To",   ["All"] + months_avail, index=len(months_avail))
        START_MONTH = None if start_sel == "All" else start_sel
        END_MONTH   = None if end_sel   == "All" else end_sel
    else:
        START_MONTH = END_MONTH = None
        filtered_data = []
        st.caption("No data yet — import a file first.")

    st.markdown("---")
    if filtered_data:
        st.caption(f"Data up to {max(r[0] for r in filtered_data):%d %b %Y}")
    st.caption(f"Data saved in `{Path(GLOBAL_FILE).name}`")


# The cheap stand-ins for the real data in every cache key below — see the
# "Cache keys" note at the top. DATA_KEY identifies `filtered_data` exactly
# (same file + same accounts ticked = same rows) without touching the rows
# themselves; CLASS_KEY identifies the fund classification overrides, so the
# two charts that depend on them refresh the moment you save a change.
DATA_KEY   = (_file_stamp(GLOBAL_FILE), tuple(SELECTED_SOURCES or ()), len(filtered_data))
CLASS_KEY  = _file_stamp(CLASSIFICATION_FILE)
VALUES_KEY = _file_stamp(VALUES_FILE)

# process() is ~3.4ms, and the Expense Analysis, Investments and Anomalies
# tabs all used to ask the cache for the exact same result on every rerun —
# at ~21ms per lookup. Computing it once here and sharing it is both simpler
# and about six times faster than caching it was.
D_MAIN = (process(filtered_data, start_month=START_MONTH, end_month=END_MONTH)
          if filtered_data else None)


# ── Tabs ───────────────────────────────────────────────────────────────────────
(tab_new, tab_analysis, tab_investments, tab_networth, tab_calendar,
 tab_anomaly, tab_edit, tab_settings) = st.tabs([
    "📥  Add New Month", "📊  Expense Analysis", "💰  Investments", "📈  Net Worth",
    "📅  Calendar", "🔍  Anomalies", "✏️  Fix Categories", "⚙️  Categories",
])



# ── Connect a bank directly ───────────────────────────────────────────────────
# Synced transactions are handed to the SAME parser as an uploaded file and land
# in the same review flow. They are never written to gastos_global.xlsx without
# passing under your eyes first, and duplicate detection means re-syncing a
# period you already have adds nothing.
@st.fragment
def _bank_link_section():
    with st.expander("🏦  Connect a bank (sync instead of uploading)",
                     expanded=False):
        if not bl.has_secrets():
            st.markdown(
                "Transactions can be pulled straight from your bank through "
                "**GoCardless Bank Account Data**, the bank's own open-banking "
                "interface. Your banking password is never involved: you sign in "
                "on your bank's own site and it hands back a token."
            )
            st.markdown(
                "1. Create a free account at **bankaccountdata.gocardless.com**\n"
                "2. Copy your *Secret ID* and *Secret Key* from the user secrets page\n"
                "3. Paste them below — they are written to "
                f"`{bl.SECRETS_FILE.name}` on this computer and never leave it"
            )
            with st.form("gc_keys"):
                sid = st.text_input("Secret ID", type="password")
                skey = st.text_input("Secret Key", type="password")
                if st.form_submit_button("Save keys") and sid.strip() and skey.strip():
                    bl.SECRETS_FILE.write_text(json.dumps(
                        {"secret_id": sid.strip(), "secret_key": skey.strip()},
                        indent=2), encoding="utf-8")
                    st.success(f"Saved to {bl.SECRETS_FILE.name}. "
                               "Add it to .gitignore if this folder is a git repo.")
                    st.rerun(scope="fragment")
            return

        try:
            conns = bl.connections()
        except bl.BankLinkError as exc:
            st.error(str(exc))
            return

        for c in conns:
            cols = st.columns([3, 2, 1, 1])
            with cols[0]:
                st.markdown(f"**{c.get('source') or 'Bank'}** · "
                            f"`{c.get('institution_id', '')[:28]}`")
            with cols[1]:
                if c.get("error"):
                    st.markdown(f":red[{c['error'][:60]}]")
                elif c.get("status") != "LN":
                    st.markdown(":orange[authorisation not finished]")
                elif c.get("expires_in") is not None and c["expires_in"] <= 0:
                    st.markdown(":red[consent expired — link again]")
                elif c.get("expires_in") is not None:
                    st.markdown(f"{len(c['accounts'])} account(s) · "
                                f"{c['expires_in']} days left")
                else:
                    st.markdown(f"{len(c['accounts'])} account(s)")
            with cols[2]:
                if st.button("Sync", key=f"sync_{c['id']}"):
                    _run_sync(c)
            with cols[3]:
                if st.button("Forget", key=f"forget_{c['id']}"):
                    bl.forget_link(c["id"])
                    st.rerun(scope="fragment")

        st.markdown("---")
        if st.button("＋ Link a bank", key="gc_new"):
            st.session_state["gc_picking"] = True
        if st.session_state.get("gc_picking"):
            try:
                inst = bl.institutions("es")
            except bl.BankLinkError as exc:
                st.error(str(exc)); return
            names = {f"{i['name']}": i for i in inst}
            pick = st.selectbox("Which bank?", sorted(names), key="gc_inst")
            label = st.text_input(
                "Save its transactions under which account name?",
                value="BBVA", key="gc_label",
                help="Use the same label you already use for this bank — "
                     "'BBVA', 'MyInvestor' — so synced rows sit alongside the "
                     "ones you imported by hand.")
            if st.button("Get authorisation link", key="gc_go"):
                try:
                    link = bl.start_link(names[pick]["id"])
                except bl.BankLinkError as exc:
                    st.error(str(exc)); return
                bl.remember_link(link, label.strip() or "Bank")
                st.session_state["gc_picking"] = False
                st.success("Link created.")
                st.markdown(
                    f"**[Open this link to authorise with your bank]({link['link']})** "
                    "— you'll sign in on the bank's own site. Come back here and "
                    "press Sync once it's done."
                )


def _run_sync(conn):
    """Fetch, then hand off to the normal import flow rather than saving."""
    since = (date.today() - timedelta(days=bl.DEFAULT_HISTORY_DAYS)).isoformat()
    try:
        with st.spinner(f"Fetching from {conn.get('source')}…"):
            result = bl.sync(conn["id"], date_from=since)
    except bl.BankLinkError as exc:
        st.error(str(exc))
        return
    for e in result.get("errors", []):
        st.warning(e)
    if not result["rows"]:
        st.info("The bank returned no transactions for this period.")
        return
    tmp = Path(tempfile.gettempdir()) / f"bank_sync_{conn['id'][:8]}.csv"
    bl.to_csv(result["rows"], tmp)
    try:
        parsed = detect_and_parse(str(tmp))
    except Exception as exc:
        st.error(f"Could not read the synced data: {exc}")
        return
    st.session_state["parse_result"] = parsed
    d = parsed["detected"]
    st.session_state["override_date"]   = d["date_col"]
    st.session_state["override_amount"] = d["amount_col"]
    st.session_state["override_desc"]   = d["desc_col"]
    st.session_state["override_desc2"]  = d["desc2_col"]
    st.session_state["source"] = result["source"]
    st.session_state["stage"] = "preview"
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — ADD NEW MONTH
# ══════════════════════════════════════════════════════════════════════════════
with tab_new:

    # ── UPLOAD ────────────────────────────────────────────────────────────────
    if st.session_state["stage"] == "upload":
        _bank_link_section()

        st.markdown('<div class="section-title">Upload bank export</div>', unsafe_allow_html=True)
        st.markdown("Upload any bank file — the format is detected automatically. No need to specify the bank.")

        uploaded = st.file_uploader(
            "Bank file (.xlsx, .xls, .csv, .pdf)",
            type=["xlsx", "xls", "csv", "pdf"],
        )

        # ── MANUAL SINGLE TRANSACTION ────────────────────────────────────────
        # For a one-off you don't want to wait to show up in a bank export —
        # goes through the exact same save_to_global() as a file import, so
        # it gets the same duplicate check and lands in the same place.
        with st.expander("✏️  Or add a single transaction manually", expanded=False):
            known_sources = list(globals().get("sources_avail") or [])
            with st.form("manual_txn_form", clear_on_submit=True):
                mc1, mc2 = st.columns(2)
                with mc1:
                    m_date   = st.date_input("Date", value=date.today())
                    m_amount = st.number_input(
                        "Amount (€) — negative for an expense, positive for income",
                        value=0.0, step=1.0, format="%.2f",
                    )
                with mc2:
                    account_options = known_sources + ["Other…"]
                    m_source_sel = st.selectbox("Account", account_options, index=0 if known_sources else 0)
                    m_source = (
                        st.text_input("New account name", placeholder='e.g. "BBVA"')
                        if m_source_sel == "Other…" else m_source_sel
                    )
                    m_category = st.selectbox("Category", load_categories())
                m_desc = st.text_input("Description", placeholder="e.g. Cena con amigos")
                m_repeat = st.checkbox(
                    "Add it even if an identical transaction is already saved",
                    help="For a real repeat, like a second identical bus fare the same day.")
                submitted = st.form_submit_button("💾  Add transaction")

                if submitted:
                    if m_amount == 0:
                        st.warning("Amount can't be zero.")
                    elif not m_desc.strip():
                        st.warning("Please add a short description.")
                    elif not m_source.strip():
                        st.warning("Please choose or type an account.")
                    else:
                        row = pd.DataFrame([{
                            "Date": m_date.strftime("%Y-%m-%d"),
                            "Amount": float(m_amount),
                            "Description": m_desc.strip(),
                            "Category": m_category,
                        }])
                        n = save_to_global(row, source=m_source.strip(),
                                           allow_repeats=m_repeat)
                        if n:
                            st.success(f"✅ Added: {m_date.strftime('%Y-%m-%d')} · €{m_amount:,.2f} · {m_desc.strip()} · {m_source.strip()}")
                        else:
                            st.info(
                                "This looks like it's already in your data (same date, amount, "
                                "description and account) — nothing added. Tick the box "
                                "above if it is a separate purchase."
                            )

        if uploaded:
            suffix = Path(uploaded.name).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(uploaded.read())
                tmp_path = tmp.name

            with st.spinner("Detecting file structure…"):
                try:
                    result = detect_and_parse(tmp_path)
                    st.session_state["parse_result"] = result
                    # Pre-fill overrides with detected values
                    d = result["detected"]
                    st.session_state["override_date"]   = d["date_col"]
                    st.session_state["override_amount"] = d["amount_col"]
                    st.session_state["override_desc"]   = d["desc_col"]
                    st.session_state["override_desc2"]  = d["desc2_col"]
                    st.session_state["stage"] = "preview"
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not parse file: {e}")

    # ── PREVIEW ───────────────────────────────────────────────────────────────
    elif st.session_state["stage"] == "preview":
        result = st.session_state["parse_result"]
        conf   = result["confidence"]
        det    = result["detected"]
        cols   = result["all_columns"]
        raw_df = result["raw_df"]

        st.markdown('<div class="section-title">Auto-detected structure</div>', unsafe_allow_html=True)

        # Confidence badge
        if conf >= 0.7:
            badge = f'<span class="conf-high">● High confidence ({conf:.0%})</span>'
        elif conf >= 0.45:
            badge = f'<span class="conf-med">● Medium confidence ({conf:.0%})</span>'
        else:
            badge = f'<span class="conf-low">● Low confidence ({conf:.0%}) — please verify the columns below</span>'
        st.markdown(badge, unsafe_allow_html=True)

        st.markdown(f"**{result['n_ok']} transactions** parsed from `{len(raw_df)}` rows.")

        # Column selectors
        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a:
            st.session_state["override_date"] = st.selectbox(
                "Date column", cols,
                index=cols.index(det["date_col"]) if det["date_col"] in cols else 0,
            )
        with col_b:
            st.session_state["override_amount"] = st.selectbox(
                "Amount column", cols,
                index=cols.index(det["amount_col"]) if det["amount_col"] in cols else 0,
            )
        with col_c:
            st.session_state["override_desc"] = st.selectbox(
                "Description column", cols,
                index=cols.index(det["desc_col"]) if det["desc_col"] in cols else 0,
            )
        with col_d:
            none_opt = ["(none)"] + cols
            default_d2 = det["desc2_col"] if det["desc2_col"] else "(none)"
            sel_d2 = st.selectbox(
                "2nd description (optional)", none_opt,
                index=none_opt.index(default_d2) if default_d2 in none_opt else 0,
            )
            st.session_state["override_desc2"] = None if sel_d2 == "(none)" else sel_d2

        # Live preview with selected columns
        preview_rows = reparse(
            raw_df,
            st.session_state["override_date"],
            st.session_state["override_amount"],
            st.session_state["override_desc"],
            st.session_state["override_desc2"],
        )
        if preview_rows:
            st.dataframe(pd.DataFrame(preview_rows[:6]), hide_index=True, width="stretch")
        else:
            st.warning("No rows could be parsed with these column selections.")

        st.markdown("")
        src_col, btn_col, back_col = st.columns([3, 2, 1])
        with src_col:
            st.session_state["source"] = st.text_input(
                "Account label (saved in Excel)",
                value=st.session_state.get("source", ""),
                placeholder='e.g. "BBVA", "MyInvestor"',
            )
        with btn_col:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("✓  Looks good — start categorising", width="stretch"):
                if not st.session_state["source"].strip():
                    st.warning("Please enter an account label.")
                elif not preview_rows:
                    st.error("No rows could be parsed. Adjust the column selections.")
                else:
                    known = load_known_merchants()
                    st.session_state["known_snapshot"] = dict(known)
                    categorized, pending = [], []
                    for row in preview_rows:
                        cat = find_category(row["description"], known)
                        if cat:
                            categorized.append({**row, "category": cat})
                        else:
                            pending.append(row)
                    st.session_state["categorized"] = categorized
                    st.session_state["pending"]     = pending
                    st.session_state["cat_index"]   = 0
                    st.session_state["auto_count"]  = len(categorized)
                    st.session_state["stage"]       = "categorizing" if pending else "review"
                    st.rerun()
        with back_col:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("← Back", width="stretch"):
                st.session_state["stage"] = "upload"
                st.session_state["parse_result"] = None
                st.rerun()

    # ── CATEGORISING ──────────────────────────────────────────────────────────
    elif st.session_state["stage"] == "categorizing":
        pending = st.session_state["pending"]
        idx     = st.session_state["cat_index"]
        CATEGORIES = load_categories()

        if idx >= len(pending):
            st.session_state["stage"] = "review"
            st.rerun()

        row   = pending[idx]
        total = len(pending)
        auto  = st.session_state["auto_count"]

        st.progress(idx / total if total else 1.0,
                    text=f"Categorising unknowns · {idx}/{total}")

        colour = "#3fb950" if row["amount"] >= 0 else "#ff7b72"
        sign   = "+" if row["amount"] >= 0 else ""
        st.markdown(f"""
        <div class="txn-card">
            <div class="txn-desc">{row['description']}</div>
            <div class="txn-meta">{row['date']} &nbsp;·&nbsp;
            <span style="color:{colour}">{sign}{abs(row['amount']):,.2f} €</span></div>
        </div>
        """, unsafe_allow_html=True)

        col_cat, col_kw = st.columns([2, 1])
        with col_cat:
            chosen = st.selectbox("Category", CATEGORIES, key=f"cat_{idx}")
        with col_kw:
            kw = st.text_input("Keyword to remember (optional)",
                               placeholder="e.g. voltio", key=f"kw_{idx}").strip().lower()

        c1, c2, c3 = st.columns([2, 1, 1])
        with c1:
            if st.button("✓  Confirm & next", width="stretch"):
                pending_before = list(pending)   # snapshot for ← Back
                n_auto_resolved = 0
                st.session_state["categorized"].append({**row, "category": chosen, "keyword": kw or None})

                if kw:
                    known = load_known_merchants()
                    known[kw] = chosen
                    save_known_merchants(known)
                    st.session_state["known_snapshot"][kw] = chosen

                    # The whole "pending" queue was split off at the start of
                    # this import, using the merchants known back then. If the
                    # same merchant shows up again later in this same file, it
                    # was already sitting in "pending" and would ask again —
                    # even though you just taught it. Re-check the rest of the
                    # queue now and auto-resolve anything that newly matches.
                    remaining     = st.session_state["pending"][idx + 1:]
                    still_pending = []
                    for r in remaining:
                        c = find_category(r["description"], st.session_state["known_snapshot"])
                        if c:
                            st.session_state["categorized"].append({**r, "category": c, "keyword": None})
                            n_auto_resolved += 1
                        else:
                            still_pending.append(r)
                    st.session_state["auto_count"] += n_auto_resolved
                    st.session_state["pending"] = pending[:idx + 1] + still_pending

                st.session_state["confirm_batches"].append({
                    "count": 1 + n_auto_resolved,
                    "n_auto": n_auto_resolved,
                    "pending_before": pending_before,
                    "keyword": kw or None,
                })
                st.session_state["cat_index"] += 1
                st.rerun()
        with c2:
            if st.button("Skip → Other", width="stretch"):
                st.session_state["categorized"].append({**row, "category": "Other", "keyword": None})
                st.session_state["confirm_batches"].append({
                    "count": 1, "n_auto": 0,
                    "pending_before": list(pending), "keyword": None,
                })
                st.session_state["cat_index"] += 1
                st.rerun()
        with c3:
            if idx > 0 and st.button("← Back", width="stretch"):
                batch = st.session_state["confirm_batches"].pop()
                n = batch["count"]
                # The manually-confirmed row is always first in the batch;
                # any rows after it were auto-resolved as a side effect.
                undone = st.session_state["categorized"][-n:]
                st.session_state["categorized"] = st.session_state["categorized"][:-n]
                st.session_state["pending"] = batch["pending_before"]
                if batch["n_auto"]:
                    st.session_state["auto_count"] -= batch["n_auto"]
                kw_to_forget = batch["keyword"] or undone[0].get("keyword")
                if kw_to_forget:
                    known = load_known_merchants()
                    known.pop(kw_to_forget, None)
                    save_known_merchants(known)
                    st.session_state["known_snapshot"].pop(kw_to_forget, None)
                st.session_state["cat_index"] -= 1
                st.rerun()

        st.caption(f"Auto: {auto}  ·  Answered: {idx}  ·  Remaining: {total - idx}")

    # ── REVIEW ────────────────────────────────────────────────────────────────
    if st.session_state["stage"] in ("review", "done"):
        CATEGORIES = load_categories()
        cats = st.session_state["categorized"]
        df = pd.DataFrame([{
            "Date": r["date"], "Amount": r["amount"],
            "Description": r["description"], "Category": r["category"],
        } for r in cats]).sort_values("Date", ascending=False).reset_index(drop=True)

        c1, c2, c3 = st.columns(3)
        n_auto = st.session_state["auto_count"]
        with c1:
            st.markdown(f'<div class="fin-card"><h4>Total</h4><div class="val neutral">{len(df)}</div></div>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div class="fin-card"><h4>Auto-categorised</h4><div class="val positive">{n_auto}</div></div>', unsafe_allow_html=True)
        with c3:
            st.markdown(f'<div class="fin-card"><h4>Manual</h4><div class="val">{len(df)-n_auto}</div></div>', unsafe_allow_html=True)

        st.markdown('<div class="section-title">Review — edit if needed</div>', unsafe_allow_html=True)
        edited = st.data_editor(
            df,
            column_config={
                "Date":        st.column_config.TextColumn("Date", width="small"),
                "Amount":      st.column_config.NumberColumn("Amount", format="€%.2f"),
                "Description": st.column_config.TextColumn("Description", width="large"),
                "Category":    st.column_config.SelectboxColumn("Category", options=CATEGORIES),
            },
            hide_index=True, width="stretch", num_rows="fixed",
        )

        if st.session_state["stage"] == "review":
            if st.button("💾  Save to global Excel"):
                n = save_to_global(edited, source=st.session_state["source"])
                st.session_state["last_save_added"]   = n
                st.session_state["last_save_skipped"] = len(edited) - n
                if n >= 0:
                    st.session_state["stage"] = "done"
                    st.rerun()

        if st.session_state["stage"] == "done":
            added   = st.session_state.get("last_save_added")
            skipped = st.session_state.get("last_save_skipped")
            if skipped:
                st.success(
                    f"✅ Saved. {added} new transaction(s) added, "
                    f"{skipped} already existed and were skipped as duplicates."
                )
            else:
                st.success("✅ Saved. Switch to **📊 Expense Analysis** to see the updated dashboard.")
            if st.button("↩  Import another file"):
                for k in ("stage", "raw_rows", "cat_index", "categorized",
                          "pending", "parse_result"):
                    st.session_state[k] = {"stage": "upload"}.get(k, [] if "rows" in k or k in ("pending","categorized") else (0 if k == "cat_index" else None))
                st.session_state["stage"] = "upload"
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — EXPENSE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
with tab_analysis:
    if st.session_state["stage"] in ("preview", "categorizing"):
        # Streamlit runs every tab's code on every rerun, even hidden ones.
        # Skip the whole analysis (stats + 2 matplotlib charts) while you're
        # mid-import — nothing here can change until you save, and this was
        # the main thing making each categorising click feel slow.
        st.info("Finish importing on **📥 Add New Month** to see the updated analysis here.")
    elif not filtered_data:
        st.info("No data yet. Go to **📥 Add New Month** to import your first file.")
    else:
        d = D_MAIN
        if not d["all_months_str"]:
            st.warning("No transactions in the selected period.")
        else:
            period = f"{d['all_months_str'][0]} → {d['all_months_str'][-1]}"

            st.markdown('<div class="section-title">Summary · ' + period + '</div>', unsafe_allow_html=True)
            k1, k2, k3, k4 = st.columns(4)
            with k1:
                st.markdown(f'''<div class="fin-card"><h4>Total income</h4><div class="val positive">€{d["total_income"]:,.0f}</div></div>''', unsafe_allow_html=True)
            with k2:
                st.markdown(f'''<div class="fin-card"><h4>Total spending</h4><div class="val negative">€{d["total_spending"]:,.0f}</div></div>''', unsafe_allow_html=True)
            with k3:
                cls = "positive" if d["net_worth"] >= 0 else "negative"
                st.markdown(f'''<div class="fin-card"><h4>Net</h4><div class="val {cls}">€{d["net_worth"]:,.0f}</div></div>''', unsafe_allow_html=True)
            with k4:
                st.markdown(f'''<div class="fin-card"><h4>Avg/month (last 12)</h4><div class="val neutral">€{d["avg_last12"]:,.0f}</div></div>''', unsafe_allow_html=True)

            st.markdown('<div class="section-title">Net worth composition</div>', unsafe_allow_html=True)
            b1, b2 = st.columns(2)
            with b1:
                st.markdown(f'''<div class="fin-card"><h4>💵 Cash</h4><div class="val neutral">€{d["cash_worth"]:,.0f}</div><div style="color:#8b949e;font-size:.8rem;margin-top:2px">{d["cash_pct"]:.1f}% of net worth</div></div>''', unsafe_allow_html=True)
            with b2:
                st.markdown(f'''<div class="fin-card"><h4>📈 Invested</h4><div class="val neutral">€{d["net_investments"]:,.0f}</div><div style="color:#8b949e;font-size:.8rem;margin-top:2px">{d["invested_pct"]:.1f}% of net worth</div></div>''', unsafe_allow_html=True)
            _cash_w = max(d["cash_pct"], 0.0)
            _inv_w  = max(d["invested_pct"], 0.0)
            st.markdown(f'''
<div style="display:flex;height:10px;border-radius:6px;overflow:hidden;margin:-6px 0 6px 0;background:#21262d">
  <div style="width:{_cash_w:.2f}%;background:#3fb950"></div>
  <div style="width:{_inv_w:.2f}%;background:#58a6ff"></div>
</div>
<div style="display:flex;gap:16px;font-size:.75rem;color:#8b949e;margin-bottom:16px">
  <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#3fb950;margin-right:4px"></span>Cash</span>
  <span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:#58a6ff;margin-right:4px"></span>Invested</span>
</div>''', unsafe_allow_html=True)

            cash_by_bank = d.get("cash_by_bank", {})
            invested_by_bank = d.get("account_totals", {})
            banks = sorted(set(cash_by_bank) | set(invested_by_bank))
            if banks:
                st.markdown("**By bank**")
                bank_rows = []
                for bank in banks:
                    cash_b = cash_by_bank.get(bank, 0.0)
                    inv_b  = invested_by_bank.get(bank, 0.0)
                    bank_rows.append({
                        "Bank":            bank,
                        "Cash (€)":        cash_b,
                        "Invested (€)":    inv_b,
                        "Total (€)":       cash_b + inv_b,
                        "% invested":      inv_b / (cash_b + inv_b) * 100 if (cash_b + inv_b) else 0,
                    })
                st.dataframe(
                    pd.DataFrame(bank_rows).style.format({
                        "Cash (€)":     "€{:,.2f}",
                        "Invested (€)": "€{:,.2f}",
                        "Total (€)":    "€{:,.2f}",
                        "% invested":   "{:.1f}%",
                    }),
                    hide_index=True, width="stretch",
                )
                st.caption(
                    "Cash per bank = money that ever flowed into that account (income, "
                    "interest, self-transfers, spending) minus what was spent there and "
                    "minus what left to become an investment. It reconstructs each "
                    "account's running balance from your transaction history, not a "
                    "live bank balance — so it can drift slightly if a recent transfer's "
                    "other leg hasn't been imported yet."
                )

            if not load_exclusions():
                st.markdown('<div class="section-title">By category</div>',
                            unsafe_allow_html=True)
                _cat_table(d)

            st.markdown('<div class="section-title">Charts</div>', unsafe_allow_html=True)
            st.caption(
                "Hover any bar for its exact figures · click a legend entry to "
                "isolate that series across the whole history."
            )
            _spending_charts(filtered_data, DATA_KEY, START_MONTH, END_MONTH,
                             d["all_months_str"])

            st.markdown('<div class="section-title">What changed</div>',
                        unsafe_allow_html=True)
            st.caption(
                "Two stretches of time side by side, per month rather than as "
                "totals so that excluded months don't distort the comparison. "
                "Ranked by euros moved — the change you'd actually act on, not "
                "the biggest category or the biggest percentage."
            )
            _comparison_section(filtered_data, DATA_KEY)

            st.markdown('<div class="section-title">Recurring charges</div>',
                        unsafe_allow_html=True)
            st.caption(
                "Charges that repeat on a regular cadence for a steady amount — and, "
                "more to the point, when one of them quietly went up. Measured over "
                "the last two years."
            )
            _subscriptions_section(filtered_data, DATA_KEY)

            if d.get("total_invested", 0) > 0:
                st.caption("📈 See the **💰 Investments** tab for the full breakdown by fund, bank and asset class.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — INVESTMENTS
# ══════════════════════════════════════════════════════════════════════════════
# Allocation over time, as an interactive Vega-Lite chart (see interactive.py).
# The € / share toggle is the thing a static image could never give you: the
# same data answers "how much do I hold?" and "is my mix drifting?", and a PNG
# has to pick one. It lives in a fragment so flipping between them re-renders
# only this section instead of the whole app.
@st.fragment
def _allocation_over_time(filtered_data, data_key, class_key):
    mode = st.segmented_control(
        "View", ["Amount (€)", "Share of portfolio"],
        default="Amount (€)", key="alloc_mode", label_visibility="collapsed",
    )
    as_share = mode == "Share of portfolio"
    spec = _cached_allocation_spec(filtered_data, data_key, class_key, as_share)
    if spec:
        st.vega_lite_chart(spec=spec, theme=None, width="stretch")
        st.caption(
            "Hover any month for the exact split · click a legend entry to "
            "isolate an asset class · drag sideways to zoom, double-click to reset."
        )
    else:
        st.caption("No investment data found.")



# ── Value & returns ───────────────────────────────────────────────────────────
# Everything else on this tab is derived from transactions, which only ever say
# how much money went IN. This is the one place the app can learn what the money
# is now WORTH, and it has to ask: most banks report
# contributions without a unit quantity attached, so there is no
# honest way to derive the value automatically. See valuation.py.
#
# A fragment because entering values is a fiddly, multi-keystroke interaction
# and it should not re-run the other six tabs on every edit.
@st.fragment
def _value_and_returns(filtered_data, fund_totals):
    snaps = load_snapshots()
    last_month, last_values = latest_snapshot(snaps)

    months_for_entry = sorted({*(snaps or {}), date.today().strftime("%Y-%m")}, reverse=True)
    c_sel, c_new = st.columns([2, 3])
    with c_sel:
        entry_month = st.selectbox(
            "Valuation month", months_for_entry,
            index=0, key="val_month",
            help="Record what each holding was worth at the end of this month.",
        )
    with c_new:
        st.markdown("<br>", unsafe_allow_html=True)
        st.caption(
            "Look the figures up in your bank or broker app and type them in. "
            "Pre-filled with your most recent snapshot, so usually you are only "
            "correcting the ones that moved."
        )

    existing = dict(snaps.get(entry_month, {}))
    seed = existing or last_values
    rows = [{"Fund": name,
             "Contributed (€)": round(amount, 2),
             "Value (€)": round(float(seed.get(name, 0.0)), 2)}
            for name, amount in fund_totals.items()]
    if not rows:
        st.caption("No investment transactions in range.")
        return

    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True, width="stretch", key="val_editor",
        column_config={
            "Fund": st.column_config.TextColumn(disabled=True),
            "Contributed (€)": st.column_config.NumberColumn(
                disabled=True, format="€%.2f",
                help="What you paid in — from your transactions."),
            "Value (€)": st.column_config.NumberColumn(
                # step MUST be 0.01, not a "nice" increment: Streamlit validates
                # typed entries against the step, so step=10 silently refuses
                # anything that isn't a multiple of ten — you simply cannot type
                # the cents off a real balance.
                min_value=0.0, step=0.01, format="€%.2f",
                help="What it is worth now. Leave 0 to omit a holding."),
        },
    )

    b1, b2, _ = st.columns([1, 1, 3])
    with b1:
        if st.button("💾 Save valuation", width="stretch", key="val_save"):
            save_snapshot(entry_month,
                          {r["Fund"]: r["Value (€)"] for _, r in edited.iterrows()})
            st.success(f"Saved valuation for {entry_month}.")
            # App-scoped on purpose: the current-value donut in the Allocation
            # section below reads VALUES_KEY, which is computed at script level
            # and would not be refreshed by a fragment-only rerun.
            st.rerun()
    with b2:
        if existing and st.button("🗑 Delete", width="stretch", key="val_del"):
            delete_snapshot(entry_month)
            st.rerun()

    res = compute_returns(filtered_data, _detect_investment_fund)
    if not res["month"]:
        st.info(
            "No valuation recorded yet. Fill in the **Value (€)** column above and "
            "save — returns, and the difference between what you put in and what "
            "it is worth, appear here once there is something to compare against."
        )
        return

    tot = res["totals"]
    st.markdown(
        f'<div class="section-title">Returns · valued {res["month"]}</div>',
        unsafe_allow_html=True)
    r1, r2, r3, r4 = st.columns(4)
    gain_cls = "positive" if tot["gain"] >= 0 else "negative"
    # Sign goes OUTSIDE the euro sign: formatting a negative straight into
    # f"€{gain:,.0f}" yields "€-18,466", which reads as a currency oddity rather
    # than as a loss.
    sign = "+" if tot["gain"] >= 0 else "−"
    with r1:
        st.markdown(f'<div class="fin-card"><h4>Contributed</h4>'
                    f'<div class="val neutral">€{tot["contributed"]:,.0f}</div></div>',
                    unsafe_allow_html=True)
    with r2:
        st.markdown(f'<div class="fin-card"><h4>Current value</h4>'
                    f'<div class="val neutral">€{tot["value"]:,.0f}</div></div>',
                    unsafe_allow_html=True)
    with r3:
        pct = f'{tot["simple_return"]*100:+.1f}%' if tot["simple_return"] is not None else "—"
        st.markdown(f'<div class="fin-card"><h4>Gain / loss</h4>'
                    f'<div class="val {gain_cls}">{sign}€{abs(tot["gain"]):,.0f}</div>'
                    f'<div style="color:#8b949e;font-size:.8rem">{pct} on money in</div></div>',
                    unsafe_allow_html=True)
    with r4:
        x = tot["xirr"]
        xs = f'{x*100:+.1f}%' if x is not None else "—"
        xcls = "positive" if (x or 0) >= 0 else "negative"
        st.markdown(f'<div class="fin-card"><h4>Annualised (XIRR)</h4>'
                    f'<div class="val {xcls}">{xs}</div>'
                    f'<div style="color:#8b949e;font-size:.8rem">money-weighted</div></div>',
                    unsafe_allow_html=True)

    st.caption(
        "**Gain / loss** divides the total gain by everything you have paid in, as "
        "though every euro had been invested for the same length of time — it "
        "hasn't. **XIRR** is the annual rate that makes your actual dated "
        "contributions balance against today's value, so money added recently is "
        "not credited with returns it was never there to earn. When the two "
        "disagree, XIRR is the one to trust."
    )

    detail = [{
        "Fund": f["fund"],
        "Contributed (€)": f["contributed"],
        "Value (€)": f["value"],
        "Gain (€)": f["gain"],
        "Return": (f"{f['simple_return']*100:+.1f}%"
                   if f["simple_return"] is not None else "—"),
        "Annualised": (f"{f['xirr']*100:+.1f}%" if f["xirr"] is not None else "—"),
    } for f in res["funds"] if f["contributed"] or f["value"]]
    st.dataframe(
        pd.DataFrame(detail), hide_index=True, width="stretch",
        column_config={
            "Contributed (€)": st.column_config.NumberColumn(format="€%.2f"),
            "Value (€)":       st.column_config.NumberColumn(format="€%.2f"),
            "Gain (€)":        st.column_config.NumberColumn(format="€%.2f"),
        },
    )

    spec = _cached_value_timeline_spec(filtered_data, DATA_KEY, _file_stamp(VALUES_FILE))
    if spec:
        st.vega_lite_chart(spec=spec, theme=None, width="stretch")
        st.caption(
            "The filled area is what you have paid in, known for every month. The "
            "line is what it was worth, drawn only where you recorded a snapshot — "
            "gaps are months you didn't measure, not months nothing happened. The "
            "distance between the two is your gain."
        )

    unvalued = [f["fund"] for f in res["funds"] if f["contributed"] > 0 and not f["valued"]]
    if unvalued:
        st.caption("⚠️ Not valued in this snapshot, so excluded from the totals above: "
                   + ", ".join(unvalued))


with tab_investments:
    if st.session_state["stage"] in ("preview", "categorizing"):
        st.info("Finish importing on **📥 Add New Month** to see the updated analysis here.")
    elif not filtered_data:
        st.info("No data yet. Go to **📥 Add New Month** to import your first file.")
    else:
        d = D_MAIN
        if d.get("total_invested", 0) <= 0:
            st.info("No investment transactions found in the selected period.")
        else:
            period = f"{d['all_months_str'][0]} → {d['all_months_str'][-1]}" if d["all_months_str"] else ""
            st.markdown('<div class="section-title">Summary · ' + period + '</div>', unsafe_allow_html=True)

            i1, i2, i3 = st.columns(3)
            with i1:
                st.markdown(f'''<div class="fin-card"><h4>Total invested</h4><div class="val neutral">€{d["total_invested"]:,.0f}</div></div>''', unsafe_allow_html=True)
            with i2:
                n_positions = len(d["fund_names"])
                st.markdown(f'''<div class="fin-card"><h4>Fund × bank positions</h4><div class="val neutral">{n_positions}</div></div>''', unsafe_allow_html=True)
            with i3:
                st.markdown(f'''<div class="fin-card"><h4>% of net worth</h4><div class="val neutral">{d["invested_pct"]:.1f}%</div></div>''', unsafe_allow_html=True)

            st.markdown('<div class="section-title">Value &amp; returns</div>', unsafe_allow_html=True)
            _value_and_returns(filtered_data, d["fund_totals"])

            st.markdown('<div class="section-title">Allocation</div>', unsafe_allow_html=True)
            st.caption(
                "Hover any slice for its exact amount and share · click a legend "
                "entry to pick that holding out of the ring."
            )
            _dn = _cached_donut_specs(filtered_data, DATA_KEY, CLASS_KEY,
                                      START_MONTH, END_MONTH)

            def _title(container, text):
                container.markdown(f'<div class="chart-title">{text}</div>',
                                   unsafe_allow_html=True)

            def _donut_panel(key, container, text):
                spec = _dn.get(key)
                if spec:
                    _title(container, text)
                    container.vega_lite_chart(spec=spec, theme=None, width="stretch")

            # Two rows, read top-to-bottom as one comparison: what you PAID IN
            # on the first row, what it is WORTH on the second. Keeping fund and
            # asset class side by side within each row means the two questions
            # ("which holding?" and "which kind of asset?") are answered on the
            # same footing at the same moment.
            _r1c1, _r1c2 = st.columns(2)
            _donut_panel("fund", _r1c1, "By fund — contributed")
            _donut_panel("account", _r1c2, "By account — contributed")

            _val_donut = _cached_value_donut_spec(
                filtered_data, DATA_KEY, VALUES_KEY, START_MONTH, END_MONTH)
            _ac_value = _cached_asset_class_value_spec(
                filtered_data, DATA_KEY, CLASS_KEY, VALUES_KEY, START_MONTH, END_MONTH)

            _r2c1, _r2c2 = st.columns(2)
            if _val_donut:
                _title(_r2c1, "By fund — current value")
                _r2c1.vega_lite_chart(spec=_val_donut, theme=None, width="stretch")
                _r2c1.caption(
                    "**▲▼ %** is the fund's return on what you paid into it. "
                    "**↗↘ pp** is how its share of the ring has shifted against "
                    "the contributions ring above, in percentage points. The two "
                    "can point opposite ways — a fund can make money and still "
                    "lose share if the rest grew faster."
                )
            else:
                _r2c1.caption(
                    "Record a valuation under **Value & returns** above to see "
                    "the same split weighted by current value."
                )

            if _ac_value:
                _title(_r2c2, "By asset class — current value")
                # Value-weighted once a valuation exists: a contributions-based
                # split can't move unless you buy something, so it never shows
                # the drift that actually changes your exposure.
                _r2c2.vega_lite_chart(spec=_ac_value, theme=None, width="stretch")
                _r2c2.caption(
                    "Same two markers, per asset class: **▲▼ %** is the return, "
                    "**↗↘ pp** the drift in its share of the portfolio. This is "
                    "the ring that moves as markets move, not only when you buy."
                )
            else:
                _donut_panel("asset_class", _r2c2, "By asset class — contributed")
                _r2c2.caption(
                    "Weighted by contributions. Record a valuation under "
                    "**Value & returns** above and this switches to your real, "
                    "current asset mix."
                )

            st.markdown('<div class="section-title">Allocation over time</div>', unsafe_allow_html=True)
            st.caption(
                "Shows your complete history, for whichever accounts are selected — "
                "not affected by the Period filter above, same as the Net Worth tab."
            )
            _allocation_over_time(filtered_data, DATA_KEY, CLASS_KEY)

            st.markdown('<div class="section-title">Detail by fund</div>', unsafe_allow_html=True)
            rows = []
            for f, acc, ac, it, amt, n, fd, ld in zip(
                d["fund_names"], d["fund_accounts"], d["fund_asset_classes"], d["fund_interest_types"],
                d["fund_amounts"], d["fund_txn_counts"], d["fund_first_dates"], d["fund_last_dates"],
            ):
                rows.append({
                    "Fund":            f,
                    "Bank":            acc,
                    "Asset class":     ac,
                    "Interest type":   it or "—",
                    "Total (€)":       amt,
                    "% of portfolio":  amt / d["total_invested"] * 100 if d["total_invested"] else 0,
                    "Contributions":   n,
                    "First":           fd.strftime("%Y-%m-%d") if fd else "",
                    "Last":            ld.strftime("%Y-%m-%d") if ld else "",
                })
            st.dataframe(
                pd.DataFrame(rows).style.format({
                    "Total (€)":      "€{:,.2f}",
                    "% of portfolio": "{:.1f}%",
                }),
                hide_index=True, width="stretch",
            )

            if "Unspecified" in d["asset_class_totals"]:
                st.caption(
                    "ℹ️ **Unspecified** = not classified yet. Use the form below to set it "
                    "yourself — for a generic bank export (e.g. BBVA's 'Contributions to "
                    "investment funds', or MyInvestor's automatic portfolio top-ups) the "
                    "program can't tell Fixed income from Equity on its own."
                )

            # ── Manual classification ─────────────────────────────────────────
            st.markdown('<div class="section-title">Classify a fund</div>', unsafe_allow_html=True)
            st.caption(
                "Override the auto-detected asset class for a fund (and, for Fixed income, "
                "its interest type). Saved permanently — applies to all its transactions, "
                "past and future."
            )
            unique_funds = sorted(set(d["fund_names"]))
            fc1, fc2, fc3, fc4 = st.columns([2, 1.4, 1.2, 0.8])
            with fc1:
                sel_fund = st.selectbox("Fund", unique_funds, key="inv_class_fund")
            cur_default = "Unspecified"
            for f, ac in zip(d["fund_names"], d["fund_asset_classes"]):
                if f == sel_fund:
                    cur_default = ac
                    break
            cur_ac, cur_it = get_classification(sel_fund, default_asset_class=cur_default)
            with fc2:
                sel_ac = st.selectbox(
                    "Asset class", ASSET_CLASSES,
                    index=ASSET_CLASSES.index(cur_ac) if cur_ac in ASSET_CLASSES else 0,
                    key="inv_class_asset",
                )
            with fc3:
                sel_it = None
                if sel_ac == "Fixed income":
                    sel_it = st.selectbox(
                        "Interest type", INTEREST_TYPES,
                        index=INTEREST_TYPES.index(cur_it) if cur_it in INTEREST_TYPES else 0,
                        key="inv_class_interest",
                    )
                else:
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.caption("(only for Fixed income)")
            with fc4:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("💾 Save", width="stretch", key="inv_class_save"):
                    set_classification(sel_fund, sel_ac, sel_it)
                    st.success(f"Saved: {sel_fund} → {sel_ac}" + (f" ({sel_it})" if sel_it else ""))
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — NET WORTH
# ══════════════════════════════════════════════════════════════════════════════
with tab_networth:
    if st.session_state["stage"] in ("preview", "categorizing"):
        st.info("Finish importing on **📥 Add New Month** to see your net worth history here.")
    elif not filtered_data:
        st.info("No data yet. Go to **📥 Add New Month** to import your first file.")
    else:
        timeline = compute_net_worth_timeline(filtered_data)
        if not timeline:
            st.info("No transactions found for the selected accounts.")
        else:
            first, last = timeline[0], timeline[-1]
            period = f"{first['month']} → {last['month']}"
            st.markdown('<div class="section-title">Summary · ' + period + '</div>', unsafe_allow_html=True)
            st.caption(
                "This tab always shows your **complete** history, for whichever accounts "
                "are selected above — unlike the other tabs, it isn't affected by the "
                "Period filter in the sidebar. A growth-over-time chart wouldn't mean much "
                "if it could be cut off partway through."
            )

            n1, n2, n3, n4 = st.columns(4)
            with n1:
                cls = "positive" if last["net_worth"] >= 0 else "negative"
                st.markdown(f'''<div class="fin-card"><h4>Net worth now</h4><div class="val {cls}">€{last["net_worth"]:,.0f}</div></div>''', unsafe_allow_html=True)
            with n2:
                st.markdown(f'''<div class="fin-card"><h4>💵 Cash</h4><div class="val neutral">€{last["cash"]:,.0f}</div></div>''', unsafe_allow_html=True)
            with n3:
                st.markdown(f'''<div class="fin-card"><h4>📈 Invested</h4><div class="val neutral">€{last["invested"]:,.0f}</div></div>''', unsafe_allow_html=True)
            with n4:
                change = last["net_worth"] - first["net_worth"]
                cls2 = "positive" if change >= 0 else "negative"
                sign = "+" if change >= 0 else ""
                st.markdown(f'''<div class="fin-card"><h4>Since {first["month"]}</h4><div class="val {cls2}">{sign}€{change:,.0f}</div></div>''', unsafe_allow_html=True)

            if len(timeline) > 12:
                ref = timeline[-13]
                chg12 = last["net_worth"] - ref["net_worth"]
                sign3 = "+" if chg12 >= 0 else ""
                arrow = "📈" if chg12 >= 0 else "📉"
                st.caption(f"{arrow} {sign3}€{chg12:,.0f} over the last 12 months (since {ref['month']}).")

            st.markdown('<div class="section-title">Chart</div>', unsafe_allow_html=True)
            st.caption(
                "Hover any month for its exact figures · click a legend entry to "
                "isolate that band · drag sideways to zoom, double-click to reset."
            )
            _nw_spec = _cached_networth_spec(filtered_data, DATA_KEY)
            if _nw_spec:
                st.vega_lite_chart(spec=_nw_spec, theme=None, width="stretch")

            with st.expander("📋  Month-by-month detail"):
                table_rows = [
                    {
                        "Month":         t["month"],
                        "Cash (€)":      t["cash"],
                        "Invested (€)":  t["invested"],
                        "Net worth (€)": t["net_worth"],
                    }
                    for t in reversed(timeline)
                ]
                st.dataframe(
                    pd.DataFrame(table_rows).style.format({
                        "Cash (€)":      "€{:,.2f}",
                        "Invested (€)":  "€{:,.2f}",
                        "Net worth (€)": "€{:,.2f}",
                    }),
                    hide_index=True, width="stretch",
                )

            st.markdown('<div class="section-title">Cash forecast</div>',
                        unsafe_allow_html=True)
            st.caption(
                "Your income arrives in occasional lump sums rather than monthly, "
                "so an \"average month\" would describe a month that has never "
                "happened. This projects the predictable side — your spending — and "
                "leaves future deposits at zero, to answer how long the cash lasts."
            )
            _forecast_section(filtered_data, DATA_KEY, timeline)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — CALENDAR
# ══════════════════════════════════════════════════════════════════════════════
# The Calendar is the most-clicked surface in the app, and without this
# decorator every single day-click re-ran the ENTIRE script: all seven tabs,
# every table, every chart lookup — just to change which day's transactions
# are listed underneath. @st.fragment scopes a rerun to this function alone,
# so clicking a day now re-renders only the calendar and its detail panel and
# leaves the other six tabs untouched. Measured end-to-end in a real browser
# (click -> that day's table actually on screen): 1257ms before, 301ms after.
@st.fragment
def _calendar_section(filtered_data, months_avail):
    daily = daily_summary(filtered_data)

    sel_month = st.selectbox(
        "Month", months_avail,
        index=len(months_avail) - 1,
        key="cal_month",
    )
    year, month = (int(x) for x in sel_month.split("-"))

    month_days = {k: v for k, v in daily.items() if k.startswith(sel_month)}
    max_abs = max((abs(v["total"]) for v in month_days.values()), default=0.0) or 1.0

    st.markdown(
        f'<div class="section-title">{pycalendar.month_name[month]} {year} '
        '— net of every movement that day (income, spending, investments)</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Self-transfers between your own accounts aren't shown — they're not "
        "income or spending, and often post on a different day than the other "
        "leg of the same transfer, which would otherwise look like a random "
        "swing in your balance."
    )

    weekday_headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    hcols = st.columns(7)
    for hc, wd in zip(hcols, weekday_headers):
        hc.markdown(
            f"<div style='text-align:center;color:#8b949e;font-size:.72rem;"
            f"font-weight:600;text-transform:uppercase;margin-bottom:4px'>{wd}</div>",
            unsafe_allow_html=True,
        )

    weeks = pycalendar.Calendar(firstweekday=0).monthdayscalendar(year, month)
    for week in weeks:
        wcols = st.columns(7)
        for wc, day in zip(wcols, week):
            with wc:
                if day == 0:
                    st.markdown("&nbsp;", unsafe_allow_html=True)
                    continue
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
                info = month_days.get(date_str)
                is_selected = st.session_state.get("cal_selected_day") == date_str
                border = "1px solid #58a6ff" if is_selected else "1px solid #21262d"
                if info and info["transactions"]:
                    total = info["total"]
                    intensity = min(abs(total) / max_abs, 1.0)
                    if total >= 0:
                        bg, fg = f"rgba(63,185,80,{0.12 + 0.55*intensity:.2f})", "#3fb950"
                    else:
                        bg, fg = f"rgba(255,123,114,{0.12 + 0.55*intensity:.2f})", "#ff7b72"
                    st.markdown(
                        f'''<div style="background:{bg};border:{border};border-radius:6px;
                        padding:5px 3px;text-align:center;margin-bottom:3px">
                        <div style="font-size:.68rem;color:#8b949e">{day}</div>
                        <div style="font-size:.7rem;font-weight:600;color:{fg}">€{total:,.0f}</div>
                        </div>''',
                        unsafe_allow_html=True,
                    )
                    clicked = st.button("view", key=f"cal_day_{date_str}", width="stretch")
                    if clicked:
                        # Streamlit already triggers a full rerun on this click by
                        # itself — the code below (which reads cal_selected_day)
                        # runs in that same rerun. An extra explicit st.rerun() here
                        # would just force a SECOND full script execution (and with
                        # it, a second expensive chart re-render) for zero benefit.
                        # Tradeoff: the little blue "selected" border above is drawn
                        # before this button in the loop, so it lags one click behind
                        # — a fine price for cutting page-load time in half.
                        st.session_state["cal_selected_day"] = date_str
                else:
                    st.markdown(
                        f'''<div style="border:{border};border-radius:6px;padding:5px 3px;
                        text-align:center;margin-bottom:3px;color:#484f58;font-size:.68rem">
                        {day}</div>''',
                        unsafe_allow_html=True,
                    )

    st.markdown(
        '<div style="display:flex;gap:16px;font-size:.72rem;color:#8b949e;margin:6px 0 4px 0">'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:rgba(63,185,80,.6);margin-right:4px"></span>Net inflow</span>'
        '<span><span style="display:inline-block;width:8px;height:8px;border-radius:2px;'
        'background:rgba(255,123,114,.6);margin-right:4px"></span>Net outflow</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    sel_day = st.session_state.get("cal_selected_day")
    if sel_day and sel_day.startswith(sel_month) and sel_day in daily:
        info = daily[sel_day]
        st.markdown(f'<div class="section-title">Transactions on {sel_day}</div>', unsafe_allow_html=True)

        txns = sorted(info["transactions"], key=lambda t: -abs(t["amount"]))
        rows = [{
            "Source":      t["source"],
            "Description": t["description"],
            "Category":    t["category"],
            "Amount (€)":  t["amount"],
        } for t in txns]
        st.dataframe(
            pd.DataFrame(rows).style.format({"Amount (€)": "€{:,.2f}"}),
            hide_index=True, width="stretch",
        )
        cls = "positive" if info["total"] >= 0 else "negative"
        st.markdown(
            f'<span class="val {cls}" style="font-size:1rem">Net: €{info["total"]:,.2f}</span>'
            f'<span style="color:#8b949e;font-size:.85rem"> · {len(txns)} transaction(s)</span>',
            unsafe_allow_html=True,
        )

        by_cat: dict[str, float] = {}
        for t in txns:
            by_cat[t["category"]] = by_cat.get(t["category"], 0.0) + abs(t["amount"])
        if len(by_cat) > 1:
            st.caption("Distribution by category")
            st.bar_chart(pd.Series(by_cat).sort_values(ascending=False))
    elif sel_day:
        st.caption("Pick a day in the month shown above to see its transactions.")


with tab_calendar:
    if st.session_state["stage"] in ("preview", "categorizing"):
        st.info("Finish importing on **📥 Add New Month** to see the updated calendar here.")
    elif not filtered_data:
        st.info("No data yet. Go to **📥 Add New Month** to import your first file.")
    else:
        _calendar_section(filtered_data, months_avail)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — ANOMALIES
# ══════════════════════════════════════════════════════════════════════════════
# Same reasoning as the Calendar fragment above. Dragging the sensitivity
# slider, or dismissing a duplicate, used to re-run all seven tabs for a
# change that only affects this one section — so the slider felt like it was
# fighting you. Scoped to a fragment, a drag re-renders only the anomaly
# panel: 1280ms -> 222ms per step, measured in a real browser by waiting for
# the server-computed fence values to actually change on screen. (st.rerun()
# inside a fragment defaults to fragment scope, which is exactly right for the
# dismiss/re-flag buttons: nothing outside this section cares about them.)
@st.fragment
def _anomaly_section(filtered_data, D_MAIN, DATA_KEY, START_MONTH, END_MONTH):
    dismissed_keys = load_dismissed()
    dup_groups = find_duplicate_groups(filtered_data, dismissed=dismissed_keys)
    st.markdown('<div class="section-title">🔁 Possible duplicate transactions</div>', unsafe_allow_html=True)
    if not dup_groups:
        st.caption("None found — every transaction has a unique date + amount + description + account combination.")
    else:
        st.caption(
            f"{len(dup_groups)} group(s) with the exact same date, amount, description and account. "
            "Some may be genuine repeats (e.g. two identical bus fares the same day) — click "
            "**Not a duplicate** on the ones you recognise so they stop being flagged."
        )
        with st.expander(f"Show {len(dup_groups)} group(s)", expanded=False):
            for group in dup_groups:
                dt0, amount0, desc0, cat0, source0 = group[0]
                key = group_key(group[0])
                gc1, gc2 = st.columns([5, 1.3])
                with gc1:
                    st.markdown(
                        f"**{dt0.strftime('%Y-%m-%d')} · €{amount0:,.2f} · {source0}** — "
                        f"{desc0}  ·  appears **{len(group)}×**"
                    )
                with gc2:
                    if st.button("✓ Not a duplicate", key=f"dismiss_{key}", width="stretch"):
                        dismiss(key)
                        st.rerun()
                rows_g = [{"Category": c} for _, _, _, c, _ in group]
                st.dataframe(pd.DataFrame(rows_g), hide_index=True, width="stretch")

    if dismissed_keys:
        with st.expander(f"✓ {len(dismissed_keys)} group(s) marked as not duplicates", expanded=False):
            for key in sorted(dismissed_keys, key=lambda k: k[0], reverse=True):
                date_s, amount_s, desc_s, source_s = key
                dc1, dc2 = st.columns([5, 1.3])
                with dc1:
                    st.markdown(f"{date_s} · €{amount_s:,.2f} · {source_s} — {desc_s}")
                with dc2:
                    if st.button("↺ Re-flag", key=f"undismiss_{key}", width="stretch"):
                        undismiss(key)
                        st.rerun()

    st.markdown("")
    d_an = D_MAIN
    if not d_an["all_months_str"]:
        st.warning("No transactions in the selected period.")
    else:
        st.markdown('<div class="section-title">IQR anomaly detection</div>', unsafe_allow_html=True)
        with st.expander("ℹ️  How does this work?", expanded=False):
            st.markdown("""
**What is IQR?**
The interquartile range (IQR) is the distance between Q1 (25th percentile) and Q3 (75th percentile)
of your monthly spending. It measures typical spread without being skewed by outliers.

**How are anomalies detected?**
A month is flagged if its spending falls outside the fences:
- **Upper fence** = Q3 + k × IQR → unusually high spending
- **Lower fence** = Q1 − k × IQR → unusually low spending (e.g. incomplete data)

**Sensitivity slider:**
- **k = 1.5** (default) — flags moderately unusual months
- **k = 3.0** — only extreme outliers

**Why does it matter?**
Holiday trips, annual bills, and one-off repairs skew your monthly average upward.
Excluding them gives a cleaner picture of your real day-to-day budget.
            """)

        IQR_K = st.slider("Sensitivity (k)", 1.0, 3.0, 1.5, 0.1,
                          help="Lower = more months flagged. Higher = only extreme outliers.")

        anomalies = detect(d_an, iqr_multiplier=IQR_K)
        high, low = anomalies["high"], anomalies["low"]

        n_normal  = len(anomalies["normal_months"])
        total_exc = sum(e for _, _, e in high)
        redistrib = total_exc / n_normal if n_normal else 0
        avg_clean = float(np.mean([d_an["monthly_totals_all"].get(m, 0)
                                   for m in anomalies["normal_months"]])) if anomalies["normal_months"] else 0

        a1, a2, a3, a4 = st.columns(4)
        with a1:
            st.markdown(f'<div class="fin-card"><h4>Normal range</h4><div class="val neutral" style="font-size:1.1rem">€{anomalies["fence_low"]:,.0f} – €{anomalies["fence_high"]:,.0f}</div></div>', unsafe_allow_html=True)
        with a2:
            st.markdown(f'<div class="fin-card"><h4>High-spend months</h4><div class="val negative">{len(high)}</div></div>', unsafe_allow_html=True)
        with a3:
            st.markdown(f'<div class="fin-card"><h4>Low-spend months</h4><div class="val">{len(low)}</div></div>', unsafe_allow_html=True)
        with a4:
            st.markdown(f'<div class="fin-card"><h4>Adjusted avg/month</h4><div class="val positive">€{avg_clean + redistrib:,.0f}</div></div>', unsafe_allow_html=True)

        st.image(
            _cached_anomaly_png(filtered_data, DATA_KEY, START_MONTH, END_MONTH, IQR_K),
            width="stretch",
        )

        if high:
            st.markdown('<div class="section-title">High-spend months — detail</div>', unsafe_allow_html=True)
            for month, total, excess in high:
                with st.expander(f"📅 {month}  ·  €{total:,.2f}  (+€{excess:,.0f} above fence)"):
                    cats_m = d_an["monthly_by_cat"][month]
                    rows_m = [{"Category": c, "Amount (€)": v}
                              for c, v in sorted(cats_m.items(), key=lambda x: -x[1]) if v > 0]
                    st.dataframe(pd.DataFrame(rows_m).style.format({"Amount (€)": "€{:,.2f}"}),
                                 hide_index=True, width="stretch")

        if low:
            st.markdown('<div class="section-title">Low-spend months</div>', unsafe_allow_html=True)
            for month, total, deficit in low:
                st.markdown(f'<div class="fin-card"><h4>{month}</h4><div class="val">€{total:,.2f} <span style="color:#8b949e;font-size:.85rem">(€{deficit:,.0f} below fence)</span></div></div>', unsafe_allow_html=True)

        if not high and not low:
            st.success("✅ No anomalous months at this sensitivity level.")

        if high or low:
            st.markdown('<div class="section-title">Analysis excluding anomalous months</div>', unsafe_allow_html=True)
            d_clean = adjusted_process(filtered_data, anomalies,
                                       start_month=START_MONTH, end_month=END_MONTH)
            if d_clean["all_months_str"]:
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f'<div class="fin-card"><h4>Avg/month (raw)</h4><div class="val">€{d_an["total_monthly_avg"]:,.0f}</div></div>', unsafe_allow_html=True)
                with c2:
                    st.markdown(f'<div class="fin-card"><h4>Avg/month (clean)</h4><div class="val positive">€{d_clean["total_monthly_avg"]:,.0f}</div></div>', unsafe_allow_html=True)
                with c3:
                    st.markdown(f'<div class="fin-card"><h4>Avg/month (redistributed)</h4><div class="val positive">€{avg_clean + redistrib:,.0f}</div></div>', unsafe_allow_html=True)
                st.caption(f"Excluded: {', '.join(sorted(anomalies['anomalous_months']))}")
                with st.expander("Full clean category breakdown"):
                    clean_rows = [{"Category": cat, "Total (€)": total,
                                   "Avg/month (€)": d_clean["cat_monthly_avg"].get(cat, 0),
                                   "Std dev": d_clean["cat_monthly_std"].get(cat, 0)}
                                  for cat, total in sorted(d_clean["cat_totals"].items(), key=lambda x: -x[1])]
                    st.dataframe(pd.DataFrame(clean_rows).style.format(
                        {"Total (€)": "€{:,.2f}", "Avg/month (€)": "€{:,.2f}", "Std dev": "€{:,.2f}"}),
                        hide_index=True, width="stretch")


with tab_anomaly:
    if st.session_state["stage"] in ("preview", "categorizing"):
        # Same reasoning as tab_analysis — anomaly detection + its chart
        # would otherwise silently re-run behind the scenes on every click.
        st.info("Finish importing on **📥 Add New Month** to see anomalies here.")
    elif not filtered_data:
        st.info("No data yet.")
    else:
        _anomaly_section(filtered_data, D_MAIN, DATA_KEY, START_MONTH, END_MONTH)




# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — FIX CATEGORIES
# ══════════════════════════════════════════════════════════════════════════════
# Until now a category could only be set during import: once a transaction was
# saved, a mistake was permanent. That left every misfiling in the file forever
# — and there are real ones, from a bar payment sitting in Water to the whole
# 111-transaction "Other" pile that no keyword rule can ever reach, because the
# bank sent no description to match on.
#
# A fragment, because filtering is the main activity here and re-running the
# whole app for each keystroke would make it unusable.
@st.fragment
def _edit_section(data_key):
    st.caption(
        "Change the category of anything already saved. Edits go through the "
        "same protected write as everything else: your file is backed up first, "
        "the new version is verified before it replaces the old one, and if a "
        "row no longer matches what you edited the whole change is refused "
        "rather than applied to the wrong transaction."
    )
    rows = transaction_rows(GLOBAL_FILE)
    if not rows:
        st.info("No transactions saved yet.")
        return

    cats = load_categories()
    f1, f2, f3 = st.columns([3, 2, 2])
    with f1:
        q = st.text_input("Search description", key="edit_q",
                          placeholder="e.g. bizum, endesa, lidl")
    with f2:
        only = st.multiselect("Only these categories", sorted({r["Category"] for r in rows}),
                              key="edit_cats")
    with f3:
        order = st.selectbox("Sort by", ["Newest first", "Largest amount", "Oldest first"],
                             key="edit_sort")

    sel = rows
    if q:
        ql = q.lower()
        sel = [r for r in sel if ql in r["Description"].lower()]
    if only:
        sel = [r for r in sel if r["Category"] in only]
    sel = sorted(sel,
                 key={"Newest first": lambda r: r["Date"],
                      "Oldest first": lambda r: r["Date"],
                      "Largest amount": lambda r: -abs(r["Amount"])}[order],
                 reverse=(order == "Newest first"))

    total = sum(r["Amount"] for r in sel if r["Amount"] < 0)
    st.caption(f"{len(sel)} of {len(rows)} transactions · €{-total:,.2f} of spending"
               + ("  — showing the first 300" if len(sel) > 300 else ""))
    sel = sel[:300]
    if not sel:
        st.info("Nothing matches those filters.")
        return

    df = pd.DataFrame([{"Date": r["Date"], "Amount": r["Amount"],
                        "Description": r["Description"], "Category": r["Category"],
                        "Source": r["Source"], "_row": r["row"]} for r in sel])
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key="edit_table",
        column_config={
            "Date": st.column_config.TextColumn("Date", disabled=True, width="small"),
            "Amount": st.column_config.NumberColumn("Amount", disabled=True,
                                                    format="€%.2f", width="small"),
            "Description": st.column_config.TextColumn("Description", disabled=True,
                                                       width="large"),
            "Category": st.column_config.SelectboxColumn("Category", options=cats,
                                                         required=True, width="medium"),
            "Source": st.column_config.TextColumn("Source", disabled=True, width="small"),
            "_row": None,
        },
    )

    changes = []
    for i in range(len(df)):
        before, after = df.iloc[i], edited.iloc[i]
        if str(before["Category"]) != str(after["Category"]):
            changes.append({
                "row": int(before["_row"]),
                "expect": {"Date": before["Date"], "Amount": float(before["Amount"]),
                           "Description": before["Description"]},
                "category": str(after["Category"]),
                "description": before["Description"],
            })

    if not changes:
        st.caption("Change a category above to enable saving.")
        return

    st.markdown(f"**{len(changes)} change{'' if len(changes) == 1 else 's'} pending**")
    for c in changes[:8]:
        st.markdown(
            f'<div style="padding:6px 12px;margin-bottom:4px;background:#161b22;'
            f'border:1px solid #21262d;border-left:3px solid #3fb950;border-radius:6px;'
            f'color:#c9d1d9;font-size:0.85rem">{c["description"][:52]} → '
            f'<b>{c["category"]}</b></div>', unsafe_allow_html=True)
    if len(changes) > 8:
        st.caption(f"…and {len(changes) - 8} more.")

    # Remembering the merchant is what stops the same fix being needed every
    # month. It is opt-in because it is a different, broader action than fixing
    # one row: a blank "Bizum: sent, no description" must NOT teach the
    # categoriser that every future description-less Bizum is whatever this one
    # happened to be.
    learn = st.checkbox(
        "Also remember these merchants for future imports", value=False,
        key="edit_learn",
        help="Adds each description to the known-merchants list so the next "
             "import categorises it automatically. Leave off for generic "
             "descriptions like a Bizum with no concept — they would teach the "
             "categoriser the wrong thing.")

    if st.button("💾  Save changes", type="primary", key="edit_save"):
        try:
            n = update_categories(changes, GLOBAL_FILE)
        except Exception as exc:
            st.error(f"Nothing was changed. {exc}")
            return
        if learn:
            known = load_known_merchants()
            for c in changes:
                key = c["description"].strip().lower()
                if key:
                    known[key] = c["category"]
            save_known_merchants(known)
        st.cache_data.clear()
        st.success(f"Saved {n} change{'' if n == 1 else 's'}."
                   + (" Merchants remembered for next time." if learn else ""))
        st.rerun()


with tab_edit:
    if st.session_state["stage"] in ("preview", "categorizing"):
        st.info("Finish importing on **📥 Add New Month** before editing saved transactions.")
    else:
        st.markdown('<div class="section-title">Fix a category</div>',
                    unsafe_allow_html=True)
        _edit_section(DATA_KEY)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — CATEGORIES
# ══════════════════════════════════════════════════════════════════════════════
with tab_settings:
    st.markdown('<div class="section-title">Category management</div>', unsafe_allow_html=True)
    st.markdown("Add or remove spending categories. Changes take effect immediately.")

    # Add new category
    col_input, col_btn = st.columns([3, 1])
    with col_input:
        new_cat = st.text_input("New category name", placeholder='e.g. "Pets"',
                                label_visibility="collapsed")
    with col_btn:
        if st.button("＋  Add", width="stretch"):
            if new_cat.strip():
                add_category(new_cat.strip())
                st.rerun()

    st.markdown("")

    # Category list with delete buttons
    for i, cat in enumerate(load_categories()):
        col_name, col_del = st.columns([5, 1])
        with col_name:
            st.markdown(f'<div style="padding:8px 12px;background:#161b22;border:1px solid #21262d;border-radius:6px;margin-bottom:4px;color:#c9d1d9">{cat}</div>',
                        unsafe_allow_html=True)
        with col_del:
            if st.button("🗑", key=f"del_{i}_{cat}", help=f"Delete {cat}"):
                delete_category(cat)
                st.rerun()

    st.markdown("")
    if st.button("↩  Reset to defaults"):
        reset_to_defaults()
        st.rerun()

    st.markdown('<div class="section-title">Data file</div>', unsafe_allow_html=True)
    summary = _cached_global_summary(all_data_raw, DATA_KEY)
    if summary.empty:
        st.caption("No global file found yet.")
    else:
        st.dataframe(summary, hide_index=True, width="stretch")

    # ── Backups ───────────────────────────────────────────────────────────────
    # Every save copies the previous file into backups/ before touching the
    # original. Listing them here is what makes that worth doing: a bad import
    # or a mis-edit is one click away from being undone, and restoring is itself
    # backed up, so picking the wrong one is not a dead end either.
    st.markdown('<div class="section-title">Backups</div>', unsafe_allow_html=True)
    backups = describe_backups(GLOBAL_FILE)
    if not backups:
        st.caption("No backups yet — one is made automatically before each save.")
    else:
        st.caption(f"The {BACKUP_KEEP} most recent saves are kept. "
                   "Restoring first backs up the current file, so it can be undone.")
        live_rows = len(all_data_raw)
        for i, bk in enumerate(backups):
            c_when, c_rows, c_btn = st.columns([3, 2, 1])
            with c_when:
                st.markdown(
                    f'<div style="padding:8px 12px;background:#161b22;border:1px solid #21262d;'
                    f'border-radius:6px;margin-bottom:4px;color:#c9d1d9">'
                    f'{bk["when"]:%d %b %Y &nbsp;·&nbsp; %H:%M:%S}</div>',
                    unsafe_allow_html=True)
            with c_rows:
                if bk["rows"] is None:
                    txt, colour = "unreadable", "#f85149"
                else:
                    delta = bk["rows"] - live_rows
                    tail  = "same as now" if delta == 0 else f"{delta:+d} vs now"
                    txt, colour = f"{bk['rows']:,} txns · {tail}", "#8b949e"
                st.markdown(
                    f'<div style="padding:8px 4px;margin-bottom:4px;color:{colour};'
                    f'font-size:0.85rem">{txt}</div>', unsafe_allow_html=True)
            with c_btn:
                if st.button("Restore", key=f"restore_{i}",
                             disabled=bk["rows"] is None,
                             width="stretch"):
                    try:
                        n = restore_backup(bk["path"], GLOBAL_FILE)
                        st.cache_data.clear()
                        st.success(f"Restored {n:,} transactions "
                                   f"from {bk['when']:%d %b %H:%M}.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not restore that backup: {exc}")

