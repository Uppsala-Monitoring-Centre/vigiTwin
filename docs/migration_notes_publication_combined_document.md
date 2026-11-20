# Migration notes — the combined document, and a signed release (1.2.7)

Three breaking changes in the evidence and publication layers. The first is
also a disclosure advisory: read it before the next secure-side run, and
check what has already been produced.

## 1. `write_publication_evidence` loses three parameters

```python
write_publication_evidence(                 # before
    result, out,
    index_title=page.title,
    collection_intro=page.intro,
    collection_note=page.note,
)
write_publication_evidence(result, out)     # now
```

The title, intro and note come from `result.page` and from nowhere else.

**Why — and this part matters beyond the signature.** The CLI passed the
*pre-transform* page's copies. `scramble_page` works on a deep copy, so
`page.intro` was the curator's original text while `result.page.intro` was
the rewritten one, and `write_publication_evidence` preferred the argument.
So a collection intro or note naming a report date, a country or a case
identifier reached **every published page unrewritten** — and nothing caught
it, because the rendered scan's probe page carried no intro either.

**What to do:** drop the arguments. Then **check any supplement already
produced**: look at the `intro` and `note` of the collection manifests behind
them, and at the top of the published pages, for a value that should have
been transformed. The per-case content was never affected; this is the
collection's own front matter only.

## 2. The output directory gains `combined.html`

Every example in one document, written by default in both modes, named by the
manifest's new `combined_document` field and linked from the index.

**What breaks:** any consumer enumerating the publication directory against
an expected set of filenames. The repo's own inventory test did.

**What to do:** add `combined.html` to the expected set, or pass
`--no-single-document` to keep the old shape. `--single-document PATH` still
writes an extra copy at PATH, so `--no-single-document --single-document
PATH` reproduces the old behaviour exactly.

A case whose `example_id` resolves to `combined.html` or `index.html` is now
refused rather than silently overwritten. The `index.html` half of that was
already broken and nobody had noticed.

## 3. The manifest and key record gain fields, and both forbid extras

`PublicationTransformManifest` gains `combined_document`;
`DisclosureStatus` gains `approved_for_release` and `released_at`;
`ScramblingKeyRecord` gains `candidate_digest` and `release`.

**What breaks:** both models are `extra="forbid"`, so a consumer parsing a
new manifest with a pinned older copy of the model class **rejects the whole
file**.

**What to do:** upgrade the package in step with the artifacts, or read the
manifest as plain JSON. Regenerate committed fixtures with
`.venv/bin/python scripts/generate_publication_schemas.py`.

## Also new, and not breaking

### A signed release replaces the candidate caveat

A `ReleaseApproval` in the approvals file — `released_by`, `released_at`,
`rationale`, `candidate_sha256` — is what makes a published document say
*"Cleared for public sharing by the study's disclosure reviewer"* instead of
*"a candidate for disclosure review, not a document certified as
anonymised"*, in the page footers and in the manifest's statement together.

There is no CLI flag. `--export-approvals` scaffolds the entry with the
digest filled in, so the signer types a name and a date; it is held back
until nothing else is outstanding, and left unsigned it loads as *no
release* rather than as an error.

Two refusals, both raising `NotClearedForRelease`: a **stale pin**, meaning
the document changed after it was read — the refusal names the digest the
current candidate hashes to, and `--export-approvals` resets the release
block to an unsigned skeleton carrying it, so releasing again takes a fresh
name and date — and an **unsigned advisory**, because
`date_pattern_unchanged` is advisory and a release must not call an
unmodified timeline cleared for public sharing. Sign a `FindingResolution`
for each advisory first — that moves it to `report.resolved` — and the
release stands.

Who released the document is recorded in the **key record**. The public
manifest carries only the fact and the date; the name and rationale stay
secure-side.

### Printing

Every example now starts a fresh sheet, the first included, and a print-only
footer repeats on every sheet. See `docs/evidence_format.md` § Printing and
PDF for the browser export settings, and for the one check to do by hand
before the first real supplement.
