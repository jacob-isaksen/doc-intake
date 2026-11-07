# Document Intake

A small pipeline that reads messy business paperwork (invoices, bills of lading, work orders), uses an LLM to pull out structured data, **checks the model's work with rules**, and stores the result in SQLite for reporting.

I built this with Claude Code as my AI coding assistant, and I read and checked what it wrote. The design choices and the results below are ones I can explain and defend.

The documents are fake. They belong to an invented lumber mill, with invented vendors and numbers, and a few are broken on purpose (missing date, totals that don't add up, an invoice sent twice). The point of the project is not "the AI reads PDFs." It is **what you do when the AI is sometimes wrong.**

```
PDF  ->  text (pdfplumber)  ->  LLM (swappable backend)  ->  schema check (Pydantic)
     ->  business rules (totals, dates, duplicates)  ->  SQLite  ->  dashboard / questions
```

## How it handles the model being wrong

| Failure | What catches it |
|---|---|
| Model returns invalid JSON or a wrong type | Pydantic schema check, one retry, then the document is marked `failed` |
| Printed total doesn't equal lines + tax | Validation rule, document marked `flagged` |
| Line quantity x price doesn't equal its amount | Validation rule |
| Missing date, absurd date, missing total | Validation rule |
| Model names our own company as the vendor | Validation rule |
| Same invoice sent twice | Duplicate check in the pipeline |
| Model "helpfully" fixes a wrong number | Prompt says never to; the evaluation counts it as a miss if the defect slips through |

Only documents that pass every check count as **confirmed** spend on the dashboard. Flagged documents show up separately, as spend nobody should trust yet.

## Results (qwen2.5:7b, running locally)

`evaluate.py` compares every extracted field to a hand-checkable answer key. Each document is scored on whether the **review decision** was right: a defective document should reach a human, and a clean one should not.

| Run | Documents | Dates right | Line items right | Defects sent to a human | Clean docs flagged by mistake |
|---|---|---|---|---|---|
| v1 first attempt | main set | 7% | 70% | 6 of 6 | 24 of 24 |
| v2 schema bug fixed | main set | 63% | 70% | 6 of 6 | 10 of 24 |
| v3 prompt clarified | main set | 97% | 87% | 6 of 6 | 2 of 24 |
| **v4** grounding check | main set | 100% | 90% | 6 of 6 | 0 of 24 |
| v3 | holdout 1 | 100% | 97% | 6 of 6 | 0 of 24 |
| **v4** | holdout 1 | 100% | 97% | 6 of 6 | 0 of 24 |
| v3 | **holdout 2 (never looked at before scoring)** | 97% | 90% | **5 of 6** | 0 of 24 |
| **v4** | **holdout 2** | 100% | 90% | **6 of 6** | 0 of 24 |

"Defects sent to a human" alone is misleading: v1 scored 6 of 6 only because it flagged every document. The last column is what shows the tool getting useful. The model is not perfectly repeatable, so rerunning shifts a document or two.

What each step was:
- **v1 -> v2:** Pydantic marks fields with defaults as optional, so the model silently left out every date. The fix was telling the model every field is required (but nullable). This was a bug in my code, not the model's fault.
- **v2 -> v3:** I read the remaining errors and fixed what caused them. The model returned no date when it was labeled "Completed:", glued bundle counts into descriptions, and once named our own company as the vendor. The last one is now also a validation rule.
- **v3 -> v4:** same model, same prompt, plus a **grounding check** (`docintake/grounding.py`). Every number, date and name the model extracts must actually appear in the PDF text. A misread digit (`$26.94` read as `$24.94`) or an invented date fails that test. The model gets one more look with the problem pointed out, and anything still unfound is flagged. The check accepts all 90 correct answers across the three sets, so it adds no false alarms of its own.

How to read this honestly:
- I tuned v3 against the main set, and I read holdout 1's misses afterward, so neither is a clean test anymore. **Holdout 2 is the fair comparison**: v3 missed one defect there (an invented date) and v4 caught it. That is a single document, so it is a good sign, not proof.
- v4 got the review decision perfect on all three sets. It did **not** get every field perfect, and I did not touch the scorer or the answer keys to make it look that way.

What is still wrong in v4:
- **Bill of lading rows** (5 documents across the sets): the 7B model sometimes glues the bundle count onto the description (`..., bundle 6`) and once put the weight in the quantity column. Grounding can't catch this, because every value really is in the text.
- **Wording differences**: the model sometimes drops `, bundle` from a description, which my strict answer key counts as wrong. Every number in those rows was right.
- **A gap in my own rule**: on holdout 2 the model named the buyer *plus its street address* as the vendor, and my "that's our own company" rule only matches an exact name. It was still flagged, for another reason. The next fix is to match on "contains" instead of "equals".
- One quantity misread on the main set (1 instead of 4) was caught only because the line math then failed.

The dashboard hides the earlier attempts (v1 to v3) from its run picker and lists every run in a "Run history" table at the bottom.

Honest limitations:
- On the main set, the invoice with a blank date came back with an **invented** date (2023-11-15). The prompt says never to guess. In v3 it was flagged only because the model also misread its line items, which was luck. v4's grounding check now catches an invented date directly, because the date is not in the PDF.
- The test set is small (30 per run), synthetic, and uses clean text PDFs. Scanned documents with OCR errors would be harder, and are routed to `failed` if there is no text layer at all.
- The Gemini adapter has not been run against the live API.

## Provider-agnostic by design

The pipeline only knows one method, `complete_json(system, user, schema)`. Each LLM lives behind that method in `docintake/backends.py`:

- `OllamaBackend`: runs an open model locally (default `qwen2.5:7b`). Free, and nothing leaves the machine.
- `GeminiBackend`: Google's free-tier API over plain HTTPS. **Written but not yet run against the live API.**

Switching models means passing `--backend`, not changing the pipeline.

## Run it

Requires Python 3.12 and [Ollama](https://ollama.com) with `ollama pull qwen2.5:7b`.

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

python generate_docs.py                          # 30 fake PDFs + answer key in data/
python -m docintake.pipeline --label "my run"    # extract, validate, store
python -m docintake.evaluate --model "ollama:qwen2.5:7b [my run]"
python dashboard.py                              # http://127.0.0.1:8501
python -m docintake.ask "Which vendor did we spend the most with?" --model "ollama:qwen2.5:7b [my run]"
python -m pytest
```

## Asking questions in plain English

The dashboard has an "Ask a question" box at the top. It shows the answer and the exact SQL the model wrote, so you can see how it got there. The same thing works from the command line. `docintake/ask.py` lets the LLM write a SQL query from a question. Model-written SQL is untrusted, so three separate guards sit in front of the database: a text check (one `SELECT` only), SQLite's `query_only` mode, and a SQLite authorizer that denies anything that isn't a read. The tests try to delete and drop tables through it.

## SQL

`docintake/db.py` holds the schema and the reporting views the dashboard reads (spend by vendor, monthly spend, repair cost by equipment, shipped weight by customer, review queue, accuracy by field).

## Notes

- The dashboard uses only the Python standard library. The usual choice, Streamlit, needs pandas and pyarrow, which this Windows machine's Application Control policy blocks from loading.
