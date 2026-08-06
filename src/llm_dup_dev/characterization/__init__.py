"""Descriptive characteristics ("Table 1") for reference sets of report pairs.

Answers "what is actually *in* this reference set?" — reporting years,
countries, patient age and sex, the commonest drugs and adverse events, and how
often the two reports of a pair agree on country, year and sex. It is the step
that runs at data-freeze time, and again before a performance evaluation, so the
sets a study reports on are described rather than assumed.

The package is generic: it takes a
:class:`~llm_dup_dev.study_eval.schemas.ReferenceSet` plus access to the
``Report`` objects it names, and returns an aggregate-only artifact containing no
report content. All the arithmetic lives here on the dev side, testable against
synthetic reports; the secure side supplies the reports through the
``ReportLoader`` protocol it already implements.

Nothing else in ``llm_dup_dev`` imports this package. In particular
``study_eval`` does not: it never handles ``Report`` objects, and this runs
before it as its own command rather than inside it.

Typical use::

    from llm_dup_dev.characterization import (
        build_characterization_report, write_characterization_report,
    )

    report = build_characterization_report(
        [precision_set, *recall_sets], reports_by_id, dataset_id=dataset.dataset_id
    )
    write_characterization_report(validation_set_dir, report)

or from the command line, ``python -m llm_dup_dev.characterization``.

See: docs/reference_set_characteristics.md
"""

from llm_dup_dev.characterization.builder import (
    NO_DRUG_ROLE_REASON,
    NO_HIERARCHY_REASON,
    UNLABELLED_KEY,
    build_characterization_report,
    build_reference_set_characteristics,
    resolution_policy_summary,
)
from llm_dup_dev.characterization.models import (
    SCHEMA_VERSION,
    CategoricalDistribution,
    CategoryCount,
    CharacterizationReport,
    Concordance,
    DataAvailability,
    NumericSummary,
    PairCharacteristics,
    ReferenceSetCharacteristics,
    ReportCharacteristics,
    ResolutionPolicySummary,
)
from llm_dup_dev.characterization.writer import (
    CSV_FILENAME,
    JSON_FILENAME,
    MARKDOWN_FILENAME,
    characterization_to_csv_rows,
    characterization_to_markdown,
    write_characterization_report,
)

__all__ = [
    "SCHEMA_VERSION",
    "CategoryCount",
    "CategoricalDistribution",
    "NumericSummary",
    "Concordance",
    "DataAvailability",
    "ReportCharacteristics",
    "PairCharacteristics",
    "ResolutionPolicySummary",
    "ReferenceSetCharacteristics",
    "CharacterizationReport",
    "NO_DRUG_ROLE_REASON",
    "NO_HIERARCHY_REASON",
    "UNLABELLED_KEY",
    "build_reference_set_characteristics",
    "build_characterization_report",
    "resolution_policy_summary",
    "characterization_to_markdown",
    "characterization_to_csv_rows",
    "write_characterization_report",
    "JSON_FILENAME",
    "MARKDOWN_FILENAME",
    "CSV_FILENAME",
]
