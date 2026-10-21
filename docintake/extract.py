"""PDF -> text -> LLM -> validated ExtractedDoc."""
import json
from pathlib import Path

import pdfplumber
from pydantic import ValidationError

from .backends import Backend
from .config import COMPANY
from .grounding import find_ungrounded
from .schema import ExtractedDoc

SYSTEM_PROMPT = f"""You extract structured data from business documents for a lumber mill, {COMPANY}.
Return JSON that matches the schema exactly.

Rules:
- doc_type is one of: invoice, bill_of_lading, work_order.
- doc_date is the date printed on the document, whatever it is labeled (Date, Invoice Date,
  Ship Date, Completed). Convert it to ISO format, YYYY-MM-DD. US dates are month/day/year.
  If the document truly has no date, use null.
- Each line item is one table row. The row's quantity (or hours, or bundle count) is its own
  column, so keep it out of the description, even when it is printed before or right after it.
- counterparty is the other company on the document: the vendor who issued an invoice,
  the consignee (receiver) on a bill of lading, or the contractor who did a work order.
  It is never {COMPANY}.
- equipment is the machine a work order was done on. Use null for other document types.
- Copy numbers exactly as printed, without currency symbols or commas.
  Do not recalculate, fix, or fill in anything, even if the totals look wrong.
- If a value is not on the document, use null. Never guess.
- A bill of lading has no dollar amounts. Each row reads: description, bundle count, weight in
  pounds. Put the bundle count in quantity, the row's weight in weight_lb, and the
  overall weight in total_weight_lb.
"""


class ExtractionError(Exception):
    """The document could not be turned into a valid ExtractedDoc."""


def read_pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()


def llm_schema() -> dict:
    """The JSON schema we hand to the model.

    Pydantic marks fields that have defaults as optional, and a constrained model
    then simply leaves them out. Making every field required (but still nullable)
    forces it to answer each one, with null when the document doesn't say.
    """
    schema = ExtractedDoc.model_json_schema()
    for part in [schema, *schema.get("$defs", {}).values()]:
        part["required"] = list(part.get("properties", {}))
    return schema


RETRY_NOTE = (
    "\n\n---\nA first reading of this document had problems: {problems}. "
    "Read the document again, character by character, and answer again. "
    "Copy numbers exactly as printed. If a value is not printed, use null."
)


def extract(text: str, backend: Backend, attempts: int = 3, ground: bool = True) -> tuple[ExtractedDoc, list[str]]:
    """Ask the model, validate the answer, and try again when the answer is unusable or ungrounded.

    Returns the best answer and any values in it that still cannot be found in the text.
    A reply that is not valid JSON is retried as is. A reply with values that are missing
    from the document is retried with those values pointed out.
    """
    schema = llm_schema()
    last_error = ""
    note = ""
    best: tuple[ExtractedDoc, list[str], int] | None = None
    for _ in range(attempts):
        try:
            raw = backend.complete_json(SYSTEM_PROMPT, text + note, schema)
            doc = ExtractedDoc.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as error:
            last_error = str(error).splitlines()[0][:200]
            continue

        issues = find_ungrounded(doc, text) if ground else []
        problems = [f"{issue} does not appear in the document" for issue in issues]
        if ground and doc.counterparty.strip().casefold() == COMPANY.casefold():
            problems.append(f"the counterparty cannot be {COMPANY}, which is us. It is the other company")
        if best is None or len(problems) <= best[2]:
            best = (doc, issues, len(problems))
        if not problems:
            break
        note = RETRY_NOTE.format(problems="; ".join(problems))

    if best is None:
        raise ExtractionError(last_error)
    return best[0], best[1]
