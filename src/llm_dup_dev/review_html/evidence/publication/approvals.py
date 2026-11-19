"""Turning a blocked run into a form a reviewer can sign.

Every ``REVIEW`` field blocks categorically. The policy cannot tell a
curator's safe title from one naming a clinician or a town, so it asks for a
signature on all of them — including the ones that carry nothing identifying,
because "carries nothing" is a conclusion a human reaches and not one the
transform can reach for them.

What that signature is *not* for is retyping the manifest's own text into a
second file. That costs a curator the same words twice and buys two hazards:
a :attr:`~.policy.ApprovedText.replacement` is published verbatim rather than
through the substitution table, so a copied title quoting a report date
publishes the real date; and an approval keyed only to a path outlives any
later edit to the manifest, silently publishing the text that was approved in
place of the text that is there now.

So this module writes the other kind of entry — ``approve_as_written``,
pinned to :func:`~.policy.digest_text` of what the reviewer is looking at.
The form it produces is deliberately *unsigned*, and therefore deliberately
invalid: ``approved_by`` and ``approved_at`` are empty, so
:class:`~.policy.PublicationApprovals` refuses it until somebody fills them
in. A form that validated as it came out of the generator would make the
signature a formality, which is the one thing this layer must not do.

The form carries paths and digests, never the text. What the reviewer reads
is the side-by-side disclosure review, which prints the originals and is
written on every run, blocked or not.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from ..models import EvidenceCaseVM, EvidencePageVM
from .policy import FREE_TEXT_FIELDS, FreeTextPolicy, digest_text
from .substitute import TextSlot, walk_text_slots


def is_generated_summary(slot: TextSlot) -> bool:
    """The deterministic summary block, which needs no decision.

    vigiTwin's own account of the pair, generated from the assessment. It
    holds no human's words, so it is rewritten through the substitution table
    like any other derived prose. A block flagged ``is_human`` never is.
    """
    return slot.key == "EvidenceTextVM.paragraphs" and not getattr(
        slot.owner, "is_human", False
    )


def slots_needing_approval(case: EvidenceCaseVM) -> Iterator[TextSlot]:
    """Every field of one case that blocks until a reviewer decides.

    Empty fields are skipped, exactly as the transform skips them: a kicker
    nobody wrote is not a decision anybody owes.
    """
    for slot in walk_text_slots(case):
        if FREE_TEXT_FIELDS.get(slot.key) is not FreeTextPolicy.REVIEW:
            continue
        if is_generated_summary(slot):
            continue
        value = slot.read()
        if value is None or value == "" or (isinstance(value, list) and not value):
            continue
        yield slot


def export_approvals_form(page: EvidencePageVM) -> dict[str, object]:
    """One ``approve_as_written`` entry per field that needs a signature.

    Returned as plain data rather than a :class:`~.policy.PublicationApprovals`
    because it is not one yet — see the module docstring on why the form comes
    out unsigned.
    """
    texts: list[dict[str, object]] = []
    for case in page.cases:
        for slot in slots_needing_approval(case):
            value = slot.read()
            assert value is not None  # noqa: S101 - slots_needing_approval filters
            texts.append(
                {
                    "example_id": case.example_id,
                    "field_path": slot.path,
                    "approve_as_written": True,
                    "text_sha256": digest_text(value),
                    "approved_by": "",
                    "approved_at": "",
                    "rationale": "",
                }
            )
    return {
        "approved_texts": texts,
        "resolutions": [],
        "country_masking": [],
    }


def write_approvals_form(
    page: EvidencePageVM, path: str | Path, *, force: bool = False
) -> Path:
    """Write the form to a separate file.

    Refuses to overwrite without ``force``, for the reason
    :func:`~..collection.write_summary_drafts` does: by the second run that
    file carries signatures, and replacing a reviewer's record with a blank
    form is the failure this arrangement exists to prevent.
    """
    out = Path(path)
    if out.exists() and not force:
        raise FileExistsError(
            f"{out} already exists. It may hold signed approvals; pass "
            f"force=True (CLI: --force) to replace it."
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(export_approvals_form(page), indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    return out


__all__ = [
    "export_approvals_form",
    "is_generated_summary",
    "slots_needing_approval",
    "write_approvals_form",
]
