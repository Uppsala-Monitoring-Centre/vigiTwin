# Migration notes — pinned resolutions and a mergeable form (1.2.6)

Three breaking changes in `vigitwin.review_html.evidence.publication`. The
first invalidates every `FindingResolution` written before this version, by
design — read it before the next secure-side run.

## 1. `FindingResolution` requires `detail_sha256`, and a signature

Two new constraints, both enforced at construction:

- `detail_sha256` is required and must be non-empty. It is
  `digest_text(finding.detail)` for the finding being resolved.
- `approved_by` and `approved_at` must be non-empty. They were required *keys*
  before, but `""` validated — so an unsigned resolution loaded and cleared
  its finding.

**What breaks:** every existing resolutions entry.

```python
FindingResolution(                       # now ValidationError
    code="retained_country_small_count",
    example_id="R1__R2",
    approved_by="reviewer",
    approved_at="2026-01-01",
    rationale="the population is wide enough",
)
```

**Why:** `code`, `example_id` and `field_path` all survive a re-curation that
changes the risk. A signature given when a country sat beside a count of 5
went on clearing the same finding after the count became 2, because all three
keys still matched. `ApprovedText` has pinned its subject with `text_sha256`
since it existed, for exactly this reason; resolutions did not, and the
asymmetry was not deliberate. The signature requirement comes in alongside it
because the generated form (change 3) would otherwise be self-approving.

**What to do:** regenerate the file rather than patching it by hand —
`--export-approvals` now writes the resolution entries with their digests
filled in (change 3), and merges rather than overwriting (change 2), so a
file holding signed *texts* keeps them:

```
… --mode publication --export-approvals secure/approvals.json \
    --approvals secure/approvals.json --side-by-side-dir secure/review/
```

Re-sign the resolution entries it writes back. To update one by hand instead,
the digest is `digest_text(finding.detail)`, and a run whose resolution has
gone stale now prints the value it needs:

```
ADVISORY: resolution pins 9f86d0…; the detail now digests to a3b1c2…
```

## 2. `write_approvals_form` merges instead of raising, and returns a result

```python
path = write_approvals_form(page, out)        # before: -> Path, or FileExistsError
written = write_approvals_form(page, out)     # now: -> ApprovalsFormWrite
written.path                                  # the Path, as before
written.summary()                             # "3 new, 5 signed kept, 1 unsigned refreshed, 0 orphaned"
```

**What breaks:** code using the return value as a path, and code relying on
`FileExistsError` to detect an existing form. `--force` still regenerates from
scratch and now genuinely discards signatures, where before it was simply how
you got a second form at all.

**Why:** findings change as approvals land and as the manifest is re-curated,
so needing a second form is the normal case. Refusing it made the one workflow
the form exists for — sign some, re-run, see what is left — impossible without
`--force`, which threw away the signatures just written.

**What to do:** use `written.path`. The merge rule is that a signed entry is
never touched and an unsigned one is regenerated, so a second run is safe by
default; nothing is dropped, and entries the run no longer asks for are kept
and counted as orphaned.

## 3. The form carries resolution entries, and the CLI writes it twice

`export_approvals_form(page)` now takes an optional `report` and emits one
unsigned `FindingResolution` per blocking finding it raised. Without a report
the `resolutions` list is empty, as before.

In `--mode publication`, `--export-approvals` now writes the form twice: once
before the salt is read, carrying the free-text entries, and once after the
transform, merging in the resolutions. The first write is what keeps the form
obtainable before anyone opens the key store.

**What breaks:** nothing, unless you asserted that the generated form's
`resolutions` list is always empty.

**Two kinds of finding deliberately get no entry.** The free-text codes
(`human_text_review_required`, `narrative_snippet_review_required`,
`curator_text_review_required`, `approval_digest_mismatch`) already have an
`approved_texts` entry, and a resolution would be a second, weaker way past a
field that must be decided on its actual words. Anything reported at
`<rendered html>` is appended after `partition` has run, so a resolution
written against one would never be consulted.

`country_masking` is still never generated: `mask: true` and `mask: false` are
both decisions, so there is no neutral entry to write. For
`retained_country_small_count`, where masking is one of the two remedies, the
generated entry names it in the `rationale` field instead.

## Also new, and not breaking

`resolution_digest_mismatch` is an advisory raised when a resolution is found
by key but no longer pins the finding's detail. The finding it failed to clear
goes back to wherever it would have been unsigned — blocking if it was
blocking — and the advisory carries both digests. It is advisory rather than
blocking because a voided signature withholds nothing on its own, and because
a stale signature on an advisory must not leave a run worse off than never
having signed.
