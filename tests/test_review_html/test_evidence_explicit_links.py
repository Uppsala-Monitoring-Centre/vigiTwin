"""A run that did not use explicit links must not be described as if it had.

A run configured without the feature still writes an ``explicit_link`` field.
The page read that field anyway, and said "neither report carried
case-identifier data, so no explicit inter-report link could be checked" —
blaming the data for a check nobody ran, on a page whose whole point is to
distinguish an assessed absence from an absence of assessment.

``explicit_link_enabled`` is on the run manifest; a dataset in a curated
collection can declare it for runs whose manifest predates it.
"""

from __future__ import annotations

import pytest

from llm_dup_dev.models.explicit_link import (
    ExplicitLinkAssessment,
    ExplicitLinkVerdict,
)
from llm_dup_dev.review_html.evidence.collection import (
    _with_declared_settings,
)
from llm_dup_dev.review_html.evidence.manifest import ExampleDataset
from llm_dup_dev.review_html.evidence.settings import EvidenceRunSettings
from llm_dup_dev.review_html.evidence.summary import build_assessment_summary

from tests.test_review_html.test_evidence_build import (
    _classification,
    _features,
)
from tests.test_review_html.test_evidence_render import _case

_OFF = EvidenceRunSettings(explicit_link_enabled=False)
_ON = EvidenceRunSettings(explicit_link_enabled=True)


def _linked(verdict: ExplicitLinkVerdict) -> ExplicitLinkAssessment:
    return ExplicitLinkAssessment(verdict=verdict)


class TestTheNoteUnderTheAssessment:
    def test_a_disabled_run_says_so(self):
        case = _case(
            features=_features(
                matching_drugs=["topiramate"],
                explicit_link=_linked(ExplicitLinkVerdict.NOT_ASSESSED),
            ),
            run_settings=_OFF,
        )
        assert "did not use explicit inter-report case links" in (
            case.explicit_link_note
        )

    def test_it_does_not_blame_the_data(self):
        """The old wording turned a configuration choice into missing data."""
        case = _case(
            features=_features(
                matching_drugs=["topiramate"],
                explicit_link=_linked(ExplicitLinkVerdict.NOT_ASSESSED),
            ),
            run_settings=_OFF,
        )
        assert "Neither report carried case-identifier data" not in (
            case.explicit_link_note
        )

    def test_it_claims_nothing_about_whether_a_link_exists(self):
        case = _case(
            features=_features(
                matching_drugs=["topiramate"],
                explicit_link=_linked(ExplicitLinkVerdict.NO_LINK),
            ),
            run_settings=_OFF,
        )
        assert "No explicit inter-report case link was found" not in (
            case.explicit_link_note
        )
        # It says what was not done, and nothing about what would have been
        # found. The explicit disclaimer that followed was spelling out the
        # absence of a claim the sentence had not made.
        assert "were not examined" in case.explicit_link_note
        assert "says nothing about" not in case.explicit_link_note

    def test_an_enabled_run_is_unchanged(self):
        case = _case(
            features=_features(
                matching_drugs=["topiramate"],
                explicit_link=_linked(ExplicitLinkVerdict.NO_LINK),
            ),
            run_settings=_ON,
        )
        assert "No explicit inter-report case link was found" in (
            case.explicit_link_note
        )

    def test_an_unrecorded_setting_is_unchanged(self):
        """Not knowing is not the same as knowing it was off."""
        case = _case(
            features=_features(
                matching_drugs=["topiramate"],
                explicit_link=_linked(ExplicitLinkVerdict.NO_LINK),
            ),
        )
        assert "No explicit inter-report case link was found" in (
            case.explicit_link_note
        )


class TestTheGeneratedSummary:
    def _text(self, run_settings) -> str:
        features = _features(
            matching_drugs=["topiramate"],
            explicit_link=_linked(ExplicitLinkVerdict.EXPLICIT_LINK),
        )
        return build_assessment_summary(
            features, _classification(), run_settings=run_settings
        ).as_text()

    def test_a_disabled_run_cites_no_declaration(self):
        assert "names the other in its case-identifier fields" not in (
            self._text(_OFF)
        )

    def test_an_enabled_run_still_does(self):
        assert "names the other in its case-identifier fields" in (
            self._text(_ON)
        )

    def test_an_unrecorded_setting_still_does(self):
        assert "names the other in its case-identifier fields" in (
            self._text(None)
        )


class TestDeclaringItOnTheDataset:
    def _dataset(self, **extra) -> ExampleDataset:
        return ExampleDataset(
            reports="reports.json",
            pairs="pairs.json",
            pair_results="results.json",
            **extra,
        )

    def test_it_fills_what_no_run_manifest_recorded(self):
        settings = _with_declared_settings(
            None, self._dataset(explicit_link_enabled=False), "d"
        )
        assert settings is not None
        assert settings.explicit_link_enabled is False

    def test_it_fills_a_manifest_that_recorded_nothing_for_it(self):
        settings = _with_declared_settings(
            EvidenceRunSettings(epsilon=1e-15),
            self._dataset(explicit_link_enabled=False),
            "d",
        )
        assert settings is not None
        assert settings.explicit_link_enabled is False
        assert settings.epsilon == 1e-15

    def test_agreeing_with_the_manifest_is_fine(self):
        settings = _with_declared_settings(
            _ON, self._dataset(explicit_link_enabled=True), "d"
        )
        assert settings is not None
        assert settings.explicit_link_enabled is True

    def test_contradicting_the_manifest_fails_the_build(self):
        """The manifest is the run's own account; a declaration against it is
        an error in the declaration, not a tie to break quietly."""
        with pytest.raises(ValueError, match="run manifest recorded"):
            _with_declared_settings(
                _ON, self._dataset(explicit_link_enabled=False), "d"
            )

    def test_declaring_nothing_leaves_the_settings_alone(self):
        assert _with_declared_settings(None, self._dataset(), "d") is None
        settings = _with_declared_settings(_ON, self._dataset(), "d")
        assert settings is _ON
