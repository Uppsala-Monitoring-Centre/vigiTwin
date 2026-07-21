"""Smoke test for static reviewer-packet generation over the review_html tool."""

from __future__ import annotations

from llm_dup_dev.study_eval.scle.packets import (
    PacketContent,
    generate_packets,
    packet_filename,
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
