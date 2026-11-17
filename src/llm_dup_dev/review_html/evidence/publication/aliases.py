"""Positional aliases for countries and identifiers.

Both are assigned by **first appearance within a case**, which is what makes
them safe and what makes them useful:

* Safe, because the alias is a function of reading order, not of the value.
  There is no key that turns ``Country 1`` back into a country and no
  dictionary attack on a hash, so the publication artifact carries no means
  of reversing itself.
* Useful, because equality survives. Two reports from the same country both
  say ``Country 1``; two from different countries say ``Country 1`` and
  ``Country 2``. The reporting context that selected a population by country
  still names the same country as the reports it was selected for, which is
  the relationship the rarity estimate rests on.

A substitute *real* country name was the alternative, and was rejected: it
asserts something false about a real place, and it needs a sensitive mapping
file to go with every supplement.

**The composite forms.** A country reaches the page in three shapes, and all
three have to agree or a reader can read one against another: a bare code in
the characteristics table, ``SE 2003`` in the context cell
(``build._context_cell``), and ``SE, 2003`` in the frequency table's scope
column (``build._scope_label``). The year in the latter two is a
year-precision date and moves on the pair's own timeline, so the context year
and the report dates beside it cannot drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Imported privately, as `review_html.compare` already does: these are the
# exact strings the builder writes when there is no country to name, and
# re-spelling them here would let the two drift apart.
from ..build import _CONTEXT_LEVEL_FALLBACK, _SCOPE_LABEL

#: Context and scope labels that name a reporting *level* rather than a
#: country. Nothing in them identifies anybody, so they pass through.
NON_COUNTRY_CONTEXT: frozenset[str] = frozenset(
    {"Global", *_SCOPE_LABEL.values(), *_CONTEXT_LEVEL_FALLBACK.values()}
)

#: ``SE 2003`` or ``SE, 2003``: a country followed by a four-digit year.
_SCOPE_RE = re.compile(r"^(?P<country>.+?)(?P<comma>,?)\s+(?P<year>\d{4})$")


@dataclass(frozen=True)
class CountryScope:
    """A country cell's contents, taken apart.

    ``separator`` is kept so the published value is written the way the
    original was: the context cell and the frequency table's scope column
    differ by a comma, and a publication page that normalised them would be
    reporting a layout change as though it were data.
    """

    country: str
    year: int | None = None
    separator: str = " "

    def rebuild(self, country: str, year: int | None) -> str:
        if year is None:
            return country
        return f"{country}{self.separator}{year}"


def parse_country_scope(text: str) -> CountryScope | None:
    """*text* as a country and optional year, or ``None`` if it names neither.

    Returns ``None`` for the level labels in :data:`NON_COUNTRY_CONTEXT` and
    for anything else that does not match a known shape — which is what makes
    an unrecognised composite a finding rather than a silent pass-through.
    """
    value = text.strip()
    if not value or value in NON_COUNTRY_CONTEXT:
        return None
    match = _SCOPE_RE.match(value)
    if match is None:
        return CountryScope(country=value)
    separator = ", " if match.group("comma") else " "
    return CountryScope(
        country=match.group("country").strip(),
        year=int(match.group("year")),
        separator=separator,
    )


@dataclass
class AliasBook:
    """Alias assignment for one case.

    One book per case, so ``Country 1`` in two different cases need not be the
    same country — which is a disclosure property, not an oversight: a reader
    cannot pool values across the supplement's examples.
    """

    country_template: str = "Country {n}"
    identifier_template: str = "Identifier {n}"
    source_template: str = "Source {n}"
    #: The two report aliases, matched to the pair's canonical report order so
    #: they agree with the page's own "Report A" / "Report B" column headings.
    report_labels: tuple[str, str] = ("Report A", "Report B")

    countries: dict[str, str] = field(default_factory=dict)
    identifiers: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    _spare_identifiers: int = 0

    def bind_reports(self, report_ids: tuple[str, str]) -> None:
        """Name the pair's two reports, so their identifiers alias as columns.

        Called with the canonical pair order, so the lower report id — which
        is the one the builder passed as report 1 — becomes "Report A".
        """
        for report_id, label in zip(report_ids, self.report_labels, strict=True):
            if report_id:
                self.identifiers.setdefault(report_id, label)

    def country(self, value: str) -> str:
        """The alias for a country, assigning one on first sight."""
        key = value.strip()
        if key not in self.countries:
            self.countries[key] = self.country_template.format(
                n=len(self.countries) + 1
            )
        return self.countries[key]

    def identifier(self, value: str) -> str:
        """The alias for an identifier.

        A value that is one of the pair's own report ids resolves to that
        report's column label, which is what preserves the explicit-link
        evidence: a report naming its counterpart still names it, as
        "Report B" rather than as a case number.
        """
        key = value.strip()
        if key not in self.identifiers:
            self._spare_identifiers += 1
            self.identifiers[key] = self.identifier_template.format(
                n=self._spare_identifiers
            )
        return self.identifiers[key]


    def source(self, value: str) -> str:
        """The alias for a shared source identifier.

        Its own series, so a reader can tell a masked study from a masked
        report, and stable within the case, so a source named in two places
        is visibly one source. Two reports that share a source therefore
        still visibly share it, which is the relationship the verdict rests
        on.
        """
        key = value.strip()
        if key not in self.sources:
            self.sources[key] = self.source_template.format(
                n=len(self.sources) + 1
            )
        return self.sources[key]


def case_aliases(position: int, template: str) -> str:
    """``"Pair 3"`` — what a published case is cited as."""
    return template.format(n=position)


def case_slug(position: int) -> str:
    """``"pair-03"`` — the published filename stem and section anchor."""
    return f"pair-{position:02d}"


__all__ = [
    "NON_COUNTRY_CONTEXT",
    "AliasBook",
    "CountryScope",
    "case_aliases",
    "case_slug",
    "parse_country_scope",
]
