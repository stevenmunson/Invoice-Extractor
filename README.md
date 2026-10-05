# Invoice Extractor

Turns messy invoices (any layout, digital PDFs or scans) into clean, structured data using Claude. Every
result is **scored against an answer key**, **costed per invoice**, and compared with a traditional
rule-based approach. Uncertain invoices go to a **human review queue**.

**Live demo:** https://invoice-extractor-bvd2cdqre4pbrydbdrwmeq.streamlit.app/

---

## Results

60 synthetic invoices: a **practice set** (30 invoices, 6 layouts) used for tuning, and a **held-out test
set** (30 different invoices, including 2 layouts the practice set never shows) scored once at the end.
10 fields per invoice: vendor, invoice #, invoice date, due date, bill-to, currency, subtotal, tax,
total and line items.

| Method | Practice set | Held-out test set | Flagged for review (test) | Cost per invoice |
|---|---|---|---|---|
| Rule-based (text + pattern matching, no AI) | 44% of fields · 0/30 perfect | 34% of fields · 0/30 perfect | n/a, never flags | $0 |
| Claude Haiku 4.5 | 100% · 30/30 perfect | 100% · 30/30 perfect | 3, all false alarms | ~$0.004 |
| **Claude Sonnet 5.5** | **100% · 30/30 perfect** | **100% · 30/30 perfect** | **0** | **~$0.012** |

That's roughly **$4 vs. $12 per 1,000 invoices**, against an estimated ~$4,700/month to key 2,000 invoices
by hand (4 min each at $35/hour; the app's Business case tab lets you plug in a client's own numbers).

**Recommendation: Sonnet.** Both models extracted everything correctly in the final runs, but Haiku's
self-flagging was unreliable (see below). The extra cost is about $8 per 1,000 invoices.

---

## What I learned

- **Rules don't handle new layouts well.** The rule-based reader reaches ~90% on the two layouts its rules were
  written for, drops to 34% overall on the test set's unfamiliar layouts, and can't read scans at all.
- **Tuning works, but measure every change.** One added instruction (strip department names like "Accounts
  Payable, …" from the bill-to field) took Haiku from 28/30 to 30/30 perfect invoices on the practice set.
- **One run isn't a measurement.** On one run with the same instructions, Haiku misread a scanned receipt
  number (R812285 for R81285) and got it right on a rerun. AI output varies a little run to run,
  especially on poor scans, so production needs checks that catch errors, not just a good score.
- **A model's own flags can't be taken on trust.** Every invoice Haiku flagged in the final runs (9 across both
  sets) was actually correct, mostly false alarms about discounts and on which balance to apply tax, while in an earlier run its one real
  mistake went *unflagged*. Sonnet flagged nothing and made no mistakes. Unreliable flags mean wasted review
  time *and* errors slipping through, which matters more than the price difference.
- **APIs change underneath you.** Mid-project, the newer models stopped accepting the "forced tool use" method
  the original code used for structured output, so we switched to structured outputs supoprted by the model provider. Any model upgrade should be re-tested
  against the answer key before switching.

---

## How it works

1. **Read:** each invoice (PDF or image) is sent to Claude with a short set of written instructions.
2. **Structure:** Claude must reply as JSON in a fixed schema (structured outputs), so results always load cleanly.
3. **Flag:** Claude marks anything unreadable or inconsistent as `needs_review`, with a reason.
4. **Score:** every field is compared to the answer key: money to the cent, dates exactly, names ignoring case and punctuation.
5. **Cost:** every call records input/output tokens, converted to dollars at published prices.

### The app

| Tab | Purpose |
|---|---|
| Try an invoice | Run one invoice (sample or upload) and see each field next to the right answer |
| Accuracy & cost report | Run a full set; compare models and instruction versions by field, layout and cost; business-case calculator |
| Review | The analyst's view: flagged invoices plus a random **spot-check** of unflagged ones, the image beside editable fields, saved corrections, CSV export |
| Tune | Edit the AI's instructions using the practice set's mistakes; each save is a new version so changes can be compared |
| How it works | Explanations and a glossary |

### Design choices

- **Practice/test split:** tune only on practice; report the held-out score. The test set includes unseen layouts, as a real client's new vendors would.
- **Spot-checks measure silent errors:** once there's no answer key, a random sample of *unflagged* invoices is the only way to estimate mistakes the AI doesn't know it made.
- **Synthetic data:** generated invoices give an exact answer key and contain no real financial data, so the repository is safe to make public.

---

## Limitations and next steps

- **Small sample.** By the "rule of three", 60 perfect invoices shows the per-invoice error rate is probably
  below ~5%, not that it's zero. A client deployment would test on a few hundred of their real invoices
  (often already keyed into their accounting system, so the answer key is free).
- **Synthetic invoices are cleaner than real ones:** no handwriting, stamps, phone photos or folded paper.
- **Next steps for production:**
  - automatic math checks (line items = subtotal, subtotal + tax = total) to catch misreads without AI
  - matching against the client's vendor list, purchase orders and already-paid invoice numbers
  - learning each vendor's usual invoice-number format from history to flag anomalies
  - tuning Haiku's flagging (most false alarms involved discounts) or routing scans to Sonnet and clean PDFs to Haiku
  - automatic intake (shared inbox or folder) and posting approved results straight into the accounting system
  - a monitoring dashboard: accuracy by vendor and field over time, spot-check error rate, cost

---

## Run it yourself

Requires Python 3.11–3.14 and an [Anthropic API key](https://console.anthropic.com).

```bash
pip install -r requirements.txt
streamlit run app.py
```

Paste the API key in the app's sidebar, or put it in `.streamlit/secrets.toml` (see `secrets.toml.example`).
The rule-based baseline and saved results work without a key.

**On Windows without the command line:** double-click `start_app.bat`. Step-by-step instructions and
troubleshooting are in [SETUP.md](SETUP.md).

Command-line evaluation (optional):

```bash
python scripts/run_eval.py --method claude --set practice
python scripts/run_eval.py --method claude --model claude-haiku-4-5-20251001 --set test
python scripts/generate_invoices.py   # rebuild both invoice sets and answer keys
```

### Project layout

```
app.py                      Streamlit app
src/extract_claude.py       Claude extraction (structured outputs)
src/extract_baseline.py     Rule-based comparison (pdfplumber + regex)
src/schema.py               Fields to extract + default instructions
src/evaluate.py             Field-by-field scoring
src/pipeline.py             Batch runs over practice/test sets, saved to results/
src/prompt_store.py         Versioned instructions (instructions.txt)
src/review_store.py         Review queue, spot-check sampling, saved corrections
src/costs.py                Token prices -> dollars
scripts/                    Invoice generator, command-line evaluation
data/                       Practice and test invoices with answer keys
results/                    Saved evaluation runs
```

---

Built with Claude as an AI coding assistant. I scoped the problem, directed the build, ran and tuned the
evaluations, reviewed the results, and made the model and design decisions.
