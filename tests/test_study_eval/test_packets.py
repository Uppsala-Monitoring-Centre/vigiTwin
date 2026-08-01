"""Smoke test for static reviewer-packet generation over the review_html tool."""

from __future__ import annotations

from llm_dup_dev.data.offline import ScreeningDetails
from llm_dup_dev.study_eval.scle.packets import (
    PacketContent,
    generate_packets,
    packet_filename,
    packet_targets,
)

from tests.test_study_eval.conftest import build_scle_precision_sample

_SPEC = [("duplicate", "Duplicates"), ("duplicate", "Otherwise_related")]


class _StaticProvider:
    """A PacketContentProvider returning the same two synthetic reports."""

    def __init__(self, report_1, report_2):
        self._content = PacketContent(report_1=report_1, report_2=report_2)

    def get(self, pair_id: str) -> PacketContent:
        return self._content


def test_generate_packets_writes_html(tmp_path, report_a, report_b):
    _, _, sample = build_scle_precision_sample(_SPEC)
    provider = _StaticProvider(report_a, report_b)

    filenames = generate_packets(sample, provider, tmp_path)

    assert set(filenames) == {c.pair_id for c in sample.cases}
    for case in sample.cases:
        expected = tmp_path / packet_filename(case.pair_id)
        assert expected.exists()
        assert filenames[case.pair_id] == expected.name
    assert (tmp_path / "index.html").exists()


def test_generate_packets_surfaces_screening_details(
    tmp_path, report_a, report_b
):
    """Covered pairs show the screening algorithm's basis; others do not."""
    _, _, sample = build_scle_precision_sample(_SPEC)
    covered = sample.cases[0].pair_id
    screening = ScreeningDetails(
        algorithm="vigiMatch",
        entries={covered: {"TotalScore": 6.4237356205981,
                           "IsSuspectedDuplicate": True}},
    )

    generate_packets(
        sample, _StaticProvider(report_a, report_b), tmp_path,
        screening=screening,
    )

    html = (tmp_path / packet_filename(covered)).read_text(encoding="utf-8")
    assert "Screening Algorithm: vigiMatch" in html
    assert "IsSuspectedDuplicate" in html
    assert "6.42" in html

    uncovered = [c.pair_id for c in sample.cases if c.pair_id != covered]
    for pair_id in uncovered:
        other = (tmp_path / packet_filename(pair_id)).read_text(encoding="utf-8")
        assert "Screening Algorithm" not in other


def test_packet_targets_relative_paths():
    _, _, sample = build_scle_precision_sample(_SPEC)
    targets = packet_targets(sample)
    assert set(targets) == {c.pair_id for c in sample.cases}
    for pair_id, target in targets.items():
        assert target == f"packets/{packet_filename(pair_id)}"


def test_packet_targets_custom_dir():
    _, _, sample = build_scle_precision_sample(_SPEC)
    targets = packet_targets(sample, packet_dir="review/html")
    for pair_id, target in targets.items():
        assert target == f"review/html/{packet_filename(pair_id)}"
