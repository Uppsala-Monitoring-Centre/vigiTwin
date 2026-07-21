"""Structured case-level examination (SCLE): forms, packets, and workflow.

The SCLE workflow is a sequence of file-mediated stages:

1. :mod:`~llm_dup_dev.study_eval.scle.form` — the assessment vocabulary and
   the reviewer-facing / auto-populated data models.
2. :mod:`~llm_dup_dev.study_eval.scle.packets` — static per-pair reviewer
   packets, built on the existing ``review_html`` renderer.
3. :mod:`~llm_dup_dev.study_eval.scle.workbook` — XLSX reviewer workbooks and
   the validated read-back of reviewer submissions.
4. :mod:`~llm_dup_dev.study_eval.scle.discrepancy` — structured comparison of
   two independent submissions.
5. :mod:`~llm_dup_dev.study_eval.scle.arbitration` — the arbitration workbook.
6. :mod:`~llm_dup_dev.study_eval.scle.consensus` — the consensus merge that
   preserves the original reviews verbatim.
"""
