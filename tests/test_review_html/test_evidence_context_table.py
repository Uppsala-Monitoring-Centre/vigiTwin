"""The context table says which of its own rows the assessment used.

Two paragraphs used to open section 2: one naming the reporting context and
why it applied, one naming the anchor and its size. Both described the table
immediately beneath them, so a reader's work was to match a sentence against
a row. The table now carries both facts as formatting — the context is the
shaded row, the anchor population is the boxed count — and these tests are
about that formatting being present, decodable, and never claimed where it
is absent.
"""

from __future__ import annotations

from llm_dup_dev.data.offline import FrozenPairBundle, FrozenScopeStats
from llm_dup_dev.models.enums import AnchorType, ReportingContextLevel
from llm_dup_dev.models.pair_features import (
    AnchorPopulation,
    ReportingContext,
)

from tests.test_review_html.test_evidence_build import _features
from tests.test_review_html.test_evidence_render import _case, _html


def _bundle(**overrides) -> FrozenPairBundle:
    base = dict(
        pair_id="R001__R002",
        matching_drugs=["topiramate"],
        matching_AEs=["cleft lip"],
        global_stats=FrozenScopeStats(
            N_total=800000, N_drugs=5000, N_AEs=4000, N_drugs_AEs=900
        ),
        country_stats=FrozenScopeStats(
            N_total=80000, N_drugs=500, N_AEs=400, N_drugs_AEs=90
        ),
        country_year_stats=FrozenScopeStats(
            N_total=8000, N_drugs=50, N_AEs=40, N_drugs_AEs=9
        ),
    )
    base.update(overrides)
    return FrozenPairBundle(**base)


def _anchor(anchor_type: AnchorType, n: int) -> AnchorPopulation:
    return AnchorPopulation(
        N_drugs=50,
        N_AEs=40,
        N_drugs_AEs=9,
        type=anchor_type,
        N_anchor=n,
    )


def _rows(**overrides):
    features = _features(
        matching_drugs=["topiramate"], matching_AEs=["cleft lip"], **overrides
    )
    return _case(features=features, frozen_bundle=_bundle()).freq_rows


def _by_label(rows, label: str):
    return next(r for r in rows if r.scope_label == label)


class TestTheScopesAreNamed:
    """A column headed "Country-year" said how, not which."""

    def test_a_country_year_scope_names_the_country_and_the_year(self):
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR,
                country="Canada",
                year=2022,
            )
        )
        assert [r.scope_label for r in rows] == [
            "Global",
            "Canada",
            "Canada, 2022",
        ]

    def test_the_global_scope_keeps_its_name(self):
        """There is no country to name it by, and "Global" is what it is."""
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.GLOBAL
            )
        )
        assert rows[0].scope_label == "Global"

    def test_a_country_scope_leaves_the_year_row_named_by_level(self):
        """Two rows called "SE" would be a table a reader cannot use.

        A country-level context recorded no year, so there is nothing to
        narrow the country by and the country-year row has no name of its own
        to take. The level stands in for it.
        """
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY, country="SE"
            )
        )
        assert [r.scope_label for r in rows] == ["Global", "SE", "Country-year"]

    def test_no_two_rows_share_a_name(self):
        for level in ReportingContextLevel:
            rows = _rows(
                reporting_context=ReportingContext(
                    level=level, country="SE", year=2022
                )
            )
            labels = [r.scope_label for r in rows]
            assert len(set(labels)) == len(labels), (level, labels)

    def test_no_recorded_country_falls_back_to_the_level(self):
        """Inventing a name would be worse than naming the level."""
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.GLOBAL, country=None
            )
        )
        assert [r.scope_label for r in rows] == [
            "Global",
            "Country (all years)",
            "Country-year",
        ]


class TestTheSelectedContextIsMarked:
    def test_the_matching_level_is_the_selected_row(self):
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY, country="Canada"
            )
        )
        assert [r.is_selected for r in rows] == [False, True, False]

    def test_exactly_one_row_is_selected(self):
        for level in ReportingContextLevel:
            rows = _rows(
                reporting_context=ReportingContext(
                    level=level, country="Canada", year=2022
                )
            )
            assert sum(r.is_selected for r in rows) == 1, level

    def test_the_selected_row_carries_the_tint_class(self):
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.GLOBAL
            )
        )
        assert rows[0].row_css_class == "scope-selected"
        assert rows[1].row_css_class == ""


class TestTheAnchorCountIsMarked:
    def test_a_two_set_anchor_boxes_the_both_sets_count(self):
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR,
                country="Canada",
                year=2022,
            ),
            anchor=_anchor(AnchorType.DRUGS_AES, 9),
        )
        selected = _by_label(rows, "Canada, 2022")
        assert selected.anchor_column == "both"
        assert selected.both_css_class == "anchor-count"
        assert selected.drugs_css_class == ""
        assert selected.aes_css_class == ""

    def test_a_drug_anchor_boxes_the_drug_count(self):
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR,
                country="Canada",
                year=2022,
            ),
            anchor=_anchor(AnchorType.DRUGS, 50),
        )
        selected = _by_label(rows, "Canada, 2022")
        assert selected.drugs_css_class == "anchor-count"
        assert selected.both_css_class == ""

    def test_an_ae_anchor_boxes_the_ae_count(self):
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR,
                country="Canada",
                year=2022,
            ),
            anchor=_anchor(AnchorType.AES, 40),
        )
        selected = _by_label(rows, "Canada, 2022")
        assert selected.aes_css_class == "anchor-count"

    def test_an_unselected_row_boxes_nothing(self):
        """The anchor was drawn from one scope, so one row can claim it."""
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR,
                country="Canada",
                year=2022,
            ),
            anchor=_anchor(AnchorType.DRUGS_AES, 9),
        )
        for row in rows:
            if row.is_selected:
                continue
            assert row.anchor_column == ""
            assert row.both_css_class == ""

    def test_the_boxed_count_is_neither_green_nor_red(self):
        """A population size is not evidence for the pair or against it."""
        rows = _rows(
            reporting_context=ReportingContext(
                level=ReportingContextLevel.COUNTRY_YEAR,
                country="Canada",
                year=2022,
            )
        )
        classes = {
            r.drugs_css_class
            for r in rows
        } | {r.both_css_class for r in rows}
        assert classes <= {"", "anchor-count"}


class TestTheRenderedTable:
    def _table(self, html: str) -> str:
        flat = " ".join(html.split())
        start = flat.index('<table class="freq">')
        return flat[start : flat.index("</table>", start)]

    def test_the_paragraphs_above_the_table_are_gone(self):
        html = _html(frozen_bundle=_bundle())
        assert "Rarity is judged within" not in html
        assert "defines an anchor population of" not in html
        assert "weakens the evidence from an identical match" not in html

    def test_the_builder_still_supplies_them_to_consumers(self):
        """Dropping the fields would be a breaking change for no gain."""
        case = _case(frozen_bundle=_bundle())
        assert "defines that context" in case.context_note
        assert "anchor population of" in case.anchor_note

    def test_the_shared_set_heading_no_longer_captions_the_anchor(self):
        """Which set defined the anchor is the panel's Anchor row."""
        html = " ".join(_html(frozen_bundle=_bundle()).split())
        assert "terms) \u2014 defines the anchor" not in html
        assert "<dt>Shared drug set (1 term)</dt>" in html

    def test_the_formatting_reaches_the_html(self):
        html = _html(
            features=_features(
                matching_drugs=["topiramate"],
                matching_AEs=["cleft lip"],
                reporting_context=ReportingContext(
                    level=ReportingContextLevel.COUNTRY_YEAR,
                    country="Canada",
                    year=2022,
                ),
                anchor=_anchor(AnchorType.DRUGS_AES, 9),
            ),
            frozen_bundle=_bundle(),
        )
        table = self._table(html)
        assert '<tr class="scope-selected">' in table
        assert '<td class="anchor-count">9</td>' in table
        assert "Canada, 2022" in table

    def test_the_key_decodes_the_formatting(self):
        """Shading nobody explained is decoration a reader has to guess at."""
        html = " ".join(_html(frozen_bundle=_bundle()).split())
        assert "The shaded row is the reporting context" in html
        assert "the boxed count is the anchor population" in html

    def test_the_key_is_not_claimed_when_no_row_is_marked(self):
        """A bundle missing the selected scope leaves nothing to point at."""
        html = " ".join(
            _html(
                features=_features(
                    matching_drugs=["topiramate"],
                    reporting_context=ReportingContext(
                        level=ReportingContextLevel.COUNTRY_YEAR,
                        country="Canada",
                        year=2022,
                    ),
                ),
                frozen_bundle=_bundle(country_year_stats=None),
            ).split()
        )
        assert "Counts include the two reports under review." in html
        assert "The shaded row" not in html
