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
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..models import EvidenceCaseVM, EvidencePageVM
from .findings import DisclosureCode, DisclosureFinding, DisclosureReport
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


#: Findings whose remedy is an :class:`~.policy.ApprovedText`, not a
#: resolution. Each already has an entry in ``approved_texts``, and
#: :func:`~.findings.partition` would let a resolution clear one — a second,
#: weaker way past a field whose whole point is that it needs a digest-pinned
#: decision about its actual words.
_TEXT_REMEDY_CODES: frozenset[str] = frozenset(
    {
        DisclosureCode.HUMAN_TEXT_REVIEW_REQUIRED.value,
        DisclosureCode.NARRATIVE_SNIPPET_REVIEW_REQUIRED.value,
        DisclosureCode.CURATOR_TEXT_REVIEW_REQUIRED.value,
        DisclosureCode.APPROVAL_DIGEST_MISMATCH.value,
    }
)

#: Where the rendered-HTML scan reports. Those findings are appended after
#: :func:`~.findings.partition` has run, so a resolution written against one
#: would never be consulted. Generating an entry that cannot work is worse
#: than generating none: it reads as an available remedy.
RENDERED_SCAN_PATH = "<rendered html>"

#: Both remedies, in the entry a reviewer is about to fill in. Generating
#: only the signature arm would quietly recommend publishing the country.
_RETAINED_COUNTRY_PROMPT = (
    "Either say why the population is wide enough to publish the country "
    "beside this count, or mask this example's country instead with a "
    "country_masking entry and delete this one."
)


def resolvable(finding: DisclosureFinding) -> bool:
    """Whether a :class:`~.policy.FindingResolution` could reach *finding*."""
    return (
        finding.code.value not in _TEXT_REMEDY_CODES
        and finding.field_path != RENDERED_SCAN_PATH
    )


def export_approvals_form(
    page: EvidencePageVM,
    *,
    report: DisclosureReport | None = None,
    candidate_digest: str | None = None,
) -> dict[str, object]:
    """The decisions a reviewer has to make, unsigned.

    Built in three parts, because they become knowable at three different
    times. Every ``REVIEW`` free-text field blocks categorically, so
    ``approved_texts`` follows from the page alone and is complete before the
    transform runs. A resolution answers a finding a check actually raised,
    so ``resolutions`` needs *report*. A release is about the finished
    document, so it needs *candidate_digest* — the pin that makes it a record
    of what was read rather than a standing permission. Each is omitted
    without its input, which is what the first, pre-transform call produces.

    ``country_masking`` is always empty, and that is not an omission:
    ``mask: true`` and ``mask: false`` are both decisions, so there is no
    neutral entry to generate. The masking remedy is named in the rationale
    of the finding it applies to instead.

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
    resolutions: list[dict[str, object]] = []
    for finding in _needing_a_resolution(report):
        if not resolvable(finding):
            continue
        resolutions.append(
            {
                "code": finding.code.value,
                "example_id": finding.example_id,
                "field_path": finding.field_path,
                "detail_sha256": digest_text(finding.detail),
                "approved_by": "",
                "approved_at": "",
                "rationale": (
                    _RETAINED_COUNTRY_PROMPT
                    if finding.code
                    is DisclosureCode.RETAINED_COUNTRY_SMALL_COUNT
                    else ""
                ),
            }
        )
    form: dict[str, object] = {
        "approved_texts": texts,
        "resolutions": resolutions,
        "country_masking": [],
    }
    if candidate_digest is not None and _releasable(report):
        form[_RELEASE] = {
            "released_by": "",
            "released_at": "",
            "rationale": "",
            "candidate_sha256": candidate_digest,
        }
    return form


#: What identifies "the same entry" in each list, so a regenerated form can
#: find the reviewer's existing decision about it.
_ENTRY_KEYS: dict[str, tuple[str, ...]] = {
    "approved_texts": ("example_id", "field_path"),
    "resolutions": ("code", "example_id", "field_path"),
    "country_masking": ("example_id",),
}

#: ``release`` is one object rather than a list, because it is the one
#: decision about the whole document. Merged under the same rule as the
#: lists: signed, it is a record and is kept; unsigned, it is scaffolding and
#: is regenerated with the current digest.
_RELEASE = "release"

#: The two fields that turn a form entry into a record. ``country_masking``
#: says ``decided_*`` rather than ``approved_*`` because it is a policy
#: decision rather than an approval, but it is signed the same way.
_SIGNATURE_FIELDS: dict[str, tuple[str, str]] = {
    "approved_texts": ("approved_by", "approved_at"),
    "resolutions": ("approved_by", "approved_at"),
    "country_masking": ("decided_by", "decided_at"),
    _RELEASE: ("released_by", "released_at"),
}


def _needing_a_resolution(
    report: DisclosureReport | None,
) -> tuple[DisclosureFinding, ...]:
    """Findings a resolution entry should exist for: unresolved **and
    resolved**.

    The resolved half is not an oversight corrected late — it is the whole
    difference between "orphaned" meaning something and meaning nothing. A
    resolution that works moves its finding out of ``findings`` and into
    ``resolved``, so a list built from ``findings`` alone regenerates no
    entry for it, and the merge then reports every *working* resolution as
    orphaned on the next run. That reads as "obsolete, tidy me up", and
    deleting one re-blocks the run on the finding it was clearing.

    Advisories are deliberately absent. An unsigned advisory gets no entry —
    that remains a decision not to offer one — but an advisory a reviewer
    *has* signed for sits in ``resolved`` like any other, so their signature
    is regenerated rather than reported as stray.
    """
    if report is None:
        return ()
    return (*report.findings, *report.resolved)


def _releasable(report: DisclosureReport | None) -> bool:
    """Whether a release is the decision that remains.

    Scaffolding one onto a run that still has findings or unsigned advisories
    would invite signing something the gate must refuse, and would pin a
    candidate that is about to change — so the entry would go stale before
    anyone read it. The release is offered last because it is decided last.
    """
    if report is None:
        return False
    return not report.findings and not report.advisories


def _key(entry: dict[str, object], fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(entry.get(field, "")) for field in fields)


def _is_signed(entry: dict[str, object], listing: str) -> bool:
    """Whether this entry is a reviewer's record rather than scaffolding."""
    return all(
        str(entry.get(field, "")).strip()
        for field in _SIGNATURE_FIELDS[listing]
    )


@dataclass(frozen=True)
class ApprovalsFormWrite:
    """What a form write did, which a merge makes worth reporting.

    Without this a reviewer cannot tell from the exit code whether their
    signatures survived the regeneration, which is the only question they
    have about it.
    """

    path: Path
    #: Entries the run needs that the file did not have.
    added: int
    #: Signed entries carried through untouched.
    kept_signed: int
    #: Unsigned entries replaced with freshly generated ones — a stale digest
    #: on an unsigned entry is scaffolding that would fail on load, so
    #: refreshing it is the point rather than a liberty.
    refreshed: int
    #: Signed entries the run no longer asks for, kept anyway. A reviewer's
    #: record is not ours to drop because the manifest moved on; it may also
    #: be the only trace of a decision about a case that was removed.
    orphaned: int
    #: Whether a signed release was reset because the document changed under
    #: it. Called out on its own because it is the one merge outcome that
    #: needs the reviewer to do something before the run can publish.
    release_reset: bool = False

    def summary(self) -> str:
        """One line for the CLI."""
        return (
            f"{self.added} new, {self.kept_signed} signed kept, "
            f"{self.refreshed} unsigned refreshed, {self.orphaned} orphaned"
        )


def _load_existing(out: Path) -> dict[str, object]:
    """The file as written, or a clear error rather than a guess.

    Read as raw JSON and not through :class:`~.policy.PublicationApprovals`,
    because a form that still has blanks in it does not validate as one — see
    the module docstring on why it is generated invalid.
    """
    try:
        loaded = json.loads(out.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(
            f"{out} is not readable as JSON ({err}). It may hold signed "
            f"approvals, so it is not overwritten; fix or move it, or pass "
            f"force=True (CLI: --force) to replace it."
        ) from err
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{out} does not hold an approvals form (expected an object with "
            f"approved_texts, resolutions and country_masking)."
        )
    existing: dict[str, object] = {}
    for listing in _ENTRY_KEYS:
        rows = loaded.get(listing, [])
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise ValueError(
                f"{out} has a {listing} that is not a list of entries; it is "
                f"not overwritten."
            )
        existing[listing] = list(rows)
    held = loaded.get(_RELEASE)
    if held is not None:
        if not isinstance(held, dict):
            raise ValueError(
                f"{out} has a {_RELEASE} that is not an entry; it is not "
                f"overwritten."
            )
        existing[_RELEASE] = dict(held)
    return existing


def merge_approvals_form(
    existing: dict[str, object],
    fresh: dict[str, object],
    *,
    authoritative: Collection[str] = (),
) -> tuple[dict[str, object], dict[str, int]]:
    """Fold a freshly generated form into the file already on disk.

    One rule, applied per entry: **a signed entry is a record and is never
    touched; an unsigned entry is scaffolding and is regenerated.** That is
    what makes a second run safe to do by default. Keeping an unsigned entry
    would hand back a digest that no longer matches anything, and rewriting a
    signed one would move the subject out from under a signature — the two
    failures this file exists between.

    Entries the run no longer asks for are appended rather than dropped, for
    the reason :func:`~..collection.write_summary_drafts` refuses to
    overwrite: replacing a reviewer's record with a blank is the one outcome
    no amount of convenience justifies.

    *authoritative* names the listings this form actually generated, and only
    those are compared. An empty ``resolutions`` means two different things —
    "the run raised nothing" and "this call had no report to look at" — and
    without the distinction the pre-transform write would report every signed
    resolution as orphaned on every run. ``country_masking`` is never
    generated at all, so it is never authoritative and always passes through
    untouched.
    """
    merged: dict[str, object] = {}
    stats = {"added": 0, "kept_signed": 0, "refreshed": 0, "orphaned": 0}
    for listing, fields in _ENTRY_KEYS.items():
        if listing not in authoritative:
            merged[listing] = list(
                _rows(existing, listing) or _rows(fresh, listing)
            )
            continue
        held = {
            _key(entry, fields): entry for entry in _rows(existing, listing)
        }
        seen: set[tuple[str, ...]] = set()
        rows: list[dict[str, object]] = []
        for entry in _rows(fresh, listing):
            key = _key(entry, fields)
            seen.add(key)
            previous = held.get(key)
            if previous is None:
                stats["added"] += 1
                rows.append(entry)
            elif _is_signed(previous, listing):
                stats["kept_signed"] += 1
                rows.append(previous)
            else:
                stats["refreshed"] += 1
                rows.append(entry)
        for key, entry in held.items():
            if key in seen:
                continue
            if _is_signed(entry, listing):
                stats["orphaned"] += 1
            rows.append(entry)
        merged[listing] = rows

    # The release, under the same rule. Held as one object because it is the
    # one decision about the whole document, so there is no key to match on:
    # signed, it is kept; unsigned, it is replaced with the fresh skeleton
    # and its digest. A call with no digest did not look at the release at
    # all, so it passes whatever is there through untouched.
    held_release = existing.get(_RELEASE)
    fresh_release = fresh.get(_RELEASE)
    if _RELEASE not in authoritative:
        if held_release is not None:
            merged[_RELEASE] = held_release
    elif isinstance(held_release, dict) and _is_signed(held_release, _RELEASE):
        if _still_pins(held_release, fresh_release):
            stats["kept_signed"] += 1
            merged[_RELEASE] = held_release
        else:
            # A signed release whose pin no longer matches is void, and
            # unlike a stale text approval there is nothing partial to
            # salvage: it speaks for the whole document, and the document is
            # a different one. Keeping it would leave the reviewer editing a
            # digest by hand beside a date that says they read something
            # else — so it is reset to an unsigned skeleton, which forces a
            # fresh name and a fresh date for what is a fresh decision. The
            # previous run's key record keeps the superseded signature.
            stats["refreshed"] += 1
            stats["release_reset"] = 1
            merged[_RELEASE] = fresh_release if fresh_release else {}
    elif fresh_release is not None:
        stats["refreshed" if held_release is not None else "added"] += 1
        merged[_RELEASE] = fresh_release
    return merged, stats


def _still_pins(held: dict[str, object], fresh: object) -> bool:
    """Whether a signed release was given for the candidate now in hand.

    ``True`` when there is no fresh release to compare against: a call that
    generated none did not look, and not looking is not grounds for voiding
    somebody's signature.
    """
    if not isinstance(fresh, dict):
        return True
    return held.get("candidate_sha256") == fresh.get("candidate_sha256")


def _rows(form: dict[str, object], listing: str) -> list[dict[str, object]]:
    """One of the three lists, typed for the merge."""
    rows = form.get(listing, [])
    assert isinstance(rows, list)  # noqa: S101 - _load_existing validates
    return rows


def write_approvals_form(
    page: EvidencePageVM,
    path: str | Path,
    *,
    force: bool = False,
    report: DisclosureReport | None = None,
    candidate_digest: str | None = None,
) -> ApprovalsFormWrite:
    """Write the form, folding it into whatever is already there.

    A second run is the normal case, not the exception: findings change as
    approvals land and as the manifest is re-curated, so the form a reviewer
    needs is rarely the one generated first. Merging by default is what makes
    that workable — ``force`` still regenerates from scratch, and now really
    does discard signatures, so it is the deliberate reset rather than the
    way to get a second form at all.
    """
    out = Path(path)
    fresh = export_approvals_form(
        page, report=report, candidate_digest=candidate_digest
    )
    generated = {"approved_texts"}
    if report is not None:
        generated.add("resolutions")
    if candidate_digest is not None and _releasable(report):
        generated.add(_RELEASE)
    if out.exists() and not force:
        merged, stats = merge_approvals_form(
            _load_existing(out), fresh, authoritative=generated
        )
    else:
        merged, stats = fresh, {
            "added": sum(
                len(value) if isinstance(value, list) else 1
                for value in fresh.values()
            ),
            "kept_signed": 0,
            "refreshed": 0,
            "orphaned": 0,
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    reset = bool(stats.pop("release_reset", 0))
    return ApprovalsFormWrite(path=out, release_reset=reset, **stats)


__all__ = [
    "RENDERED_SCAN_PATH",
    "ApprovalsFormWrite",
    "export_approvals_form",
    "is_generated_summary",
    "merge_approvals_form",
    "resolvable",
    "slots_needing_approval",
    "write_approvals_form",
]
