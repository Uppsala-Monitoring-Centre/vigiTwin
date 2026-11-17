"""The substitution table, the tree walk, and the residual scan.

Three pieces that between them make "all occurrences of a transformed value
are consistent" a property of the design rather than something to be checked
case by case.

**The table.** Every value the transform rewrites is recorded as an
original → published pair. Because the record is a table and not a sequence
of edits, the same original cannot come out two ways: a second attempt to map
it differently is a conflict, and a conflict is a finding. The table is then
the one source for rewriting prose, for the residual scan, for the secure key
record and for the side-by-side review — four jobs that would otherwise be
four chances to disagree.

**The walk.** The evidence view model shares objects deliberately:
``CaseSummaryVM.anchor_sets`` holds the *same* ``SharedSetVM`` objects as
``EvidenceCaseVM.shared_sets``, a demographic ``DiscrepancyVM.left`` is the
*same* ``EvidenceCell`` as the characteristics row's, and a drug row's side
names mirror its canonical name. That sharing is what makes one rewrite reach
the table, the chip and the finding at once — and it means a naive recursive
edit would offset the same value two or three times. Every walk here is
guarded by object identity, so each object is visited once, at the first path
it was reached by.

A deep copy is taken before any of this, and ``copy.deepcopy`` carries a
single memo through the whole tree, so the sharing survives into the
publication copy while the internal tree is left untouched.

**The scan.** After every transform has run, the output is searched for
anything recognisably original. It assumes the transforms were incomplete,
which is the only safe assumption to make about them — and it has to do that
without crying wolf, because a report nobody can read through is a report
nobody reads. Three things make the difference between a finding and noise:

* **Dates are compared as whole tokens.** ``2023`` sits inside
  ``2023-12-05``, so a substring search for an unchanged year-only date finds
  it in every complete date that shares its year. Dates are therefore
  extracted as maximal tokens and compared as units.
* **A figure is not a date.** A co-occurrence count of 2019 reports is not a
  year, and the fields that hold counts, scores, contributions and weights
  are named in :data:`~.policy.NUMERIC_TEXT_FIELDS` and exempted from
  date-shape detection. They are still searched for original *values*, which
  is the check that would catch a date written into one by mistake.
* **A bare four-digit token is only a year inside the policy's era window.**
  Outside it there is nothing a reader could read as a date.

A value that is both an original and a published value is not searched for at
all: finding it proves nothing either way. Published values may legitimately
equal reported ones — a transform forbidden from producing a value the pair
reported would be a transform whose output told you what the pair reported.
"""

from __future__ import annotations

import re
import types
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Union, get_args, get_origin

from pydantic import BaseModel

from ..models import EvidenceCell
from .findings import DisclosureCode, DisclosureFinding
from .policy import NUMERIC_TEXT_FIELDS, PublicationPolicy

#: How the builder joins a report's several candidate values for one field
#: (``build._sourced_text`` and friends). A cell is therefore not always one
#: value, and the transform works on components.
MULTI_VALUE_SEPARATOR = "; "

#: Boundary for replacing a value inside prose. Not ``\b``: report values
#: contain hyphens and spaces, and ``\b`` would match inside ``2023-11-13``.
_LEFT_BOUNDARY = r"(?<![0-9A-Za-z])"
_RIGHT_BOUNDARY = r"(?![0-9A-Za-z])"

#: An ISO-date-shaped token anywhere in a string. Greedy, so
#: ``2023-12-05`` yields one token and not three: the whole point is that a
#: year is not a substring match inside a complete date.
_DATE_TOKEN_RE = re.compile(
    r"(?<![0-9A-Za-z])(\d{4}(?:-\d{2}(?:-\d{2})?)?)(?![0-9A-Za-z-])"
)

#: A digit run long enough to be a case number rather than a count. Counts are
#: comma-grouped by ``build._format_count``, so they never reach seven.
_DIGIT_RUN_RE = re.compile(r"\d{7,}")

#: Paths whose contents describe the *run* rather than the reports: a run id
#: and a commit sha are long hex strings, and a render timestamp is a date by
#: construction. Scanning them produces nothing but noise about values that
#: were never anybody's.
_SCAN_EXEMPT_PREFIXES = ("provenance.",)
_SCAN_EXEMPT_PATHS = frozenset({"generated_at"})


@dataclass(frozen=True)
class Substitution:
    """One value, as reported and as published."""

    original: str
    published: str
    #: What class of value this is — a ``ValueKind`` value, or an alias class
    #: such as ``"country"`` or ``"identifier"``. Recorded so the side-by-side
    #: review can group the changes a reader will be checking.
    kind: str
    note: str = ""

    @property
    def changed(self) -> bool:
        return self.original != self.published


@dataclass
class SubstitutionTable:
    """Original → published, for one case."""

    entries: dict[str, Substitution] = field(default_factory=dict)
    #: Originals that were mapped two different ways. Within one case each
    #: becomes a finding: a value that comes out differently in two places is
    #: exactly the inconsistency across tables this layer exists to prevent.
    #: Across cases they are expected — each pair has its own offset — which
    #: is why a merged table is only ever used for prose, never as a case's
    #: own answer.
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)
    #: Published values from elsewhere, for a merged table. A value this
    #: table did not produce but another case did is still not something the
    #: residual scan can look for: see :meth:`searchable_originals`.
    extra_published: set[str] = field(default_factory=set)

    def add(
        self, original: str, published: str, *, kind: str, note: str = ""
    ) -> None:
        """Record one mapping, or a conflict with what is already recorded."""
        key = original.strip()
        if not key:
            return
        existing = self.entries.get(key)
        if existing is not None:
            if existing.published != published:
                self.conflicts.append((key, existing.published, published))
            return
        self.entries[key] = Substitution(
            original=key, published=published, kind=kind, note=note
        )

    def published_for(self, original: str) -> str | None:
        entry = self.entries.get(original.strip())
        return None if entry is None else entry.published

    def changed_originals(self) -> list[str]:
        """Originals whose published form differs, longest first.

        Longest first so a composite is replaced before one of its parts: with
        ``2023-11-13`` ahead of ``2023`` in the alternation, the regex engine
        tries the full date at a given position before the bare year.
        """
        return sorted(
            (e.original for e in self.entries.values() if e.changed),
            key=len,
            reverse=True,
        )

    def published_values(self) -> set[str]:
        return {e.published for e in self.entries.values()} | self.extra_published

    def searchable_originals(self) -> list[str]:
        """Originals the residual scan can look for without false positives.

        A value that is both an original and a published value cannot be
        searched for: finding it proves nothing either way. The date and
        numeric solvers reject offsets that create such a collision, so this
        normally drops nothing — it is here so a collision arising some other
        way degrades the scan rather than filling the report with noise.
        """
        published = self.published_values()
        return [
            original
            for original in self.changed_originals()
            if original not in published
        ]

    def rewrite(self, text: str) -> str:
        """Replace every recorded value inside *text*, in one pass.

        One pass, so a published value that happens to equal another
        original is not rewritten again.
        """
        if not text:
            return text
        originals = self.changed_originals()
        if not originals:
            return text
        pattern = re.compile(
            _LEFT_BOUNDARY
            + "(?:"
            + "|".join(re.escape(value) for value in originals)
            + ")"
            + _RIGHT_BOUNDARY
        )
        return pattern.sub(
            lambda m: self.entries[m.group(0)].published, text
        )

    def conflict_findings(self, example_id: str) -> list[DisclosureFinding]:
        return [
            DisclosureFinding(
                code=DisclosureCode.SUBSTITUTION_CONFLICT,
                example_id=example_id,
                message=(
                    f"{original!r} was mapped to two different published "
                    "values, so the page would show it inconsistently"
                ),
                detail=f"{first!r} and {second!r}",
            )
            for original, first, second in self.conflicts
        ]


# ---------------------------------------------------------------------------
# Identity-guarded tree walks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextSlot:
    """One string-bearing field of one model instance."""

    owner: BaseModel
    field_name: str
    #: Dotted path with list indices, e.g.
    #: ``cases[0].characteristics[1].left_evidence``.
    path: str
    #: ``"ClassName.field"`` — the key into the policy's field inventory.
    key: str
    is_list: bool

    def read(self) -> str | list[str] | None:
        value = getattr(self.owner, self.field_name)
        if isinstance(value, str) or isinstance(value, list) or value is None:
            return value
        return None  # pragma: no cover - the walk only yields str fields

    def write(self, value: str | list[str]) -> None:
        setattr(self.owner, self.field_name, value)


def date_tokens(text: str) -> list[str]:
    """Every maximal ISO-date-shaped token in *text*.

    Maximal is the whole contribution: comparing tokens rather than searching
    for substrings is what stops an unchanged ``2023`` being reported as a
    leak in every complete date of that year.
    """
    return _DATE_TOKEN_RE.findall(text)


def is_date_shaped(value: str) -> bool:
    """Whether *value* on its own reads as an ISO date."""
    stripped = value.strip()
    return bool(stripped) and date_tokens(stripped) == [stripped]


def _is_plain_str(annotation: object) -> bool:
    """Whether an annotation is ``str`` and not a ``str``-valued enum.

    ``EvidenceRole`` and ``ValueKind`` subclass ``str``, so a runtime
    ``isinstance`` check would sweep every enum field into the text
    inventory. The annotation says what the field *is*.
    """
    return annotation is str


def _text_shape(annotation: object) -> str | None:
    """``"str"``, ``"list"`` or ``None`` for an annotation.

    Driven by the annotation rather than the value, so an empty list is not
    mistaken for a ``list[str]`` and an unset ``str | None`` is still
    reported — a field needs a policy whether or not this case populated it.
    """
    if _is_plain_str(annotation):
        return "str"
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return _text_shape(members[0])
        return None
    if origin is list:
        args = get_args(annotation)
        if len(args) == 1 and _is_plain_str(args[0]):
            return "list"
    return None


def walk_models(root: BaseModel) -> Iterator[tuple[str, BaseModel]]:
    """Every model in the tree, once each, at the first path it was reached by."""
    seen: set[int] = set()
    stack: list[tuple[str, BaseModel]] = [("", root)]
    while stack:
        path, node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        yield path, node
        children: list[tuple[str, BaseModel]] = []
        for name in type(node).model_fields:
            value = getattr(node, name, None)
            child_path = name if not path else f"{path}.{name}"
            if isinstance(value, BaseModel):
                children.append((child_path, value))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, BaseModel):
                        children.append((f"{child_path}[{index}]", item))
        # Reversed, so a depth-first walk reports fields in declaration order.
        stack.extend(reversed(children))


def walk_cells(root: BaseModel) -> Iterator[tuple[str, EvidenceCell]]:
    """Every :class:`EvidenceCell` in the tree, once each.

    Once each is the point: a demographic cell is reachable from both the
    characteristics table and the discrepancy that quotes it, and offsetting
    it twice would put a different value on the page from the one the
    transform recorded.
    """
    for path, node in walk_models(root):
        if isinstance(node, EvidenceCell):
            yield path, node


def walk_text_slots(root: BaseModel) -> Iterator[TextSlot]:
    """Every ``str`` and ``list[str]`` field in the tree, once per owner."""
    for path, node in walk_models(root):
        class_name = type(node).__name__
        for name, info in type(node).model_fields.items():
            shape = _text_shape(info.annotation)
            if shape is None:
                continue
            if isinstance(getattr(node, name, None), Enum):  # pragma: no cover
                continue
            yield TextSlot(
                owner=node,
                field_name=name,
                path=name if not path else f"{path}.{name}",
                key=f"{class_name}.{name}",
                is_list=shape == "list",
            )


# ---------------------------------------------------------------------------
# Residual scan
# ---------------------------------------------------------------------------


def _found(original: str, chunk: str) -> bool:
    """Whether *original* occurs in *chunk* as a value rather than a substring.

    A date is matched against the chunk's own maximal date tokens; anything
    else against a word-ish boundary, which is right for a country code, an
    identifier or a quantity with its unit.
    """
    if is_date_shaped(original):
        return original in date_tokens(chunk)
    return bool(
        re.search(_LEFT_BOUNDARY + re.escape(original) + _RIGHT_BOUNDARY, chunk)
    )


def scan_residual(
    root: BaseModel,
    table: SubstitutionTable,
    *,
    example_id: str,
    published_dates: Sequence[str] = (),
    policy: PublicationPolicy | None = None,
) -> list[DisclosureFinding]:
    """Everything recognisably original still present in *root*.

    Deliberately suspicious of the transforms that ran before it. Three
    things are looked for, in descending order of certainty: a value still in
    the table's key set (certain), a date-shaped token that is not one of the
    dates the transform produced (near-certain — an untransformed date), and a
    long digit run (a case number, probably).
    """
    settings = policy or PublicationPolicy()
    findings: list[DisclosureFinding] = []
    originals = table.searchable_originals()
    allowed_dates = set(published_dates) | table.published_values()

    for slot in walk_text_slots(root):
        if (
            slot.path.startswith(_SCAN_EXEMPT_PREFIXES)
            or slot.path in _SCAN_EXEMPT_PATHS
        ):
            continue
        value = slot.read()
        if value is None:
            continue
        chunks = value if isinstance(value, list) else [value]
        for chunk in chunks:
            if not chunk:
                continue
            for original in originals:
                if _found(original, chunk):
                    findings.append(
                        DisclosureFinding(
                            code=DisclosureCode.RESIDUAL_ORIGINAL_VALUE,
                            example_id=example_id,
                            field_path=slot.path,
                            message=(
                                f"{slot.key} still contains {original!r}, "
                                "which the transform replaced elsewhere"
                            ),
                            detail=chunk,
                        )
                    )
            if slot.key not in NUMERIC_TEXT_FIELDS:
                for token in date_tokens(chunk):
                    if token in allowed_dates:
                        continue
                    if len(token) == 4 and not (
                        settings.date_year_floor
                        <= int(token)
                        <= settings.date_year_ceiling
                    ):
                        # A four-digit figure outside any plausible era is a
                        # number that happens to have four digits.
                        continue
                    findings.append(
                        DisclosureFinding(
                            code=DisclosureCode.RESIDUAL_DATE_TOKEN,
                            example_id=example_id,
                            field_path=slot.path,
                            message=(
                                f"{slot.key} contains the date-shaped token "
                                f"{token!r}, which the transform did not "
                                "produce"
                            ),
                            detail=chunk,
                        )
                    )
            for run in _DIGIT_RUN_RE.findall(chunk):
                findings.append(
                    DisclosureFinding(
                        code=DisclosureCode.RESIDUAL_DIGIT_RUN,
                        example_id=example_id,
                        field_path=slot.path,
                        message=(
                            f"{slot.key} contains a {len(run)}-digit run, "
                            "which is the shape of a case number"
                        ),
                        detail=chunk,
                    )
                )
    return findings


def scan_rendered_html(
    html: str, table: SubstitutionTable, *, example_id: str
) -> list[DisclosureFinding]:
    """A last look at the rendered page itself.

    Reading, never rewriting: the transform operates on the view model, and a
    page that only looks clean after its HTML has been edited is a page whose
    model was wrong. This exists to catch the case where a template composes
    something the model walk never saw.
    """
    findings: list[DisclosureFinding] = []
    for original in table.searchable_originals():
        if _found(original, html):
            findings.append(
                DisclosureFinding(
                    code=DisclosureCode.RESIDUAL_ORIGINAL_VALUE,
                    example_id=example_id,
                    field_path="<rendered html>",
                    message=(
                        f"the rendered page still contains {original!r} after "
                        "the view model was transformed"
                    ),
                )
            )
    return findings


__all__ = [
    "MULTI_VALUE_SEPARATOR",
    "date_tokens",
    "is_date_shaped",
    "Substitution",
    "SubstitutionTable",
    "TextSlot",
    "scan_rendered_html",
    "scan_residual",
    "walk_cells",
    "walk_models",
    "walk_text_slots",
]
