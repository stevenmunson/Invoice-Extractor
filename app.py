"""
Invoice Extractor - demo web app.

Run locally:   streamlit run app.py
"""
from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from src import extract_baseline, extract_claude
from src.costs import DEFAULT_MODEL, PRICES
from src.evaluate import score
from src.pipeline import invoice_dir, load_runs, load_truth, run_batch
from src.prompt_store import history, load_prompt, reset_prompt, save_prompt, version_of
from src.review_store import build_queue, clear_review, load_reviews, save_review
from src.schema import HEADER_FIELDS, SCORED_FIELDS

st.set_page_config(page_title="Invoice Extractor", page_icon="🧾", layout="wide")

# Link to the project's public GitHub repository, shown under the title. Leave empty to hide it.
REPO_URL = "https://github.com/stevenmunson/Invoice-Extractor"

# Rule-based is always gray; AI runs take categorical colors in a fixed order.
BASELINE_COLOR = "#8a8a85"
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
SET_LABELS = {"practice": "Practice set", "test": "Test set (held out)"}
FIELD_LABELS = {"vendor_name": "Vendor", "invoice_number": "Invoice #", "invoice_date": "Invoice date",
                "due_date": "Due date", "bill_to": "Bill to", "currency": "Currency", "subtotal": "Subtotal",
                "tax": "Tax", "total": "Total", "line_items": "Line items"}


RULE_BASED_EXPLAINER = """
Before AI, the usual way to automate invoices was to write **rules**: copy the text out of the PDF,
then search it for fixed patterns. For example:

- *Find the words "Total Due" and take the dollar amount right after them.*
- *Find "Invoice #" and take the code that follows.*

That works well for vendors whose layout the rules were written for. It breaks when a vendor writes
"Balance Payable" instead of "Total Due", puts the label above the value instead of beside it, uses
European number formats (1.234,56), or sends a **scan** (a scan is a picture, so there's no text to search).
Every new layout means writing and maintaining more rules.

This app includes one so you can see the difference: same invoices, same scoring, side by side.
"""

GLOSSARY = {
    "Answer key": "The correct value for every field on every sample invoice. Results are graded against it.",
    "Field": "One piece of information pulled from an invoice: vendor, invoice #, invoice date, due date, "
             "bill to, currency, subtotal, tax, total, line items (10 in all).",
    "Field accuracy": "Share of fields extracted correctly, averaged over all invoices. Money must match to the "
                      "cent; dates must match exactly; names ignore capitalization and punctuation.",
    "Line items": "The individual rows on an invoice (product, quantity, price, amount). Scored as the share "
                  "of rows found with the right quantity and amount.",
    "Perfect invoices": "Invoices where every one of the 10 fields was correct.",
    "Flagged for review": "Invoices the AI itself marked as uncertain (unreadable, or numbers that don't add up), "
                          "so a person checks them. The rule-based extractor never flags anything.",
    "Silent errors": "Invoices with at least one wrong field that were NOT flagged. These would slip into the "
                     "books unnoticed, which is the number a client should care about most.",
    "Token": "The unit AI models count text in, roughly ¾ of a word. A page image is converted to tokens too. "
             "You pay per token sent in (input) and per token returned (output).",
    "Cost per invoice": "Input tokens × input price + output tokens × output price, for one invoice.",
    "Cost per 1,000": "Average cost per invoice × 1,000. An easier number to compare and quote.",
    "Instructions version": "Which version of the AI's written instructions a run used. 'default' is the "
                            "starting version; each save in the Tune tab creates v1, v2, ... so you can see which "
                            "change moved the score.",
    "Practice set": "30 invoices you're allowed to study and tune on, as often as you like.",
    "Test set (held out)": "30 different invoices, including 2 layouts the practice set never shows. Kept aside "
                           "and scored once at the end, so the score reflects invoices the AI wasn't tuned for.",
    "Model": "Which version of Claude does the reading. Bigger models cost more per token and are usually "
             "more accurate. Comparing them is part of finding the right fit for a client.",
}

# ------------------------------------------------------------------ helpers
def get_api_key() -> str | None:
    try:
        if "ANTHROPIC_API_KEY" in st.secrets:
            return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:  # no secrets file - fine
        pass
    return os.environ.get("ANTHROPIC_API_KEY") or st.session_state.get("typed_key") or None


@st.cache_data
def preview_image(data: bytes, suffix: str):
    if suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        return pdfium.PdfDocument(data)[0].render(scale=1.3).to_pil()
    from io import BytesIO
    from PIL import Image
    return Image.open(BytesIO(data))


@st.cache_data
def preview_pages(data: bytes, suffix: str, max_pages: int = 3):
    if suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        return [doc[i].render(scale=1.3).to_pil() for i in range(min(len(doc), max_pages))]
    return [preview_image(data, suffix)]


def fmt_value(field, v):
    if v is None or v == "":
        return "—"
    if field in ("subtotal", "tax", "total"):
        try:
            return f"{float(v):,.2f}"
        except (TypeError, ValueError):
            return str(v)
    return str(v)


MODEL_SHORT = {"claude-sonnet-5-5": "Sonnet 5.5", "claude-haiku-4-5-20251001": "Haiku 4.5",
               "claude-opus-5-5": "Opus 5.5"}


def run_name(run):
    """Short name for a run: the model, plus which version of the instructions it used."""
    if run["method"] == "baseline":
        return "Rule-based"
    return f"{MODEL_SHORT.get(run['model'], run['model'])} · {run.get('prompt_version', 'original')}"


def run_label(run):
    return f"{run_name(run)}  ·  {run['run_at'].replace('T', ' ')[:16]}"


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Settings")
    method = st.radio("Extraction method", ["AI (Claude)", "Rule-based baseline"],
                      captions=["Claude reads the invoice the way a person would. Any layout, including scans.",
                                "The traditional, non-AI approach. Free, but brittle."])
    with st.expander("What's a rule-based extractor?"):
        st.markdown(RULE_BASED_EXPLAINER)
    model = st.selectbox("Claude model", list(PRICES), index=list(PRICES).index(DEFAULT_MODEL),
                         disabled=method != "AI (Claude)",
                         format_func=lambda m: f"{m}  (${PRICES[m][0]:g} in / ${PRICES[m][1]:g} out per M tokens)")
    if method == "AI (Claude)" and not get_api_key():
        st.text_input("Anthropic API key", type="password", key="typed_key",
                      help="Kept only in this browser session. For a deployed app, store it as a secret instead.")
    api_key = get_api_key()
    if method == "AI (Claude)":
        st.caption("✅ API key loaded" if api_key else "⚠️ Add an API key to run the AI extractor.")
    st.divider()
    dataset = st.radio("Invoice set", ["practice", "test"], format_func=SET_LABELS.get,
                       captions=["30 invoices, 6 layouts. Look at these and tune on them as much as you like.",
                                 "30 different invoices, incl. 2 layouts never seen in practice. "
                                 "Score once, at the end."])
    if dataset == "test":
        st.warning("Held-out set: don't study its mistakes or tune on it, or the score stops being fair.")

method_key = "claude" if method == "AI (Claude)" else "baseline"
needs_key = method_key == "claude" and not api_key

st.title("🧾 Invoice Extractor")
st.caption("Turn messy invoices — any layout, PDF or scan — into clean, structured data. "
           "Every result is scored against an answer key, and every call is costed."
           + (f" [Source code and write-up on GitHub]({REPO_URL})" if REPO_URL else ""))

tab_one, tab_batch, tab_review, tab_tune, tab_about = st.tabs(
    ["Try an invoice", "Accuracy & cost report", "Review", "Tune", "How it works"])

# ------------------------------------------------------------------ tab 1
with tab_one:
    truth_all = load_truth(dataset)
    src_choice = st.radio("Document", ["Pick a sample invoice", "Upload your own"], horizontal=True)
    truth = None
    if src_choice == "Pick a sample invoice":
        name = st.selectbox("Sample", sorted(truth_all),
                            format_func=lambda n: f"{n}  —  {truth_all[n]['_meta']['layout']} layout, {truth_all[n]['_meta']['format']}")
        data, suffix = (invoice_dir(dataset) / name).read_bytes(), Path(name).suffix
        truth = truth_all[name]
    else:
        up = st.file_uploader("PDF, PNG or JPG", type=["pdf", "png", "jpg", "jpeg"])
        data, suffix = (up.getvalue(), Path(up.name).suffix) if up else (None, None)

    if data:
        left, right = st.columns([1, 1.15], gap="large")
        with left:
            st.image(preview_image(data, suffix), caption="Page 1 preview")
        with right:
            if st.button("Extract data", type="primary", disabled=needs_key):
                with st.spinner("Reading the invoice..."):
                    try:
                        res = (extract_claude.extract(data, suffix, api_key=api_key, model=model)
                               if method_key == "claude" else extract_baseline.extract(data, suffix))
                        st.session_state["single"] = {"res": res, "id": (src_choice, dataset, len(data))}
                    except Exception as e:
                        st.error(f"Extraction failed: {e}")
            if needs_key:
                st.info("Add an API key in the sidebar to run the AI extractor, or switch to the baseline.")

            out = st.session_state.get("single")
            if out and out["id"] == (src_choice, dataset, len(data)):
                res, f = out["res"], out["res"]["fields"]
                if res.get("note"):
                    st.warning(res["note"])
                if f.get("needs_review"):
                    st.warning(f"Flagged for human review: {f.get('review_reason') or 'no reason given'}")

                m1, m2, m3 = st.columns(3)
                m1.metric("Cost", f"${res['cost_usd']:.4f}")
                m2.metric("Tokens (in / out)", f"{res['input_tokens']:,} / {res['output_tokens']:,}")
                m3.metric("Time", f"{res['seconds']:.1f}s")

                rows = []
                sc = score(f, truth) if truth else {}
                for fld in HEADER_FIELDS:
                    row = {"Field": FIELD_LABELS[fld], "Extracted": fmt_value(fld, f.get(fld))}
                    if truth:
                        row["Expected"] = fmt_value(fld, truth[fld])
                        row["Match"] = "✅" if sc[fld] == 1 else "❌"
                    rows.append(row)
                if truth:
                    st.metric("Field accuracy vs answer key", f"{sc['overall']:.0%}")
                st.dataframe(pd.DataFrame(rows), hide_index=True)

                st.subheader("Line items")
                items = f.get("line_items") or []
                if items:
                    st.dataframe(pd.DataFrame(items), hide_index=True)
                else:
                    st.caption("None extracted.")
                if truth:
                    st.caption(f"Line-item match vs answer key: {sc['line_items']:.0%} "
                               f"({len(truth['line_items'])} expected)")
                with st.expander("Raw JSON output"):
                    st.json(f)

# ------------------------------------------------------------------ tab 2
with tab_batch:
    st.subheader(f"Run the full evaluation: {SET_LABELS[dataset]}")
    label = (f"**{model}** with instructions **{version_of(load_prompt())}**" if method_key == "claude"
             else "**the rule-based baseline**")
    st.write(f"Runs {label} over all 30 invoices in the {SET_LABELS[dataset].lower()}, scores every field against "
             "the answer key, and records tokens, cost and time. Results are saved, so you can compare runs side by side.")
    if method_key == "claude":
        st.caption("This makes 30 API calls, typically well under a dollar.")
    if dataset == "test":
        n_prev = len([r for r in load_runs("test") if r["method"] == "claude"])
        st.info("**Final exam.** Run this once, after you're done tuning on the practice set. The score you get "
                "here is the honest one to report." + (f" (AI runs on the test set so far: {n_prev}.)" if n_prev else ""))
    if st.button("Run evaluation", type="primary", disabled=needs_key):
        bar = st.progress(0.0, text="Starting...")
        run_batch(method_key, api_key=api_key, model=model, dataset=dataset,
                  on_progress=lambda i, n, fname: bar.progress(i / n, text=f"{i}/{n}  {fname}"))
        bar.empty()
        st.success("Done.")

    runs = load_runs(dataset)
    if not runs:
        st.info(f"No saved runs on the {SET_LABELS[dataset].lower()} yet. Run the evaluation above.")
    else:
        st.divider()
        st.subheader("Comparison")
        summary = pd.DataFrame([{
            "Method": r["model"],
            "Instructions": r.get("prompt_version", "n/a"),
            "Field accuracy": r["summary"]["overall_accuracy"] * 100,
            "Perfect invoices": f"{r['summary']['perfect_documents']}/{r['summary']['documents']}",
            "Flagged for review": r["summary"]["flagged_for_review"],
            "Avg cost / invoice": r["summary"]["avg_cost_usd"],
            "Cost per 1,000": r["summary"]["avg_cost_usd"] * 1000,
            "Avg seconds": r["summary"]["avg_seconds"],
            "Run at": r["run_at"].replace("T", " ")[:16],
        } for r in runs])
        st.caption("Hover over a column name for its definition. Full glossary in the *How it works* tab.")
        st.dataframe(summary, hide_index=True, column_config={
            "Method": st.column_config.TextColumn(help="Rule-based extractor, or the Claude model used."),
            "Instructions": st.column_config.TextColumn(help=GLOSSARY["Instructions version"]),
            "Field accuracy": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100,
                                                              help=GLOSSARY["Field accuracy"]),
            "Perfect invoices": st.column_config.TextColumn(help=GLOSSARY["Perfect invoices"]),
            "Flagged for review": st.column_config.NumberColumn(help=GLOSSARY["Flagged for review"]),
            "Avg cost / invoice": st.column_config.NumberColumn(format="$%.4f", help=GLOSSARY["Cost per invoice"]),
            "Cost per 1,000": st.column_config.NumberColumn(format="$%.2f", help=GLOSSARY["Cost per 1,000"]),
            "Avg seconds": st.column_config.NumberColumn(format="%.1f", help="Average time to process one invoice."),
        })

        st.markdown("**Accuracy by field**")
        latest = {}  # latest run per method + instructions version
        for r in runs:
            latest[run_name(r)] = r
        names = list(latest)
        # Default view: the rule-based baseline (the "before AI" reference) plus the latest AI runs.
        ai_names = [n for n in names if n != "Rule-based"]
        default = (["Rule-based"] if "Rule-based" in names else []) + ai_names[-3:]
        chosen = st.multiselect("Runs to compare", names, default=default,
                                help="Each run is named model · instructions version.")
        long = pd.DataFrame([{"Method": n, "Field": FIELD_LABELS[f], "Accuracy": latest[n]["summary"]["per_field"][f]}
                             for n in chosen for f in SCORED_FIELDS])
        methods = chosen
        palette, colors = iter(SERIES_COLORS * 3), {}
        for n in names:  # color follows the run, not its position in the selection
            colors[n] = BASELINE_COLOR if n == "Rule-based" else next(palette)
        chart = (alt.Chart(long).mark_bar(cornerRadiusEnd=4, height={"band": 0.8})
                 .encode(y=alt.Y("Field:N", sort=[FIELD_LABELS[f] for f in SCORED_FIELDS], title=None),
                         yOffset=alt.YOffset("Method:N", sort=methods),
                         x=alt.X("Accuracy:Q", axis=alt.Axis(format="%", grid=True), scale=alt.Scale(domain=[0, 1]), title=None),
                         color=alt.Color("Method:N", sort=methods, legend=alt.Legend(orient="top", title=None, labelLimit=0,
                                                                           columns=min(3, max(1, len(methods)))),
                                         scale=alt.Scale(domain=methods, range=[colors[m] for m in methods])),
                         tooltip=["Method", "Field", alt.Tooltip("Accuracy:Q", format=".0%")])
                 .properties(width="container", height=34 * len(SCORED_FIELDS) * max(1, len(methods)) ** 0.5))
        if chosen:
            st.altair_chart(chart)

        st.markdown("**Where does each method struggle?**")
        pick = st.selectbox("Run", runs, format_func=run_label, index=len(runs) - 1)
        docs = pd.DataFrame([{"File": r["file"], "Layout": r["layout"], "Format": r["format"],
                              "Accuracy": r["scores"]["overall"] * 100, "Cost": r["cost_usd"],
                              "Flagged": "⚑" if r["fields"].get("needs_review") else "",
                              "Flag reason": r["fields"].get("review_reason") or "",
                              "Missed fields": ", ".join(FIELD_LABELS[f] for f in SCORED_FIELDS if r["scores"][f] < 1),
                              "Error": r.get("error") or r.get("note") or ""} for r in pick["rows"]])
        by_layout = docs.groupby("Layout", as_index=False)["Accuracy"].mean().sort_values("Accuracy")
        c1, c2 = st.columns([1, 2.4])
        with c1:
            st.dataframe(by_layout, hide_index=True, column_config={
                "Accuracy": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100)})
        with c2:
            st.dataframe(docs, hide_index=True, height=320, column_config={
                "Flag reason": st.column_config.TextColumn(
                    width="medium", help="Why the AI flagged this invoice. Double-click a cell to read all of it."),
                "Accuracy": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
                "Cost": st.column_config.NumberColumn(format="$%.4f")})

        st.divider()
        st.subheader("Business case")
        s = pick["summary"]
        st.markdown(
            f"Estimates what a client would save per month by switching from typing invoices by hand to this "
            f"pipeline. The **AI cost** and **review rate** come from the run selected above "
            f"(**{run_label(pick)}**). The rest are the client's numbers; change them and everything below updates.")

        b1, b2, b3 = st.columns(3)
        volume = b1.number_input(
            "Invoices per month", 100, 1_000_000, 2000, step=100,
            help="How many invoices the client processes each month. Every cost below scales with this.")
        manual_min = b2.number_input(
            "Manual minutes per invoice", 0.5, 60.0, 4.0, step=0.5,
            help="How long a person spends today reading one invoice and typing it into their system. "
                 "Ask the client, or time a few. 3–5 minutes is typical for a simple invoice.")
        rate = b3.number_input(
            "Loaded staff cost ($/hour)", 10.0, 300.0, 35.0, step=5.0,
            help="What an hour of that person's time really costs the business: wage PLUS benefits, payroll "
                 "taxes and overhead. A rough rule is 1.3–1.5× the hourly wage.")
        b4, b5, b6 = st.columns(3)
        # flagged invoices + invoices that failed outright both go to a person
        default_share = round(100 * (s["flagged_for_review"] + s.get("errors", 0)) / max(1, s["documents"]), 1)
        review_pct = b4.number_input(
            "Share needing human review (%)", 0.0, 100.0, default_share, step=1.0,
            key=f"review_{pick['model']}_{pick['run_at']}",
            help="Percentage of invoices a person still checks. Starts at the share the AI flagged (plus any that "
                 "failed outright) in the selected run. Raise it if the client wants extra spot-checks.")
        review_min = b5.number_input(
            "Minutes to review one invoice", 0.5, 30.0, 1.5, step=0.5,
            help="Checking a pre-filled invoice is much faster than typing it from scratch. "
                 "This is the time to glance over the extracted fields and fix any mistakes.")
        b6.metric("AI cost per invoice (from run)", f"${s['avg_cost_usd']:.4f}",
                  help="Average Claude cost per invoice measured in the selected run. "
                       "The rule-based extractor costs $0 because it doesn't call an AI.")

        review_share = review_pct / 100
        manual_cost = volume * manual_min / 60 * rate
        ai_cost = volume * s["avg_cost_usd"]
        review_cost = volume * review_share * review_min / 60 * rate
        new_cost = ai_cost + review_cost
        savings = manual_cost - new_cost

        k1, k2, k3 = st.columns(3)
        k1.metric("Today: manual cost", f"${manual_cost:,.0f}/mo")
        k2.metric("With the pipeline", f"${new_cost:,.0f}/mo", help="AI calls + human review.")
        k3.metric("Monthly savings", f"${savings:,.0f}",
                  f"{savings / manual_cost:.0%} lower" if manual_cost else None)

        silent = sum(1 for r in pick["rows"]
                     if r["scores"]["overall"] < 1 and not r["fields"].get("needs_review") and not r.get("error"))
        if silent:
            st.warning(
                f"**Reality check: {silent} of {s['documents']} invoices in this run had a wrong field that was "
                f"NOT flagged** (silent errors). The savings above assume only flagged invoices need a person. "
                f"Silent errors would reach the client's books unnoticed, so either raise the review share or "
                f"improve accuracy before promising these savings."
                + (" The rule-based extractor never flags anything, so all of its mistakes are silent."
                   if pick["method"] == "baseline" else ""))
        st.markdown("**How it's calculated**")
        st.dataframe(pd.DataFrame([
            {"Line": "Manual cost today", "Formula": "invoices × manual minutes ÷ 60 × staff $/hour",
             "With your numbers": f"{volume:,} × {manual_min:g} ÷ 60 × ${rate:,.2f}", "Per month": f"${manual_cost:,.2f}" if manual_cost < 100 else f"${manual_cost:,.0f}"},
            {"Line": "AI calls", "Formula": "invoices × AI cost per invoice",
             "With your numbers": f"{volume:,} × ${s['avg_cost_usd']:.4f}", "Per month": f"${ai_cost:,.2f}" if ai_cost < 100 else f"${ai_cost:,.0f}"},
            {"Line": "Human review", "Formula": "invoices × review share × review minutes ÷ 60 × staff $/hour",
             "With your numbers": f"{volume:,} × {review_pct:g}% × {review_min:g} ÷ 60 × ${rate:,.2f}",
             "Per month": f"${review_cost:,.2f}" if review_cost < 100 else f"${review_cost:,.0f}"},
            {"Line": "Savings", "Formula": "manual cost − AI calls − human review",
             "With your numbers": f"${manual_cost:,.0f} − ${ai_cost:,.2f} − ${review_cost:,.0f}", "Per month": f"${savings:,.2f}" if savings < 100 else f"${savings:,.0f}"},
        ]), hide_index=True)

        st.markdown(f"""
**What moves the number most**
- **Manual minutes** and **staff $/hour** set the size of the prize: they're what the client spends today.
- **Review share** is the biggest cost of the new process. Fewer flags means bigger savings, which is why accuracy matters.
- **AI cost** is usually tiny by comparison: at ${s['avg_cost_usd']:.4f} per invoice, {volume:,} invoices cost
  ${ai_cost:,.2f}/month.
""")

        st.caption("Not included: one-time setup, hosting (usually a few dollars a month), and handling "
                   "invoices that fail entirely. Mention these to the client.")

# ------------------------------------------------------------------ tab: review
STATUS_ICON = {"approved": "✅ Approved", "corrected": "✏️ Corrected", None: "⏳ To review"}


def _parse_money(v):
    s = str(v).replace(",", "").replace("$", "").replace("€", "").strip()
    return float(s) if s else 0.0


with tab_review:
    st.subheader("Review queue")
    st.markdown(
        "How a person works alongside the AI in production. Two queues: **every invoice the AI flagged** "
        "(or that failed), and a **random spot-check** of invoices it did *not* flag. The spot-check is the "
        "only way to measure silent errors once there's no answer key. Check each invoice against the image, "
        "fix anything wrong, and save. Your decisions are kept, and the corrected data can be downloaded.")
    if dataset == "test":
        st.warning("You're on the held-out test set. Reviewing it shows you its answers, so prefer the practice set here.")
    ai_runs = [r for r in load_runs(dataset) if r["method"] == "claude"]
    if not ai_runs:
        st.info(f"No AI runs on the {SET_LABELS[dataset].lower()} yet. Run one in the Accuracy & cost report tab.")
    else:
        c1, c2 = st.columns([2, 1])
        run = c1.selectbox("Run to review", list(reversed(ai_runs)), format_func=run_label, key="review_run")
        spot_pct = c2.number_input("Spot-check sample of unflagged invoices (%)", 0, 100, 10, step=5,
                                   help="Share of the invoices the AI did NOT flag that a person checks anyway. "
                                        "5–10% is typical. The sample is fixed per run, so it can't be re-rolled.")
        run_id = run["_id"]
        queue = build_queue(run, spot_pct)
        reviews = load_reviews(run_id)
        rows_by_file = {r["file"]: r for r in run["rows"]}

        def done(files):
            return sum(1 for f in files if f in reviews)

        spot_done = [f for f in queue["spot_check"] if f in reviews]
        spot_wrong = [f for f in spot_done if reviews[f]["status"] == "corrected"]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Flagged reviewed", f"{done(queue['flagged'])} / {len(queue['flagged'])}")
        m2.metric("Spot-checks reviewed", f"{len(spot_done)} / {len(queue['spot_check'])}")
        m3.metric("Invoices corrected", sum(1 for v in reviews.values() if v["status"] == "corrected"))
        m4.metric("Spot-check error rate", f"{len(spot_wrong) / len(spot_done):.0%}" if spot_done else "—",
                  help="Share of spot-checked (unflagged) invoices that needed a correction: an estimate of the "
                       "silent error rate. With few checks it's rough. By the rule of three, 0 errors in n checks "
                       "only shows the rate is probably below 3/n.")

        which = st.radio("Queue", ["flagged", "spot_check"], horizontal=True, key=f"queue_{run_id}",
                         format_func=lambda q: (f"Flagged by AI ({len(queue['flagged'])})" if q == "flagged"
                                                else f"Spot-check sample ({len(queue['spot_check'])})"))
        files = queue[which]
        if not files:
            st.info("Nothing in this queue." + (" The AI didn't flag any invoices in this run." if which == "flagged" else ""))
        else:
            st.dataframe(pd.DataFrame([{
                "File": f, "Layout": rows_by_file[f]["layout"],
                "Flag reason": rows_by_file[f]["fields"].get("review_reason") or rows_by_file[f].get("error") or "—",
                "Status": STATUS_ICON[reviews.get(f, {}).get("status")]} for f in files]),
                hide_index=True, column_config={"Flag reason": st.column_config.TextColumn(width="large")})

            sel_key = f"review_sel_{run_id}_{which}"
            if st.session_state.get(sel_key + "_next"):  # advance to the next invoice after a save
                st.session_state[sel_key] = st.session_state.pop(sel_key + "_next")
            if st.session_state.get(sel_key) not in files:
                st.session_state[sel_key] = next((f for f in files if f not in reviews), files[0])
            file = st.selectbox("Invoice", files, key=sel_key,
                                format_func=lambda f: f"{f}  ·  {STATUS_ICON[reviews.get(f, {}).get('status')]}")
            row = rows_by_file[file]
            existing = reviews.get(file)
            current = existing["final"] if existing else row["fields"]
            data_bytes = (invoice_dir(dataset) / file).read_bytes()

            left, right = st.columns([1, 1.1], gap="large")
            with left:
                for i, page in enumerate(preview_pages(data_bytes, Path(file).suffix), start=1):
                    st.image(page, caption=f"{file} · page {i}")
            with right:
                if row.get("error"):
                    st.error(f"Extraction failed: {row['error']}")
                elif row["fields"].get("needs_review"):
                    st.warning(f"**AI's flag reason:** {row['fields'].get('review_reason') or 'none given'}")
                else:
                    st.info("Not flagged by the AI. Randomly picked for a spot-check.")
                if existing:
                    changed = ", ".join(FIELD_LABELS.get(c, c) for c in existing["changed_fields"]) or "nothing"
                    st.success(f"{STATUS_ICON[existing['status']]} on {existing['reviewed_at'].replace('T', ' ')}. "
                               f"Changed: {changed}.")

                with st.form(key=f"form_{run_id}_{file}"):
                    st.caption("Check each value against the invoice image. Save without edits = approved as correct; "
                               "any edit = corrected.")
                    inputs = {}
                    fc1, fc2 = st.columns(2)
                    for i, fld in enumerate(HEADER_FIELDS):
                        v = current.get(fld, "")
                        shown = f"{float(v):.2f}" if fld in ("subtotal", "tax", "total") and v not in ("", None) else str(v or "")
                        inputs[fld] = (fc1 if i % 2 == 0 else fc2).text_input(FIELD_LABELS[fld], shown)
                    st.markdown("**Line items**")
                    items_df = pd.DataFrame(current.get("line_items") or [],
                                            columns=["description", "quantity", "unit_price", "amount"])
                    edited_items = st.data_editor(items_df, num_rows="dynamic", hide_index=True,
                                                  key=f"items_{run_id}_{file}")
                    note = st.text_input("Reviewer note (optional)", existing.get("note", "") if existing else "")
                    submitted = st.form_submit_button("Save review", type="primary")

                if submitted:
                    try:
                        final = {fld: (_parse_money(inputs[fld]) if fld in ("subtotal", "tax", "total")
                                       else inputs[fld].strip()) for fld in HEADER_FIELDS}
                        items = []
                        for it in edited_items.to_dict("records"):
                            if all(pd.isna(x) or x == "" for x in it.values()):
                                continue
                            clean = {k: ("" if pd.isna(v) else v) for k, v in it.items()}
                            items.append({"description": str(clean["description"]),
                                          "quantity": _parse_money(clean["quantity"]),
                                          "unit_price": _parse_money(clean["unit_price"]),
                                          "amount": _parse_money(clean["amount"])})
                        final["line_items"] = items
                    except ValueError:
                        st.error("Amounts must be numbers (e.g. 1234.56). Nothing was saved.")
                    else:
                        original = {k: row["fields"].get(k, "") for k in SCORED_FIELDS}
                        save_review(run_id, file, which, original, final, note)
                        nxt = next((f for f in files if f not in load_reviews(run_id)), None)
                        if nxt:
                            st.session_state[sel_key + "_next"] = nxt
                        st.rerun()

                if existing and st.button("Undo this review", key=f"undo_{run_id}_{file}"):
                    clear_review(run_id, file)
                    st.rerun()

                with st.expander("Compare with the answer key (demo only)"):
                    st.caption("In production there's no answer key. This is here so you can check your own review.")
                    truth_r = load_truth(dataset)[file]
                    sc = score(current, truth_r)
                    st.dataframe(pd.DataFrame([{
                        "Field": FIELD_LABELS[f], "Current value": fmt_value(f, current.get(f)) if f != "line_items"
                        else f"{len(current.get('line_items') or [])} rows",
                        "Answer key": fmt_value(f, truth_r[f]) if f != "line_items" else f"{len(truth_r[f])} rows",
                        "Match": "✅" if sc[f] == 1 else "❌"} for f in SCORED_FIELDS]), hide_index=True)

        # Everything the client's accounting system would receive: reviewed values where a person checked,
        # the AI's values everywhere else.
        export = []
        for r in run["rows"]:
            rv = reviews.get(r["file"])
            vals = rv["final"] if rv else r["fields"]
            export.append({"file": r["file"],
                           "status": rv["status"] if rv else ("needs review" if r["file"] in queue["flagged"]
                                                              else "auto-accepted"),
                           **{f: vals.get(f, "") for f in HEADER_FIELDS},
                           "line_item_count": len(vals.get("line_items") or [])})
        st.download_button("Download results with corrections (CSV)", pd.DataFrame(export).to_csv(index=False),
                           file_name=f"{run_id}__reviewed.csv", mime="text/csv",
                           help="One row per invoice: corrected values where reviewed, the AI's values elsewhere.")

# ------------------------------------------------------------------ tab: tune
with tab_tune:
    st.subheader("Tune the AI's instructions")
    st.markdown("""
Claude gets the same written instructions with every invoice. **Tuning** means improving those instructions
based on the mistakes it makes, then measuring again. The loop:

1. Run the evaluation on the **practice set** (Accuracy & cost report tab).
2. Look at what it got wrong (listed below).
3. Add or reword a rule in the instructions that addresses the *kind* of mistake, and **save** (creates a new version).
4. Run the practice set again and compare versions in the report. Keep the change only if the score went up
   and nothing else got worse.
5. When you're done, run the **test set** once for the final, fair score.
""")
    current = load_prompt()
    cur_ver = version_of(current)
    st.markdown(f"**Current instructions: version `{cur_ver}`**")
    edited = st.text_area("Instructions sent to Claude with every invoice", current, height=300,
                          key=f"prompt_editor_{cur_ver}",
                          help="Plain English. The list of fields to extract is set separately, so these are "
                               "the rules for HOW to read them.")
    c1, c2, c3 = st.columns([1, 1, 3])
    if c1.button("Save as new version", type="primary", disabled=edited.strip() == current.strip()):
        v = save_prompt(edited)
        st.session_state["tune_msg"] = f"Saved as version {v}. Now rerun the practice set to see the effect."
        st.rerun()
    if c2.button("Reset to default", disabled=cur_ver == "default"):
        reset_prompt()
        st.session_state["tune_msg"] = "Back to the default instructions."
        st.rerun()
    if edited.strip() != current.strip():
        c3.caption("You have unsaved changes.")
    if st.session_state.get("tune_msg"):
        st.success(st.session_state.pop("tune_msg"))

    with st.expander("Tips for writing good rules"):
        st.markdown("""
- **Fix the kind of mistake, not the invoice.** "European dates are day-first" helps every future European invoice;
  "invoice_17's date is March 5" helps nobody and is cheating.
- **One change at a time**, then rerun, so you know what caused the improvement (or the damage).
- **Watch for side effects.** A rule that fixes due dates can break something else. Check every field in the comparison.
- **Prefer flagging over guessing.** If something is truly ambiguous, tell it to set `needs_review` and explain why.
""")

    st.markdown("**What the AI got wrong in its latest practice run**")
    st.caption("Practice set only. The test set's mistakes are deliberately never shown here.")
    p_runs = [r for r in load_runs("practice") if r["method"] == "claude"]
    if not p_runs:
        st.info("No AI runs on the practice set yet. Run one in the Accuracy & cost report tab.")
    else:
        last = p_runs[-1]
        p_truth = load_truth("practice")
        misses = []
        for r in last["rows"]:
            t = p_truth[r["file"]]
            for fld in SCORED_FIELDS:
                if r["scores"][fld] < 1:
                    if fld == "line_items":
                        exp = f"{len(t['line_items'])} rows"
                        got = f"{len(r['fields'].get('line_items') or [])} rows, {r['scores'][fld]:.0%} matched"
                    else:
                        exp, got = fmt_value(fld, t[fld]), fmt_value(fld, r["fields"].get(fld))
                    misses.append({"File": r["file"], "Layout": r["layout"], "Field": FIELD_LABELS[fld],
                                   "Expected": exp, "AI said": got,
                                   "Flagged?": "⚑ yes" if r["fields"].get("needs_review") else "no (silent)"})
            if r.get("error"):
                misses.append({"File": r["file"], "Layout": r["layout"], "Field": "(whole invoice)",
                               "Expected": "", "AI said": f"Error: {r['error'][:80]}", "Flagged?": ""})
        st.caption(f"From {run_label(last)}")
        if misses:
            st.dataframe(pd.DataFrame(misses), hide_index=True)
        else:
            st.success("No mistakes on the practice set. Time to run the test set.")

    with st.expander("Version history"):
        for h in reversed(history()):
            st.markdown(f"**{h['version']}**" + (f"  ·  saved {h['saved_at'].replace('T', ' ')}" if h["saved_at"] else ""))
            st.code(h["text"], language=None)

# ------------------------------------------------------------------ tab 3
with tab_about:
    st.markdown("""
### The problem
Businesses receive invoices in every possible layout — PDFs from big vendors, scanned paper from small ones,
European formats, multi-page statements. Someone usually re-types them into a spreadsheet or ERP.

### The pipeline
1. **Ingest** — PDF (digital or scanned) or image.
2. **Extract** — Claude reads the document visually and returns JSON in a fixed schema
   (*structured outputs*, so the reply is always valid, machine-readable data).
3. **Validate** — the model flags anything unreadable or that doesn't add up (`needs_review`).
4. **Score** — every field is compared to a hand-verified answer key; money within one cent, dates exact.
5. **Cost** — every call records input/output tokens, converted to dollars at published rates.

### Tuning, and keeping the score honest
The AI's written instructions can be improved from the **Tune** tab, using mistakes on the **practice set**.
The **test set** is kept aside: different invoices, including two layouts the practice set never shows, scored
once at the end. That mirrors a real deployment, where new vendors with unfamiliar layouts show up all the time.

### Why the rule-based comparison matters
""" + RULE_BASED_EXPLAINER + """
The gap between the two is the value of the AI approach: measured, not just claimed.
""")
    st.markdown("### Glossary")
    st.markdown("\n".join(f"- **{k}**: {v}" for k, v in GLOSSARY.items()))
    st.markdown("""
### Known limitations
- Sample invoices are synthetic (so the answer key is exact); real client documents will be messier.
- The prompt was written while looking at these samples; a fair final score should use a held-out set.
- No data leaves the client's control in production without agreement on retention and security.
""")
