"""Publication mode: the evidence presentation with its values transformed.

The internal evidence review is unchanged and remains the default. This
package adds a second presentation built from the same view model and the
same template, with ages offset, dates shifted, countries and identifiers
aliased, and human-authored text held back for review.

What it does **not** do is reconsider the assessment. It never reads a
:class:`~llm_dup_dev.models.report.Report` or a
:class:`~llm_dup_dev.models.pair_features.PairFeatures`; its input is the
built :class:`~..models.EvidencePageVM`. Every role, contribution, penalty,
contextual frequency and classification on a publication page is the one the
pipeline produced, because this layer has no way to compute a different one.

Typical use::

    from llm_dup_dev.review_html.evidence.publication import (
        PublicationPolicy,
        PublicationSecrets,
        scramble_page,
        write_publication_evidence,
        write_scrambling_key,
        write_side_by_side,
    )

    secrets = PublicationSecrets.from_env()
    result = scramble_page(page, policy=PublicationPolicy(), secrets=secrets)

    # Secure side: the originals, the offsets and the review page.
    write_scrambling_key(result, secure_dir / "scrambling_key.json")
    write_side_by_side(page, result, secure_dir / "review")

    # Refuses while anything is unresolved.
    write_publication_evidence(result, out_dir / "publication")

The two halves are separate calls to separate paths by design: the code that
writes the supplement never holds the mappings, so they cannot end up beside
it.
"""

from __future__ import annotations

from ..models import PresentationMode
from .aliases import AliasBook, CountryScope, parse_country_scope
from .approvals import (
    ApprovalsFormWrite,
    export_approvals_form,
    write_approvals_form,
)
from .dates import (
    DateRole,
    offset_candidates,
    offset_floor,
    offset_tiers,
    parse_partial_date,
    resolution_of,
    shift_partial_date,
)
from .export import (
    MANIFEST_FILENAME,
    REVIEW_FILENAME,
    NotClearedForRelease,
    PublicationBlocked,
    verify_combined_document,
    verify_rendered_output,
    write_publication_evidence,
    write_scrambling_key,
    write_side_by_side,
)
from .findings import (
    DisclosureCode,
    DisclosureFinding,
    DisclosureReport,
)
from .invariants import (
    SEVERITY_BOUNDARY_DAYS,
    DateRelations,
    InvariantFailure,
    InvariantReport,
    check_cross_block,
    check_date_offset,
    date_relations,
    separation_preserved,
    severity_band,
)
from .key import ScramblingKeyRecord, build_key_record
from .manifest import (
    CountryMaskingSummary,
    SourceMaskingSummary,
    PublicationTransformManifest,
    build_publication_manifest,
)
from .numbers import (
    NumericOffset,
    Quantity,
    format_quantity,
    imperial_amount,
    parse_quantity,
    solve_numeric_offset,
)
from .policy import (
    FREE_TEXT_FIELDS,
    KIND_POLICIES,
    NUMERIC_TEXT_FIELDS,
    SALT_ENV_VAR,
    ApprovedText,
    CountryMaskingOverride,
    FindingResolution,
    FreeTextPolicy,
    PublicationApprovals,
    PublicationPolicy,
    PublicationSecrets,
    ReleaseApproval,
    ValuePolicy,
    digest_text,
)
from .scramble import (
    CaseTransform,
    CellOutcome,
    ScrambleResult,
    digest_candidate,
    scramble_page,
)
from .substitute import (
    Substitution,
    SubstitutionTable,
    date_tokens,
    is_date_shaped,
    walk_cells,
)

__all__ = [
    "FREE_TEXT_FIELDS",
    "KIND_POLICIES",
    "MANIFEST_FILENAME",
    "NUMERIC_TEXT_FIELDS",
    "REVIEW_FILENAME",
    "SALT_ENV_VAR",
    "SEVERITY_BOUNDARY_DAYS",
    "AliasBook",
    "ApprovedText",
    "CaseTransform",
    "CellOutcome",
    "CountryMaskingOverride",
    "DateRole",
    "CountryMaskingSummary",
    "CountryScope",
    "DateRelations",
    "DisclosureCode",
    "DisclosureFinding",
    "DisclosureReport",
    "FindingResolution",
    "FreeTextPolicy",
    "InvariantFailure",
    "InvariantReport",
    "NumericOffset",
    "PresentationMode",
    "PublicationApprovals",
    "NotClearedForRelease",
    "PublicationBlocked",
    "PublicationPolicy",
    "PublicationSecrets",
    "ReleaseApproval",
    "PublicationTransformManifest",
    "Quantity",
    "ScrambleResult",
    "ScramblingKeyRecord",
    "SourceMaskingSummary",
    "Substitution",
    "SubstitutionTable",
    "ValuePolicy",
    "build_key_record",
    "build_publication_manifest",
    "check_cross_block",
    "check_date_offset",
    "date_relations",
    "format_quantity",
    "imperial_amount",
    "offset_candidates",
    "offset_floor",
    "offset_tiers",
    "parse_country_scope",
    "parse_partial_date",
    "parse_quantity",
    "date_tokens",
    "is_date_shaped",
    "resolution_of",
    "scramble_page",
    "separation_preserved",
    "severity_band",
    "shift_partial_date",
    "solve_numeric_offset",
    "digest_candidate",
    "digest_text",
    "ApprovalsFormWrite",
    "export_approvals_form",
    "verify_combined_document",
    "verify_rendered_output",
    "walk_cells",
    "write_publication_evidence",
    "write_approvals_form",
    "write_scrambling_key",
    "write_side_by_side",
]
