"""
app.py — Streamlit personal finance dashboard.
Run with:  streamlit run app.py
"""

import sys, tempfile
import numpy as np
import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from pathlib import Path

st.set_page_config(page_title="Finance", page_icon="💰",
                   layout="wide", initial_sidebar_state="expanded")

sys.path.insert(0, str(Path(__file__).parent))

from auto_parser import detect_and_parse, reparse
from categories  import load_categories, save_categories, add_category, delete_category, reset_to_defaults
from categorizer import find_category, load_known_merchants, save_known_merchants, sign_mismatch
from storage     import save_to_global, load_from_global, global_summary
from self_transfer import find_transfer_match
from processor   import process
from anomaly     import detect, adjusted_process, plot_anomaly_overview
from charts      import plot_spending_analysis, plot_investment_allocation
from style       import apply_dark_theme

apply_dark_theme()

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
}.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 💰 Finance")
    st.markdown("---")

    all_data_raw = None
    try:
        all_data_raw = load_from_global()
    except Exception:
        pass

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
    st.caption("Data saved in `global_spending.xlsx`")


# ── Tabs ───────────────────────────────────────────────────────────────────────
tab_new, tab_analysis, tab_anomaly, tab_settings = st.tabs([
    "📥  Add New Month", "📊  Expense Analysis", "🔍  Anomalies", "⚙️  Categories",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — ADD NEW MONTH
# ══════════════════════════════════════════════════════════════════════════════
with tab_new:

    # ── UPLOAD ────────────────────────────────────────────────────────────────
    if st.session_state["stage"] == "upload":
        st.markdown('<div class="section-title">Upload bank export</div>', unsafe_allow_html=True)
        st.markdown("Upload any bank file — the format is detected automatically. No need to specify the bank.")

        uploaded = st.file_uploader(
            "Bank file (.xlsx, .xls, .csv)",
            type=["xlsx", "xls", "csv"],
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
            st.dataframe(pd.DataFrame(preview_rows[:6]), hide_index=True, use_container_width=True)
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
            if st.button("✓  Looks good — start categorising", use_container_width=True):
                if not st.session_state["source"].strip():
                    st.warning("Please enter an account label.")
                elif not preview_rows:
                    st.error("No rows could be parsed. Adjust the column selections.")
                else:
                    known = load_known_merchants()
                    st.session_state["known_snapshot"] = dict(known)
                    transfer_candidates = all_data_raw or []
                    src = st.session_state["source"]
                    categorized, pending = [], []
                    for row in preview_rows:
                        # Reciprocal cross-account match wins over keywords —
                        # it's evidence the money actually moved between your
                        # own accounts, regardless of how the bank worded it.
                        if find_transfer_match(row, src, transfer_candidates):
                            categorized.append({**row, "category": "Self-transfer"})
                            continue
                        cat = find_category(row["description"], known)
                        if cat:
                            categorized.append({**row, "category": cat})
                        else:
                            pending.append(row)
                    st.session_state["categorized"] = categorized
                    st.session_state["pending"]     = pending
                    st.session_state["cat_index"]   = 0
                    st.session_state["stage"]       = "categorizing" if pending else "review"
                    st.rerun()
        with back_col:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("← Back", use_container_width=True):
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
        auto  = len([r for r in st.session_state["categorized"]
                     if find_category(r["description"], st.session_state["known_snapshot"]) is not None])

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
            if st.button("✓  Confirm & next", use_container_width=True):
                st.session_state["categorized"].append({**row, "category": chosen, "keyword": kw or None})
                if kw:
                    known = load_known_merchants()
                    known[kw] = chosen
                    save_known_merchants(known)
                    # Apply the newly learned keyword to the rest of THIS
                    # batch too, instead of asking about every other
                    # occurrence of the same merchant in the same import.
                    remaining, still_pending = pending[idx + 1:], []
                    for r in remaining:
                        auto_cat = find_category(r["description"], known)
                        if auto_cat:
                            st.session_state["categorized"].append({**r, "category": auto_cat})
                        else:
                            still_pending.append(r)
                    st.session_state["pending"] = pending[:idx + 1] + still_pending
                st.session_state["cat_index"] += 1
                st.rerun()
        with c2:
            if st.button("Skip → Other", use_container_width=True):
                st.session_state["categorized"].append({**row, "category": "Other"})
                st.session_state["cat_index"] += 1
                st.rerun()
        with c3:
            if idx > 0 and st.button("← Back", use_container_width=True):
                last = st.session_state["categorized"].pop()
                if last.get("keyword"):
                    known = load_known_merchants()
                    known.pop(last["keyword"], None)
                    save_known_merchants(known)
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
        n_auto = sum(1 for r in cats if find_category(r["description"],
                     st.session_state["known_snapshot"]) is not None)
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
            hide_index=True, use_container_width=True, num_rows="fixed",
        )

        mismatches = [
            (row["Date"], row["Category"], row["Amount"])
            for _, row in edited.iterrows()
            if sign_mismatch(row["Category"], row["Amount"])
        ]
        if mismatches:
            st.warning(
                f"⚠️ {len(mismatches)} row(s) have an amount sign that doesn't match "
                f"their category (e.g. a negative amount tagged 'Income'). Double-check "
                f"before saving:"
            )
            st.dataframe(
                pd.DataFrame(mismatches, columns=["Date", "Category", "Amount"]),
                hide_index=True, use_container_width=True,
            )

        if st.session_state["stage"] == "review":
            if st.button("💾  Save to global Excel"):
                n = save_to_global(edited, source=st.session_state["source"])
                if n >= 0:
                    st.session_state["stage"] = "done"
                    st.rerun()

        if st.session_state["stage"] == "done":
            st.success("✅ Saved. Switch to **📊 Análisis** to see the updated dashboard.")
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
    if not filtered_data:
        st.info("No data yet. Go to **📥 Nuevo mes** to import your first file.")
    else:
        d = process(filtered_data, start_month=START_MONTH, end_month=END_MONTH)
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

            st.markdown('<div class="section-title">By category</div>', unsafe_allow_html=True)
            cat_rows = [{"Category": cat, "Total (€)": total,
                         "Avg/month": d["cat_monthly_avg"].get(cat, 0),
                         "Std dev": d["cat_monthly_std"].get(cat, 0),
                         "95% CI (±)": d["cat_monthly_ci"].get(cat, 0)}
                        for cat, total in sorted(d["cat_totals"].items(), key=lambda x: -x[1])]
            st.dataframe(
                pd.DataFrame(cat_rows).style.format(
                    {"Total (€)": "€{:,.2f}", "Avg/month": "€{:,.2f}",
                     "Std dev": "€{:,.2f}", "95% CI (±)": "€{:,.2f}"}),
                hide_index=True, use_container_width=True,
            )

            st.markdown('<div class="section-title">Charts</div>', unsafe_allow_html=True)
            fig1 = plot_spending_analysis(d, subtitle=period)
            st.pyplot(fig1, use_container_width=True)
            plt.close(fig1)

            if d.get("total_invested", 0) > 0:
                st.markdown('<div class="section-title">Investments</div>', unsafe_allow_html=True)
                fig2 = plot_investment_allocation(d)
                st.pyplot(fig2, use_container_width=True)
                plt.close(fig2)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — ANOMALIES
# ══════════════════════════════════════════════════════════════════════════════
with tab_anomaly:
    if not filtered_data:
        st.info("No data yet.")
    else:
        d_an = process(filtered_data, start_month=START_MONTH, end_month=END_MONTH)
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

            fig3 = plot_anomaly_overview(d_an, anomalies)
            st.pyplot(fig3, use_container_width=True)
            plt.close(fig3)

            if high:
                st.markdown('<div class="section-title">High-spend months — detail</div>', unsafe_allow_html=True)
                for month, total, excess in high:
                    with st.expander(f"📅 {month}  ·  €{total:,.2f}  (+€{excess:,.0f} above fence)"):
                        cats_m = d_an["monthly_by_cat"][month]
                        rows_m = [{"Category": c, "Amount (€)": v}
                                  for c, v in sorted(cats_m.items(), key=lambda x: -x[1]) if v > 0]
                        st.dataframe(pd.DataFrame(rows_m).style.format({"Amount (€)": "€{:,.2f}"}),
                                     hide_index=True, use_container_width=True)

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
                            hide_index=True, use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — CATEGORIES
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
        if st.button("＋  Add", use_container_width=True):
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
    try:
        summary = global_summary()
        if not summary.empty:
            st.dataframe(summary, hide_index=True, use_container_width=True)
    except Exception:
        st.caption("No global file found yet.")
