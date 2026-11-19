# Changelog

All notable changes to `llm-dup-dev` are recorded here.

**The version is a significance signal, and deliberately not strict
[SemVer](https://semver.org).** It says how far the method has moved, not what
shape of diff produced it — never a per-commit identifier. Exact code
provenance lives in the `dev_git_commit` field of each manifest; this file and
the version number carry the *meaning* of a change. See `agents.md` §
Conventions → Versioning for the full convention.

- **major** — a substantial new generation of vigiTwin. Breaking API or schema
  changes alone do not earn one.
- **minor** — meaningful new functionality or a substantial enhancement. May
  carry backward-incompatible API or schema changes.
- **patch** — fixes, smaller functional improvements, and minor interface or
  schema adjustments, including limited backward-incompatible ones.
- **no bump** — documentation, tests and refactoring. Recorded under
  `[Unreleased]` when a consumer would still notice.

Because the number no longer tracks compatibility, **every incompatible change
is spelled out in its entry**, with a `docs/migration_notes_*.md` wherever a
consumer needs steps rather than a sentence.

Entries from `1.1.0` to `1.2.1` were renumbered on 2026-11-18 when this
convention replaced strict SemVer; the old numbers they shipped under are
mapped in `docs/migration_notes_version_renumbering.md`. Entries below `1.0.0`
follow the pre-1.0 rule, where a breaking change bumped the minor and
everything else the patch.

**A release is not a study.** The version behind a given published analysis is
identified by a git tag and an archived snapshot, not by a round version
number.

## [Unreleased]

### Changed — the version is a significance signal, not strict SemVer

No bump: documentation and convention only, with no functional change. It is
recorded here because it changes how a consumer should *read* every number in
this file.

A major is now reserved for a substantial new generation of vigiTwin; a minor
marks meaningful new functionality or a substantial enhancement; a patch covers
fixes, smaller improvements and minor interface or schema adjustments. Breaking
API or schema changes no longer earn a major on their own, and a minor or a
patch may carry one — so **the version number is no longer the warning that a
break happened.** The entry here is, together with a
`docs/migration_notes_*.md` where updating takes steps.

The versions from 1.1.0 to 1.2.1 were renumbered to match, from the eleven
numbers the old rule had spent on them. Old → new numbers, old → new commit
hashes, and what a consumer has to remap:
`docs/migration_notes_version_renumbering.md`. Nothing published moved — `v1.0.2`
and everything before it, both Zenodo DOIs and `CITATION.cff` are untouched.

One consequence worth stating: the planned package rename no longer requires a
2.0.0, and the README no longer promises one.

## [1.2.5] - 2026-11-19

A hardening pass on the publication layer, taken before the evidence
presentation and its masking go out on the public mirror. Publishing the
algorithm is the right call — the scheme was built with the salt as its only
secret, and most of what an adversary wants is either in the manifest or
implied by the artifact's own semantics — but it changes what a reader can
work out, and two of the three changes here are the answer to that.

A patch: a bug fix, two new checks and a documented threat model, with no new
capability. **It is nonetheless breaking in three ways** — a run that cleared
before may now block, a policy that constructed before may now raise, and the
public manifest loses a field. The number is not the warning; this entry and
`docs/migration_notes_publication_disclosure_checks.md` are. Read the note
before the next secure-side run.

### Fixed — the candidate offset pool was sampled, not searched

`date_offset_candidates` was 2048, documented as exceeding the size of every
band so that the offset search would be exhaustive. The clinical band is 3594
candidates — 30 days to five years, over two signs — so it was **sampled**,
and a sample drops whole-year multiples. Those matter more than their number
suggests: a history mixing year-, month- and day-precision dates is frequently
solvable only at a whole year, because nothing else leaves a bare `2006` in
place against a `2006-12-05`. Measured over twenty seeds the sample lost four
of the ten whole-year offsets on average and eight at worst.

The consequence was a disclosure one. A case left with none of its viable
offsets falls through the below-floor band, which holds no year-shaped offset
either, and lands on zero — publishing the pair's real timeline for want of a
candidate that was in the band and got shuffled out. The number existed to
prevent exactly that, and was set below the band it was protecting.

The default is now 3600, and the guarantee is enforced instead of asserted:
`PublicationPolicy.offset_band_sizes` computes every band the policy produces
and the validator **refuses a cap that would truncate one**, naming the band
and the size it needs. `dates._bounds` now delegates to the policy, so the
arithmetic the validator trusts is the arithmetic the pool uses — the two
disagreeing is what let this ship.

**Breaking:** a policy with `date_offset_candidates` below the largest band it
produces no longer constructs. `PublicationPolicy(date_offset_candidates=2048)`
on the default ranges now raises `ValidationError`. See
`docs/migration_notes_publication_disclosure_checks.md`.

### Added — `date_offset_pool_thin`, for an offset drawn from a small set

Publishing the masking algorithm changes what a reader can work out, and this
reports the part the salt does not cover. The salt chooses *within* the set of
offsets the invariants accept; it does nothing to hide the set. Anyone holding
the source and a published page can run the invariants over the published
dates and recover it, so a case with three survivors narrows the real timeline
to one of three however good the salt is.

The search therefore no longer stops at the winner. It continues until it has
seen `date_offset_min_survivors` survivors — four by default — and a case that
runs out first takes a `date_offset_pool_thin` advisory naming the count. It
stops at the threshold rather than counting the set out, because the question
is only whether the pool is wide enough to be worth anything. The threshold is
deliberately low: almost every real pair is solvable only at whole-year
multiples, of which the clinical band holds ten, so a threshold near ten would
fire on nearly every case and tell a reviewer nothing. Zero switches it off.

The accepted offset is unchanged — still the first candidate in trial order —
so no supplement's dates move because of this.

`CaseKeyRecord` gains `report_offset_survivors` and
`clinical_offset_survivors`, populated only where the set was thin. They are
secure-side, because the number says how much the salt was actually doing for
that case.

### Added — `retained_country_small_count`, which blocks

A scope that names a real country, showing a contextual count at or below
`min_contextual_count` (five by default), now blocks the export. This is the
combination this layer's own documentation has called "the main thing a
disclosure reviewer should be weighing", and until now the only place it was
written down was that paragraph — which is not where a reviewer is looking
when they clear a case. The counts are retained by instruction and the country
is retained by default, so nothing objected to a page showing a real country,
a real calendar year and a co-occurrence count of two. The counts **include
the two reports under review**, so a displayed two is this pair and nobody
else.

It blocks rather than advises because there is a remedy, which is the line
between the two kinds of finding here: mask this one example's country with a
`CountryMaskingOverride`, or sign a `FindingResolution` saying why the
population is wide enough. The finding names both.

The global scope is deliberately not reported — the population a global count
narrows is one the page never names — and § Known limitations now says what
that leaves open, since a global count of two is still two reports worldwide.

**Breaking:** a page that cleared before may now block.
`PublicationPolicy.min_contextual_count = 0` switches the check off. See
`docs/migration_notes_publication_disclosure_checks.md`.

### Added — a threat model for the publication layer

`docs/evidence_publication_mode.md` gains a § Threat model stating the salt as
the only secret, what the scheme resists and what it does not, and the four
readers it is written against — in particular that a reader holding the
comparison database is not defended against *by construction*, because the
interval preservation they would exploit is the property that makes the page
evidence at all. It also says plainly that this is disclosure-risk reduction
applied to data already held to be non-identifying, not anonymisation.

A § For anyone adopting this on their own data names the two defaults tuned
for a large multi-national database — `mask_countries` off and
`min_contextual_count` at five — and why a national centre should revisit
both. The gate does not know how large your database is.

A § Retaining the country collects the country-masking reasoning that was
previously spread between the policy docstrings and the limitations list.

### Changed — the public manifest no longer carries per-class counts

`PublicationTransformManifest.counts` and `TransformCounts` are removed. They
were the one field in the public manifest that gave a reader of the *manifest
alone* something the supplement did not already give them: a reader holding
the pages can see every transformed value and very nearly count them, so the
counts added nothing for them — while for a single-case supplement the
run-level aggregate is that case's own profile, and `date:day → 16` is close
to a fingerprint. A field that is derivable where it is useful and identifying
where it is not is a field worth losing. Exact per-kind counts remain in the
secure `ScramblingKeyRecord.substitutions`.

**Breaking:** `schemas/evidence_publication_manifest.schema.json` loses
`counts`. A consumer reading it should read the key record instead.

### Changed — the review page's advisory block said the wrong thing

It was headed "Advisory — fidelity, not disclosure" and explained that a
relationship the page never states had moved. That is true of
`unasserted_relation_changed` and false of the other three:
`date_pattern_unchanged`, `date_offset_below_floor` and the new
`date_offset_pool_thin` are disclosure judgements, and two of them predate
this change. The block now says which kind is which, and renders each
finding's `message` as well as its `detail` — advisory messages were being
dropped from the page a reviewer works from.

## [1.2.4] - 2026-11-19

### Changed — the assessment summary names the population, not the level

"The shared drug pattern defines an anchor population of two reports in the
selected country context" now reads "...two reports in United States, all
years", and a country-year context reads "...in United States in 2003". The
level said how the population was chosen but not which one it was, and a
count of two means something quite different in one country-year than in
another — which is the whole point of quoting it.

The country is **whatever the run recorded**, verbatim: an ISO code in most
artifacts, a name in some. No code is expanded into a name and no article is
invented, because "the Netherlands" against "Sweden" is a lexical fact about
each name rather than something derivable from the value — the same reason
`characterization.policy.resolve_country` and the context cell already show
it as reported. A country-level context is qualified with "all years", and a
context that recorded no country, or a country-year that recorded no year,
falls back to the old level phrase: vague beats wrong.

This reverses a deliberate decision, documented on the old `context_phrase`,
to keep identities out of the sentences so that a publication export needed
no different ones. That reasoning no longer holds: these sentences are
`SUBSTITUTE` free text, the context cell registers the country with the alias
book, and the substitution table rewrites the sentence and the cell
together — so a masked run now says `Country 1` in both, which a test
asserts in both directions.

`context_phrase` moved from `evidence.build` to `evidence.summary`, where the
sentences are built, and takes a `ReportingContext` rather than a
`ReportingContextLevel`. It stays importable from `evidence.build`, and the
duplicate phrase table the two modules were keeping is gone.

## [1.2.3] - 2026-11-19

Publication mode stops publishing real clinical timelines, and stops asking a
curator to retype the manifest to clear a gate that blocks on all of it.

The date change is the substantial one: clinical dates now move independently
of report dates, which is what makes a floor on the offset satisfiable for a
case with a long history. See `docs/migration_notes_clinical_date_offsets.md`
— the default policy changed, `policy_version` is now `2.0`, and an earlier
run's offsets are no longer reproducible from the same salt. **Also
incompatible for approvals files**, in two small ways named below; no existing
file is affected unless it left a decision or a signature blank.

A patch, not a minor: the masking layer is still being tuned, and a version
here says how far the method has moved rather than how large the diff was.

### Changed — clinical dates take an offset of their own

A report date says when a report reached the centre; a drug start, drug end or
onset says when something happened to a patient, and reports routinely arrive
years after the event. They were tied to one offset, which let the tighter
constraint govern both: a report date is pinned near its reporting-context
year, while a history mixing year-, month- and day-precision dates can
usually only move by whole years. For a real pair carrying sixteen dates
across twelve years, exactly three offsets satisfied the invariants — `-365`,
`0` and `+365` — so the supplement carried the patient's real timeline.
Solved separately, the same pair takes a report offset of a few weeks and a
clinical offset of four years.

The clinical block moves backwards by default, up to five years: a forward
shift is the one direction that can carry an event past the report describing
it. Its ordering against every report date is an invariant — an event that
preceded a report still precedes it, and a displayed equality stays equal —
and a case whose finding states a separation *across* the two blocks keeps
one shared offset, because that figure has to survive exactly.

What the split gives up is the absolute interval between an event and its
report. The footer says so, the manifest records both bands and the key
record both offsets. What it preserves is every interval the adjudication
reads: both reports' clinical dates move together and both reports' report
dates move together, so the difference between the two reports' latencies —
the duplicate signal — survives exactly.

### Changed — the clinical block may move forwards, under a named guard

Restricting the direction was a proxy for the rule that actually matters, so
the rule is now checked instead: every clinical date stays before the report
date of **its own report**. That is enforced per report rather than against
both, because a relationship between one report's event and the other
report's arrival date is not something the page states, and holding offsets
to it rejected them for no reader's benefit. With the guard explicit,
`clinical_offset_backward_only` defaults to off and the pool is twice the
size — which matters for a case that has little room in it.

### Changed — a coarse date may stay put while precise dates move

A year-only value does not move under a shift smaller than a year: thirty
days from 1 January 2006 is still 2006. Treating the resulting drift as a
hard failure meant a history mixing precisions could take *only* whole-year
offsets, because nothing else leaves a bare `2006` in place against a
`2006-12-05`. It is now an advisory, `coarse_date_drift`, under
`allow_coarse_date_drift` (on by default).

An unmoved year is close to no disclosure on its own — it is the dates beside
it, known to the day, that identify, and those do move. Where a finding
states the separation it is still a hard failure, and offsets that avoid the
drift are still preferred: the search takes an advisory-free candidate over
one that raises this, so a whole year wins wherever a whole year is
available.

### Fixed — zero is tried last, and a floor is a preference

Zero is the one offset guaranteed to raise no advisory, since nothing moves,
and the search's first pass accepted only advisory-free offsets. So zero took
every date-rich case by construction — exactly the population whose real
timeline identifies best. `dates.offset_candidates` claimed the search did
not favour it and the limitations note blamed the bias on zero being a larger
share of survivors; both were wrong. Zero is now tried after every other
candidate, with the pool unchanged, so an observer learns nothing new.

Both floors default to 30 days where the report-date floor was 0, and they
are preferences rather than refusals: the search walks bands, strongest
first, so a case whose relationships survive nothing larger than four days is
published four days out with a `date_offset_below_floor` advisory rather than
refused. A floor that refused outright would turn every hard history into
manual handling, and the case that motivated the floor carried sixteen dates.
`date_offset_candidates` rises to 2048 so every band is still searched
exhaustively.

### Added — an approval can point at the text instead of restating it

`ApprovedText` takes `approve_as_written: true` with a `text_sha256`, in place
of a `replacement`. The field publishes what the manifest already holds, and
the digest is what keeps the approval attached to the text it was given for:
edit the manifest afterwards and the case blocks again with
`approval_digest_mismatch`, rather than quietly publishing the approved text
in place of the edited text — which is what a path-keyed approval did.

It is also the safer of the two decisions. A `replacement` is published
verbatim, outside the case's substitution table, because it is the reviewer's
own words and rewriting them would publish something nobody approved. Text
approved as written is derived prose, so it goes through the table: a report
date a curator quoted in a title comes out shifted. Copying that same title
into a `replacement` publishes the real date and leaves the residual scan to
catch it — the habit this entry exists to remove.

The key record and the disclosure review page list these paths separately
from `replaced_paths` (additive, so the key record's `schema_version` stays
`1.0`): the published words are the curator's rather than the reviewer's, and
they are not quite what the manifest says.

### Added — `--export-approvals` writes the form to sign

The CLI turns a blocked run into one `approve_as_written` entry per field that
needs a decision, digests included, written before the salt is read so a
reviewer can be handed the form before anyone opens the key store. It carries
paths and digests and **not** the text: what a reviewer reads is the
side-by-side disclosure review, which prints the originals. The form comes out
*unsigned* and therefore does not validate — a form that loaded as generated
would make the signature a formality.

### Fixed — the rendered scan no longer reads the stylesheet

`scan_rendered_html` now strips `<style>` and `<script>` blocks and HTML
comments before searching. It was reading the whole rendered page, and the
stylesheet carries a comment explaining why a penalty weight needs a space
before its figure: *"2005 1.2" read as one number*. A pair whose year-only
date of 2005 was moved by the offset matched that comment and blocked the
export — a finding about four characters of CSS prose that no reader of the
page ever sees, and an unresolvable one, because the rendered scan runs after
`partition` and a `FindingResolution` cannot reach it.

Attributes are still read, which is what the scan is for: a value composed
into an `id`, a class or a `data-` attribute is exactly the leak the model
walk cannot see. Only the regions that hold no report value by construction
are skipped, and a test asserts that property of the templates rather than
trusting it — every Jinja construct inside such a region must be an
`{% include %}` of a partial that carries no Jinja itself.

### Changed — an approvals entry decides exactly one thing, and is signed

`ApprovedText` now requires exactly one of `replacement`, `withhold` and
`approve_as_written`, and rejects an empty `approved_by` or `approved_at`.
Both were already the intent: the old validator caught a withhold carrying
replacement text, and an entry with nobody's name on it recorded an approval
that cannot be traced to a person. The second is what makes an unsigned
export safe to write.

## [1.2.2] - 2026-11-18

A pass over what the evidence review puts in front of a reader: one thing
moved, one added, and a good deal of prose about what was *not* found taken
out. Presentation only — no schema, no classification result and no public
name moves, and one additive field on `EvidenceCaseVM` — but a consumer that
relied on the page's limitations list should read the last entry.

### Changed — the agreement line sits with the verdicts it compares

"vigiTwin agrees / disagrees with the reference label" moved up one level, to
directly under the row of outcome tiles. It compares two of the four verdicts
that row states; below the decision tiles, where it was, a reader met it a
stage later than the thing it describes and read it as a comment on how
vigiTwin decided.

### Added — a publication page says MASKED at the top

A publication-mode page now opens with a band carrying the word **MASKED**
and one line: *Select displayed values have been transformed.* The footer is
unchanged and still carries the full, policy-derived account of what the run
did, and the caveat that the page is a candidate for disclosure review rather
than a document certified as anonymised — but a footnote does not travel: a
page screenshotted into a slide, cropped into a figure or printed as the
first sheet of a bundle arrives with transformed values and, until now,
nothing on it saying so. The line says *select* values because a transform
may leave a value as reported, and a band claiming more would be publication
metadata a reader cannot check. Internal pages are untouched, and the
side-by-side disclosure review deliberately carries no band: it prints
originals.

### Changed — an absence of evidence is no longer written out as a sentence

The page stops asserting what was not there. Gone: the explicit
inter-report case link note except where a link was actually **found**; "No
additional matching features contributed to the score."; "vigiTwin recorded
no inconsistencies for this pair" (section 3 keeps its *Inconsistencies*
heading and answers `None`, so every case has the same skeleton); and the
sentence saying whether each report carried a narrative, which left the
literature reference it used to precede. Each said in a sentence what the
panel at the top of the case already states as a value a reader can count.

The generated summary follows the same rule: an empty slot is dropped rather
than filled, so "No additional matching evidence was identified" and
"vigiTwin identified no inconsistencies" no longer appear, and the pair that
stayed independent is told only the half that says something — that the
relationship question was not reached, because independence was not rejected.
One consequence for callers:
`AssessmentSummary.paragraphs(split_after=...)` splits on a slot being
present, so a summary with nothing in that slot now comes back as one
paragraph rather than two.

`EvidenceCaseVM.explicit_link_note` is unchanged and still describes all four
states, because the difference between "none found", "nothing to check" and
"this run did not look" is real and a consumer may need it. The new
`EvidenceCaseVM.explicit_link_found` is what the page branches on.

### Removed — the Limitations of this review list

**Worth reading before upgrading a consumer that relied on the page.** The
reader-facing limitations list is gone from the page. Most of what it carried
were plain absences of input — no frozen frequency statistics, no run
manifest — but one entry was not: `score_unreconciled` says the contributions
printed on *that page* do not add up to the score the run stored, and that
warning no longer appears anywhere a reader can see.

Nothing changed in the record. Every gap keeps its code and its message on
`EvidenceCaseVM.gaps`, `EvidencePageVM.gaps` still collects them across a
collection, and `EvidenceGap.is_provenance` still distinguishes a provenance
note from an absence. A consumer that wants a reader warned must read `gaps`
and say so itself.

### Changed — the findings table carries no explanatory note

The paragraph under *Inconsistencies* explained how to read the table's own
two columns: severity as the level finally counted, the figure beside it as
the weight this run applied to that level, the retained weights summing to
the penalty. That is how the format works rather than evidence about this
pair, so it now lives once in `docs/evidence_format.md` instead of under
every table in a supplement. `EvidenceCaseVM.findings_weighed` is still built
and still on the view model; the panel still marks an uncompared penalty
`(not weighed)` and says why, which remains a test.

## [1.2.1] - 2026-11-17

Two reader-facing names on the evidence page. Presentation only: no schema,
no public name and no classification result moves.

### Changed — the relationship outcome is named, not spelled

The decision tile and the assessment table printed `Same_case` and
`Different_case`, which are the classifier's field names. They now read
**Same case** and **Different cases** — plural, because two reports found not
to be one case describe different cases — from one table used by both, so the
page calls one outcome one thing. `RelationshipAssessment` is unchanged and
`final_label` still reads as it always did.

### Changed — a shared source is not assumed to be a publication

`SourceIdentifierType.ARTICLE_REFERENCE` is documented as "a free-text
citation when no more specific type fits", and the page labelled it
*Literature citation* — a specific claim about a source that need not be a
paper at all: a registry entry, a programme, a company's own case series. It
and `UNKNOWN`, the other catch-all, now read **Shared external source** in
the source-context block and "a shared external source" in the generated
summary. The specific kinds — trial registration, DOI, PubMed ID, protocol
number, study name, linked-case reference — are still named specifically.

## [1.2.0] - 2026-11-17

A second presentation of the evidence review, with its displayed values
transformed, and a gate that refuses to write it while anything is
unresolved. The internal review is unchanged and remains the default.

### Added — publication mode

`llm_dup_dev.review_html.evidence.publication` turns a built
`EvidencePageVM` into a publication candidate: dates shifted by one whole-day
offset per pair, ages and measurements offset per unit, countries and report
identifiers replaced by positional aliases, and human-authored or verbatim
narrative text held back for review. `scramble_page` does the transform;
`write_publication_evidence` writes it, or raises `PublicationBlocked`;
`write_side_by_side` writes both presentations plus a disclosure review page
listing every value that changed and every finding.

vigiTwin is not re-run. The input is the view model, so every role,
attribution, contribution, contextual frequency, penalty and classification on
a publication page is the one the pipeline produced.

**Partial dates are the substance of it.** A single offset is exact for
complete dates; a partial date is shifted along the same timeline and written
back at its original precision, so a year-only drug start can sit unchanged
beside an onset that moved by a month. Representability is not the same as
preserved meaning, so each candidate offset is checked against the
relationships the page actually states — display equality, the pipeline's own
match verdict, exact day differences, separations, interval ordering, the
reporting context's year — and rejected if any of them stops holding. The
search is exhaustive over its pool and makes two passes, preferring an offset
that disturbs nothing at all to one that disturbs only what the page does not
state. See `docs/evidence_publication_mode.md`.

**A value may be published as reported, and that is the point.** Nothing
requires a date or an age to move: zero is a candidate offset like any other,
whole-year offsets are candidates, and a published value may equal a value the
pair reported. A transform obliged to change every value tells anyone holding
the policy that the displayed value is *not* the true one, which is a narrower
search than they had before. The outcome is reported instead of prevented — a
case whose dates all held carries a `date_pattern_unchanged` advisory and the
review page says so above its tables. `allow_unchanged_values` restores the
stricter behaviour, and exists so its cost is visible rather than because it
is right.

**A separation has to keep its meaning, not its arithmetic.** Separations of
zero are preserved exactly, as are those at or below `separation_exact_days`
(seven, the whole minor band), so a one-day discrepancy between two onsets
cannot be rounded into something else. Larger ones may move by
`separation_relative_tolerance` — 20% — provided they stay inside the same
severity band, with the bands imported from the pipeline's own clinical-date
thresholds so the transform cannot move a finding into a severity the
assessment never gave it. The figure a finding quotes is then recomputed from
the dates printed beside it, **for presentation only**: severity, weight,
penalty and every count stay exactly as the pipeline produced them, and both
figures are recorded in the key record.

**Countries are shown as reported by default.** `mask_countries` is new on
`PublicationPolicy` and defaults to `false`: a country is part of the
reporting context vigiTwin judged rarity in, and where a reference set was
drawn by country the countries are known from the design, so masking them
costs the reader the population the counts belong to and buys little. A single
example can be decided against the default with a `CountryMaskingOverride` in
the approvals file, and the effective policy is recorded per case in the key
record and summarised in the manifest's `country_masking`. A retained country
is registered as an unchanged mapping rather than skipped, so it appears in the
review page's unchanged table and the residual scan does not report it.

**The country-year a contextual count came from is no longer reattributed.**
The counts are real and published unchanged, and the context label says which
country and calendar year they belong to — so the year in that label is now
frozen rather than shifted with the timeline. Shifting it attributed real
counts to a year they did not come from, which with the country retained is a
claim a reader could check and find false. Because `reporting_context` takes
the year the two reports *share*, an offset that carries either report's date
over a new year is now rejected: the page cannot name a country-year its own
dates do not support. The cost is that a pair reported close to a year boundary
has fewer usable offsets, which the review page's date profile makes visible.

**The residual scan compares values, not substrings.** Dates are extracted as
maximal tokens, so an unchanged `2023` is no longer reported as a leak inside
every `2023-…` date; count, score and weight fields are exempt from
date-shape detection, so a co-occurrence count of 2019 reports is not read as
a year; a bare four-digit token is only a year inside the policy's era
window; and each case is scanned with its own table rather than a merged one,
since one pair's original is routinely another pair's published value.

Two new artifacts, deliberately separate: `ScramblingKeyRecord`
(`schemas/evidence_scrambling_key.schema.json`) holds the offsets, the
mappings, the per-place cell outcomes, the recomputed figures and the count of
dates moved against dates total, and belongs in the secure key store;
`PublicationTransformManifest`
(`schemas/evidence_publication_manifest.schema.json`) travels with the
supplement and records the policy, the offset bounds and per-class counts of
values the transform was **applied to** — not of values that moved, which in
a case where none did would say that every date on the page is the real one.
No offset, no mapping, no original value, no salt. The salt comes from
`VIGITWIN_PUBLICATION_SALT` and has no default.

The disclosure review page keeps the three outcomes apart — transformed and
moved, transformed and unchanged, and failed — and opens each case with how
many of its dates moved, highlighted where the answer is none.

`EvidencePageVM.mode` is new, defaulting to `PresentationMode.INTERNAL`, and
the evidence writers take an optional `mode`. The CLI takes `--mode
publication`, `--policy`, `--approvals`, `--key-out` and
`--side-by-side-dir`.

### Added — the two decision stages, as tiles

Each case now opens with one tile per stage, Independence and Relationship,
carrying the outcome, a badge naming the **pathway** the decision took, the
evidence that drove it, the threshold clause, and — where there is one — a
line naming evidence that was computed and did not decide. Both tiles always
appear, in the same shape, whichever route each took. The outcome tiles above
them are verdicts from four different places and say nothing about how
vigiTwin reached its own.

The pathways are `Shared source`, `Explicit reference`, `Probabilistic
matching`, `Inconsistency assessment`, `Not reached` and `Not recorded`. They
are described from what the run stored and never re-derived, so a tile cannot
reach a different answer from the classifier's. `DecisionTileVM`,
`DecisionStage` and `DecisionPathway` are new on `EvidenceCaseVM.decisions`.

The threshold and tolerance clauses move from the at-a-glance panel into the
tiles, said once rather than twice; the panel keeps the figures.

Tiles are coloured by **which way the decision went**, not by which route it
took: green where the outcome counts towards the reports being one case, red
where it counts against, neutral for an abstention. That is the page's one
colour rule, so a reader who has learned it on the tables does not learn a
second.

`Shared source` and `Individual ids` are separate pathways, because a reader
needs both halves of that route: a shared source is what rejects
independence, and the case labels within it are what the relationship turns
on. A pair sharing one registration and naming different cases within it
reads `Shared source` green on the independence tile and `Individual ids` red
on the relationship tile.

A tile does not comment on evidence that did not decide. The badge says what
the decision rested on, and a line naming the figure that had *not* decided
answered a question the tile never raised — as did the shared clauses'
trailing "not on the matching score" and "with no inconsistency-penalty
comparison", which are written for a paragraph that has just quoted the
figure. The generated summary keeps its own wording.

### Added — source context as structure

`EvidenceCaseVM.source_context` is new: the verdict in the reader's terms,
the shared source identifier as an `EvidenceCell`, and what became of the
case labels *within* that source. Section 2 renders it as a table rather than
as a sentence in passing, which is what gave direct source evidence the
standing of a footnote beside the rarity arithmetic. The identifier is a cell
so the page's colour rule reaches it and a publication transform reaches it
by kind; the prose names no identifier, so it reads the same in both
presentations.

### Changed — a pinned verdict's finding is no longer a weighed inconsistency

Where a definitive source-context verdict settles the relationship, the
classifier pins the outcome whatever the inconsistency penalty comes to. The
verdict also produces an `Inconsistency` row with a severity and a weight,
and the page listed it in the table of weighed findings beside the ones that
were weighed — which says the arithmetic reached the conclusion.

It now appears under its own heading, **Decided on source context**, with the
severity the assessment recorded, no weight, and a note saying it decided on
its own. It is out of the findings table, out of the panel's chips and out of
the generated summary's count of inconsistencies.

**Presentation only.** The finding keeps its severity, its weight, its
retained flag and its place in `discrepancies` and in every stored artifact.
`retained_discrepancies` is unchanged and remains the audit view; the new
`scored_discrepancies` and `deferred_discrepancies` split it, and they differ
only where `source_context_decided` is true.

### Fixed — the panel's penalty no longer contradicts the row above it

`w_tot` is the stored total over every retained finding. With the
source-derived one shown as source-context evidence, the panel read
"Inconsistencies: None" with a penalty of 100.0 underneath — the arithmetic
of a row it had just said was not there. The Penalty row is now the sum of
the findings the panel lists, which for a pinned verdict is 0.0; a pair with a
source verdict *and* an ordinary finding shows the ordinary one's weight, and
a run that recorded no weights falls back to the stored total. The stored
`w_tot` is unchanged.

### Changed — three sentences removed, and one threshold named

The limitations list no longer records a source verdict as evidence "the
comparison tables" could not carry: the Source context block names the
identifier and says what became of the case labels, so the sentence had
stopped being true. The note under **Decided on source context** is gone, as
is the trailing "This says nothing about whether such a link exists." from
the disabled-explicit-links note — a sentence spelling out the absence of a
claim the preceding sentence had not made.

The independence clause now puts the comparison before the conclusion, which
is the order a reader checks it in: "The matching score of 5.1 exceeds the
independence threshold of 1.0 and supports non-independence, falling in the
compelling range."

### Changed — reader-facing wording throughout the source-context path

No stored field name reaches the page. `_source_case_note` printed
`same_source_different_case` with its underscores swapped for spaces, and the
generated summary spliced `trial_registration` into otherwise plain prose as
"(a shared trial_registration)". Verdicts now say which thing differed —
"Shared source, but different cases within it" — and identifier kinds are
named in words. A test asserts that `article_reference` and its siblings do
not appear in the rendered page.

### Added — shared source identifiers are masked in publication mode

`PublicationPolicy.mask_source_identifiers` defaults to `True`, decided
independently of `mask_countries`. A country is a population; a named study
or publication is a small and often public set of patients, so naming one
beside a drug, an event and two dates narrows the field much further. Aliases
go in their own series (`Source 1`), so a reader can tell a masked study from
a masked report and a source named twice is visibly one source — the sharing
is the evidence, and it survives.

Generated prose is covered by construction, because it names no identifier;
human-authored text naming one blocks the export until a reviewer approves a
replacement. `ValueKind.SOURCE_IDENTIFIER` is new, and the manifest records
`source_masking` beside `country_masking`.

### Changed — the publication footer says what the run actually did

It was a fixed sentence naming ages, dates, countries and report identifiers.
With the masking switches configurable that overstated any run that left one
of those as reported, and an overstated footer is the one piece of
publication metadata a reader cannot check. It is now built from the
effective policy and recorded in the manifest as `transform_note`.

### Changed — publication output is a candidate, not a certificate

A publication page's footer states that its values were transformed and that
it is a candidate for disclosure review, in those words, and the public
manifest repeats it. Nothing in this release certifies a page as anonymised.

`EvidenceCell.withheld` — reserved since 1.1.4 and never set — now renders as
`Withheld`, distinct from a `missing` cell, which still renders blank.
Internal pages set it nowhere, so their rendered output is unchanged apart
from the one CSS selector the new state needs.

A matching-feature row that quotes a demographic value now carries that
value's own `ValueKind` rather than `FREE_TEXT`. The kind drives no colouring
and no rendering, so internal pages are byte-identical; without it an age in
that row would reach a publication page untransformed.

## [1.1.8] - 2026-11-16

Each evidence case now opens with the assessment in figures, and red on the
page means something wider than it did.

### Changed — red now means "counted against the pair"

**Read this before comparing a page rendered here with one rendered by
1.1.7 or earlier.** The rule was: green is matching evidence or the anchor
pattern, red is a retained inconsistency, neutral is everything else. It is
now: green is what the assessment counted *for* the pair, red is what it
counted *against*, neutral is what it did not speak to.

Nothing that was red stops being red — a retained inconsistency is still the
commonest thing that counts against. What becomes red is the **anchor
population adjustment**, `-log10(C(N_anchor, 2))`. A larger anchor population
means more report pairs that could have matched by chance, so the term pushes
the matching score down, towards independence. It is not a finding about
anything either report said, but it told against the pair, and rendering it
neutral said it was not part of the argument.

Because a rule that holds in one place and not another is not a rule, the
sign colour is applied wherever a figure in the score arithmetic appears: the
contributions column, its total — red when the total comes out negative,
which is the floor case — and the adjustment in both the new panel and
section 2.

The widening reaches nothing that told neither way. A suppressed finding
contributed exactly zero and stays neutral; so does a term of exactly zero,
which is why a two-report anchor still shows no adjustment row at all.

### Added — the at-a-glance panel

Seven rows above the written summary: reporting context, anchor and anchor
population, the additional matching features with their contributions, the
matching score, the retained inconsistencies, and the inconsistency penalty.
The matching score, the penalty and the same-case tolerance had appeared
**only inside prose sentences** until now; this is the first place a reader
can read them as figures.

Each piece of evidence is a chip carrying its own figure, so both sides of
the assessment read in the same units: `Female +0.3` against `31 vs 30 years
0.4`. Chips take one green and one red and are **never shaded by magnitude** —
colour says which way a piece told, the figure says how much.

A chip carries the values and the weight and **nothing said about them**. The
feature, the severity and the assessment's own sentence are three columns of
the findings table in section 3, so `Adverse events: infection listed on
report A without a matching HLT on report B.` is `infection`, a drug-set
finding is its substances, a clinical-date finding is its two dates, and a
repeated unit is said once — `31 vs 30 years`. The unit is factored out only
where both sides are bare figures without it, so two country names that
happen to end alike are never spliced together.

A finding's weight is **unsigned** here — `0.4`, not `+0.4`: every retained
finding counts against the pair, which its colour says, and a `+` beside a
red figure reads as a contradiction. A contribution keeps its sign, where the
sign is information (the anchor adjustment is the one negative term), and the
findings table keeps the signed form in its column of figures.

The penalty row states **the total**, not `3 × 0.4 = 1.2`. With each term's
own weight on a chip a row above, the multiplication was the same sum twice.

New public model `CaseSummaryVM`, and `EvidenceCaseVM.summary_panel` holding
one (`None` where the stored artifacts support no row). The panel keeps no
copy of the contributions or the findings — it renders the same objects
sections 2 and 3 render, so the two cannot drift and one publication rewrite
of a value reaches both.

No `Classification` row: the outcome tile states that a dozen lines above.

### Changed — the page states the total, not the bounded stored score

A set of contributions implying 17.2 now reads as **17.2** in the panel, in
the contributions column and in the clause beside it, rather than as the 15.0
the stored `L_match` was held at. 1.1.6 stopped the page explaining the
ceiling and the floor on the grounds that neither moves a classification;
this finishes that: a bound nobody explains should not quietly change the
number a reader is shown either.

The two cannot straddle a decision boundary — at the ceiling both sit far
above any independence threshold, at the floor the stored score is zero and
the total is negative — so the quoted figure always agrees with the verdict
beside it. Where the two **genuinely** disagree the page quotes the stored
score instead, because a total that provably does not describe the decision
would let a case print "the matching score of -3.2 … exceeding the
independence threshold of 1.0". `ReconciliationVM.reconciled` is new and
carries that choice.

`independence_clause` and `build_assessment_summary` take a new optional
`score`, so the generated prose quotes the same figure the panel does.

### Fixed — a score disagreement is no longer silent when epsilon is known

`score_unreconciled` fired only when the run had recorded no `epsilon`. A run
that *had* recorded one, whose contributions still failed to account for the
stored score, produced no gap at all. That mattered little while the page
printed the stored score; it matters now that the page prints the total. The
gap fires whenever no bound explains the difference, and names both figures.

### Changed — values, rather than descriptions of values

Three places where the page described a value instead of showing it:

- A contribution is named by its value alone — `Female`, `54 years` — not
  `Sex (female)`. A clinical date never carried its kind, and the column is
  headed "Additional matching feature". Where a run recorded no value, the
  kind is shown by itself as before.
- A finding about a pair of values shows them: `31 years vs 30 years`, not
  "Difference of 1 years." `DiscrepancyVM` gains `left` and `right`, holding
  the *same cells* the characteristics table holds, so a finding cannot come
  to quote a value the table above it no longer shows. A finding with no
  single pair behind it keeps its prose.
- The context row names the population — `SE 2003` — not the level,
  `Country-year`. The country is shown as the run recorded it, usually an ISO
  code; expanding a code into a country name is a mapping this package has no
  business owning. `CaseSummaryVM.context` is a cell, so the publication
  phase masks or substitutes it like any other reported value.

Also public: `format_score` (promoted from `summary._score`, so the panel's
figure and the clause beside it cannot be formatted two ways) and
`sign_css_class`. New `AttributionKind.ANCHOR_MULTIPLICITY`, so the
adjustment's red carries a recorded reason like every other colour.

### Added — the overall adverse-event onset is in the report comparison

The date the case-level onset check compares appeared **nowhere on the page**,
so a finding about it named two values a reader could not check against
anything. It is now a characteristics row, taken from the pipeline's own
`effective_overall_ae_onset_date` rather than re-derived — a row showing a
different date from the one the assessment used would be worse than no row.

Where the pipeline derived it rather than reading it off the report, the cell
says so: the column is headed "Report A", and a derived date is not something
report A stated. New `CharacteristicRow.left_note` / `right_note` carry that;
new public `AE_ONSET_LABEL` names the row. An overall-onset finding now
colours those two cells, which it previously could not reach.

### Changed — event onsets sit under their own term

They had a table of their own, keyed by term, so a reader who wanted to know
when an event started had to match it against a second row — something the
drug table never asked of them. They are now under each report's own term in
the adverse-event table, laid out as a drug's dates are under its substance.
The separate `onsets` table and its note are gone; `AEEvidenceRow.has_onsets`
is kept for callers but no longer selects anything.

### Fixed — a clinical-date finding now shows its dates in red

"Overall adverse event onset date: 2021-04-09 versus 2021-04-08, at least 1
day apart." is an inconsistency about two dates that showed **neither of them
in red**, on a page whose whole visual vocabulary exists to mark exactly that.
The dates were inside a prose string the colour rule could not reach.

`DiscrepancyVM` gains `lead` and `tail` — the words either side of the two
values — so the findings table colours the values and still prints the
sentence. `text` keeps the full prose for callers, and the panel's chip is
unchanged.

### Changed — the shared sets sit side by side

Drugs left, adverse events right, in the column order of the counts table
directly beneath them. Stacked, a reader held the drug set in mind while
scrolling past the AE set to reach the row of numbers that counts both. They
stack again below 650px.

### Fixed — an inconclusive SCLE verdict is styled as a verdict

"Genuinely ambiguous" rendered in the small grey type reserved for "Not
assessed", which reads as though nobody had looked. Somebody did look and
found the reference label genuinely ambiguous, which is a finding.
`OutcomeTileVM.css_class` mutes `NOT_ENTERED` and `NOT_ASSESSED` only.

### Fixed — a run that used no explicit links is no longer described as if it had

A run configured without the feature still writes an `explicit_link` field,
and the page read it anyway: "neither report carried case-identifier data, so
no explicit inter-report link could be checked" — blaming the data for a check
nobody ran, on a page whose point is to tell an assessed absence from an
absence of assessment. The generated summary could likewise cite a
declaration the run never looked at.

`EvidenceRunSettings.explicit_link_enabled` (already read from the run
manifest, already in the footer) is now honoured by both. New
`ExampleDataset.explicit_link_enabled` declares it for runs whose manifest
predates the setting; it only **fills** what the manifest did not record, and
a declaration contradicting the manifest fails the build rather than quietly
winning.

### Changed — the human narrative block is headed "Human interpretation"

Was "Case-level interpretation". The block opposite is headed "vigiTwin
assessment summary", and which of the two a sentence came from is what a
reader needs to know; "case-level" answered a question nobody was asking.

### Fixed — a matched drug with differing dates is no longer a red row

A substance both reports carry, matched as part of the shared pattern, with a
clinical-date inconsistency on one of its records, came out **red**. A row's
tint was the strongest role among its cells, so the date won and the row read
as though the medication were the finding. It is not: the substance matched,
and a date attached to it did not.

A row's tint is now the verdict on the **entity the row is about** — the
substance, the adverse-event term. The inconsistency is not hidden by this; it
is red in the cell it is about, and still listed in section 3. Where the
entity carries no verdict of its own, the strongest role among the row's cells
still decides, so an inconsistency is never left untinted. Same change for the
adverse-event table and its onset dates.

### Fixed — ordinary pairs were being reported as unreconciled

`score_unreconciled` fired on pairs whose arithmetic was perfectly consistent,
in a sentence quoting two figures that print identically: *"totals +14.1,
while the run stored a matching score of 14.1"*.

Two causes, both fixed:

- **The stabiliser is not only a ceiling.** `L_match = -log10(P_match +
  epsilon)`. `epsilon` does not switch on at 15.0 — it inflates the
  denominator continuously as `P_match` approaches it, so a score of 14.1
  against `epsilon = 1e-15` is already about 0.05 below its additive figure,
  past a tolerance built for rounding and nowhere near the ceiling a bound
  test looks for. Where `epsilon` is known the stored score is now
  **recomputed** through the score's own formula and compared, which covers
  the ceiling, the floor and everything between in one expression. Where it is
  not, that arithmetic at `epsilon = 0` bounds the stored score from above,
  and a score below that bound is unverifiable rather than wrong.
  `ceiling_reached` and `floor_reached` stay on `ReconciliationVM` as
  description; they no longer decide whether a difference is reported.
- **One decimal hid the difference it was reporting.** The gap now names both
  figures to enough decimals to tell them apart.

### Changed — a set column with no set is left blank

"How many reports carry every term in this set" has no answer for a set with
no terms; the stored count is the whole scope, which is not a co-occurrence of
anything, and printing it invited a reader to weigh a population size as
evidence. *Both sets* blanks as soon as either side does, being then a
restatement of the one that is left. With neither set shared the table does
not appear.

That is **not** a gap: `frequency_stats_missing` now means the frozen bundle
was absent, and is no longer recorded for a pair that simply shares no terms.

### Added — a reference set's own annotation vocabulary

A reference set annotating `Otherwise related` had that label **silently
dropped**: three terms were built in, anything else became `None`, and the
tile read "Not supplied" — indistinguishable from a pair nobody had annotated.
That is how a whole set's annotations can go unread with nobody noticing.

- `ExampleDataset.reference_label_map` carries a set's own vocabulary mapped
  to the study's binary one, e.g. `{Otherwise related: non_duplicate}`. How a
  set's terms were read in an analysis is a study decision that lives with the
  study; this package cannot infer it, and guessing would be inventing a human
  verdict. Keys fold case, spaces, underscores and hyphens. Targets must be
  `duplicate`, `non_duplicate` or `abstain`, and a key may not redefine a
  built-in term — both rejected at load, so a manifest cannot quietly make
  `duplicate` mean its opposite.
- It sits on the **dataset**, for the same reason `screening_verdict_key`
  does: a reference label is read out of that dataset's `annotations` file,
  and a collection spanning two reference sets may be spanning two
  vocabularies. `CuratedExampleManifest.reference_label_map` is the default
  for datasets that declare none, so a single-set collection still says it
  once. A dataset's mapping **replaces** the default rather than merging with
  it — a set that declares a vocabulary gets exactly the one it declared,
  because a wrongly inherited term is shown as a human verdict where an
  unmapped one is reported as a gap. Resolved with
  `CuratedExampleManifest.reference_label_map_for(dataset_id)`.
- An unmapped term is now **reported**: new gap `reference_label_unreadable`
  names it and points at the manifest field. New `ReferenceInfo.reference_note`
  carries it, mirroring `screening_note`.
- `reference_from_annotation` takes `label_map`. New public
  `normalise_reference_term`, `REFERENCE_VOCABULARY`, `BUILTIN_REFERENCE_TERMS`.
- Folding is applied to the built-in terms too, so an annotation reading
  `Non duplicate` is now read where it previously was not.

Also public: `separating_decimals` (promoted from `summary._separating_decimals`).

### Changed — section 2's context table says what two paragraphs said

Two paragraphs opened the independence section: one naming the reporting
context and why it applied, one naming the anchor and its size. Both described
the table directly beneath them, so a reader's work was to match a sentence
against a row. The table now carries both facts itself:

- scope rows are named for the population they **are** — `Canada, 2022`
  rather than `Country-year`. The level says how the population was chosen,
  not which one it was, and a reader comparing two cases wants the second. The
  country is shown as the run recorded it, usually an ISO code; where no
  country was recorded the level still stands in for it.
- the row the rarity was judged in is **shaded**, and the count that became
  the anchor population is **boxed**. Neither mark is green or red: a
  population size is not evidence for the pair or against it, and spending one
  of the page's two colours on it would make the colour rule mean two things.
- one sentence under the table decodes both marks, and is omitted where no row
  is marked — which happens when the frozen bundle holds no statistics for the
  selected scope.

`FreqRowVM` gains `is_selected`, `anchor_column` and the derived classes
`row_css_class`, `drugs_css_class`, `aes_css_class`, `both_css_class`. The set
list's `— defines the anchor` caption is gone too; the panel's `Anchor` row
says which sets defined it.

`EvidenceCaseVM.context_note` and `.anchor_note` are **retained and still
populated**, for the same reason `assessment_rows` is. One sentence did not
survive the move: that a larger anchor population weakens the evidence from an
identical match. It is method rather than evidence about this pair, and it
lives in `docs/evidence_format.md` and in the adjustment chip's tooltip.

### Changed — the same-case threshold is named with its zone

`the total inconsistency penalty of 2.4 is below the same-case threshold of
4.0` becomes `... of 4.0 for the compelling range`. There is no single
same-case threshold — the tolerance varies with how strongly independence was
rejected — so the old clause let a reader comparing two cases find two
different "the" thresholds with no stated reason for either. A pair whose
independence was decided on direct evidence rather than on rarity has no zone
to name, and none is named. This changes the generated summary text, so a
saved page rendered by 1.1.7 reads differently here.

### Changed — in `both` mode, vigiTwin's own account leads

It belongs with the panel directly above it, which states the same assessment
in figures. A human reading is a response to that assessment, so it follows
rather than standing between the panel and the prose restating it.

### Changed — the SCLE tile is headed "SCLE of reference label"

Was "SCLE assessment of reference label". The tile's value is the assessment;
the word was carrying nothing in a four-across row.

### Removed — the "vigiTwin classification" decision table in section 3

Superseded by the panel, which states both verdicts before the evidence
rather than after it. The three clauses that table carried — the independence
threshold and zone, the penalty against its tolerance, and why no comparison
happened where none did — did **not** go with it: they are rendered beneath
the panel's two decision figures, from the same `independence_clause()` and
`same_case_clause()` builders that wrote the table's rows. Without that, a
case presented with a human interpretation instead of the generated summary
would quote no threshold anywhere on the page, and 1.1.7's removal of the
inconsistency note — justified at the time *by* this table — would have left
the unweighed case unexplained.

`EvidenceCaseVM.assessment_rows` is **retained and still populated**. It is no
longer rendered, but it is a field on a model in the public surface, and
removing it would be a breaking change for the sake of tidiness.

## [1.1.7] - 2026-11-15

Two readability fixes on the rendered page. No logic, no colour rule, no
model changed.

### Changed — the decision table is headed "vigiTwin classification"

It was "vigiTwin assessment", while the outcome tile at the top of the case
and the table's own last row both say *classification*. One name for one
thing.

### Changed — the inconsistency table's note is gone where nothing was weighed

The note under the inconsistencies explained, at length, that the findings
had not been weighed against a same-case tolerance because that comparison is
only reached once independence is rejected. The decision table a few lines
below already says exactly that, on its *Same case* row — for both ways a
penalty goes unweighed, whether independence was not rejected or the
relationship was pinned on source context — so the page said it twice.

Only the weighed note remains. The findings are still shown, still tinted,
and still carry their weights. `findings_weighed` stays, because what it
prevents is the live risk: the "retained and weighed" note appearing on a
pair where nothing was weighed, which would claim the findings decided
something.

## [1.1.6] - 2026-11-15

A second pass over the rendered page, from reading a generated collection.
No assessment logic and no colour rule changed; one quoted number was wrong
and is now right.

### Fixed — the summary quoted the wrong independence threshold

A compelling pair was described as "supports non-independence, exceeding the
compelling threshold of 3.0". Independence is not decided at 3.0.
`decision_logic` rejects independence on `L_match > lmatch_limited` — 1.0 by
default — and *only then* asks which zone the score sits in;
`lmatch_compelling` and `lmatch_intermediate` are not independence
thresholds at all, they select the **same-case** tolerance for a question
that comes later. `EvidenceRunSettings.independence_threshold` returned the
zone's own bound, so every compelling pair was told it had to clear a number
it did not.

It now returns `lmatch_limited` for every rarity reason, and the sentence
reads "exceeding the independence threshold of 1.0 and falling in the
compelling range" — the zone still named, because it is what sets the
tolerance the next sentence quotes, but without a threshold number of its
own. A run that recorded only `lmatch_compelling` now quotes no independence
threshold rather than quoting that one.

### Changed — the sections are named for the two questions

Section 2 "Context and matching evidence" is now **Independence
assessment**, and sections 3 and 4 are merged into **Relationship
assessment**, holding the inconsistencies and then the decision table. The
page now names the two questions vigiTwin is described as answering, instead
of naming the material each section happens to contain.

The reporting-context note moved with them: it explains the population the
rarity is judged against, so it opens the independence section at body size
rather than trailing section 1's comparison table as a footnote.

### Changed — the score arithmetic is a total, not a paragraph

The reconciliation paragraph is replaced by a **Total** row at the foot of
the contributions table, with the anchor adjustment shown as its own row
whenever it is non-zero so the column adds up to the figure beneath it. A
pair with 17.2 decades of contributions now reads `Total +17.2` and nothing
else.

Specifically, the page no longer explains the ceiling or the floor. Neither
moves a classification — every zone boundary sits far below the ceiling, and
a score at the floor is an absence of matching evidence either way — so the
explanation cost a reader more attention than it returned.
`ReconciliationVM.ceiling_reached` and `.floor_reached` remain, because they
are what distinguishes a bound from a genuine disagreement: a Total and a
stored score that differ with no `epsilon` to explain it still raise
`score_unreconciled` under *Limitations of this review*.
`ReconciliationVM.note` is gone.

### Changed — two more explanations off the page

The legend keeps the colour key and the blank-cell key. The caveats that
colour is not severity and that a green term is a set member, and the claim
that nothing depends on hovering, are now in `docs/evidence_format.md`. The
hovering claim in particular was addressed to a reader of a printed page,
who cannot act on it; the property it described still holds and is still
asserted in the renderer tests.

The frequency table's note is reduced to "Counts include the two reports
under review" — the one fact a reader cannot get from the table itself.

## [1.1.5] - 2026-11-15

Examples in a collection are now labelled and placed. Nothing about the
assessment, the colour rule or the stored evidence changed.

### Added — a case says what it is called

`example_id` already did three jobs — filename stem, HTML section anchor, and
the link text in the index — but never appeared on the case itself. A reader
following a reference to "S3", or clicking `S3` in the index, landed on a
section headed only by its title, with nothing on it saying `S3`.

The heading now carries the label: `S3 · Similar exposures, different
outcomes`. A curator's `example_id` is used exactly as given, so the id in
the manifest, the anchor, the filename and the visible label are one string
and cannot drift apart. Where an example gives no id, the collection shows
`Example 3` rather than the invented anchor slug `example-3`.
`EvidenceCaseVM` and `ExamplePresentation` gain `example_label`;
`EvidenceIndexItem` gains it too, so the index and the case agree.

### Added — a case says where it sits

Each case carries `position` and `position_total`, rendered as `3 of 12`
beside the kicker, with `position_text` empty for a case that stands alone
(a lone case is not "1 of 1"). Counted over the cases that actually render,
so an example skipped under `MissingPairPolicy.SKIP` does not leave a reader
hunting for a section that is not in the document.

Deliberately kept apart from the label, and deliberately carried on the case
rather than derived from template loop position. A position describes this
document's current order and is not citable — insert one example and every
later number moves, silently invalidating any text that cited one. And the
batch path writes one case per file, where loop position would make every
case "1 of 1" instead of telling a reader which of twelve they are holding.

### Fixed — an example id that could not be an anchor was accepted

`example_id` was checked for uniqueness and for filename collisions, but
never for shape. `S 1` passed both and rendered `id="S 1"`: a section no
link can reach, in a document whose whole purpose is being cross-referenced,
failing silently at the point someone clicks the index. It is now validated
on load — a leading letter, then letters, digits, `.`, `-` or `_` — with an
error that says what it is used as.

This tightens what a `CuratedExampleManifest` accepts, which is why it is
worth stating plainly: the only inputs it now rejects are those that were
already producing a broken document, so no manifest that worked stops
working. Taken as additive rather than breaking on that basis. `.` stays
legal, which keeps the filename-collision check reachable (`S.1` and `S_1`
still collide).

## [1.1.4] - 2026-11-15

Four refinements from a final read of a generated collection. Structure,
design and the colour rule are unchanged.

### Fixed — the reconciliation claimed agreement where the score hit its floor

The stored score is `max(0, -log10(P_match + epsilon))` with
`P_match = 1 - (1 - p_joint) ** M`, while the two figures the page shows add to
`-log10(p_joint x M)`. The reconciliation clamped its own expectation with
`max(0.0, ...)` before comparing, so a pair whose figures came to a negative
number and whose score was zero was reported as agreeing — the page printed
"contributions total +0.0; anchor adjustment -1.7; stored matching score 0.0.
The contributions, the anchor adjustment and the score agree to the precision
shown", which is visibly not so.

The floor is now explained as the ceiling already was. When the additive figure
is negative and the score sits at zero, the page states the figure, says the
score is held at its lower bound, and says what that means: as `p_joint x M`
approaches one a chance match stops being unlikely, `P_match` saturates towards
one, and a score of `0.0` reports *absent* matching evidence rather than a
computation that did not run. Unlike the ceiling this needs no run manifest —
zero is not a function of epsilon. A negative sum with a non-zero score is
still a genuine disagreement and still raises `score_unreconciled`.

The row now also shows the scale, `0.0–15.0`, where epsilon is known, so both
bounds are visible rather than only the upper one. `ReconciliationVM` gains
`additive_total`, `additive_total_text` and `floor_reached`.

### Fixed — "retained and weighed" where nothing was weighed

Section 3's note was unconditional, so an `Unrelated` pair — independence not
rejected, so the relationship question never reached — was told its findings
had been "retained and weighed" and that their weights "sum to the
inconsistency penalty". The note is now conditional on
`InconsistencyPenalty.threshold is not None`, carried on the case as
`findings_weighed`. Where no comparison happened it says the findings were not
weighed against a same-case tolerance, that the comparison is only reached once
independence is rejected, and that they bore on no decision for this pair. The
weights are still shown: the run assigns them, and what did not happen is the
weighing, not the assignment.

### Fixed — "The shared drug and adverse event sets defines"

The two-set anchor's name is plural and took a singular verb. It now reads "the
shared drug and adverse event sets **together define** an anchor population of
…"; the one-set anchors are unchanged. The anchor name table carries the verb
beside the name so the two cannot drift apart again.

### Added — the footer states what produced the page

The rendering version always, read from installed distribution metadata — the
same source `build_run_manifest` uses for `dev_package.version`. From the run
manifest when one was supplied: the run id and date, the dev commit behind the
run, and the decision settings it used (`L_match` thresholds, `L_patient`,
`epsilon`, whether explicit links were enabled), each only if recorded. A
collection spanning several runs says "several pipeline runs; see each case"
rather than attributing the page to whichever came first.

`EvidenceRunSettings` gains `run_id`, `created_at`, `dev_package_version`,
`dev_git_commit` and `configuration_text`; `page_provenance` and `ProvenanceVM`
are new and exported; the three evidence writers take an optional
`provenance=`.

### Changed — nothing on the page depends on a tooltip

The legend now says so, which is true and was worth stating for a printed copy:
every coloured value's tooltip repeats evidence that the tables also name — a
green demographic appears in the contributions table with its value, a green
date likewise, a shared-set member in the set list, and every red cell in
section 3. No print rule hides content, and a test asserts both.

### Public surface

Additive: `ProvenanceVM` and `page_provenance` exported from
`llm_dup_dev.review_html`; `EvidencePageVM.provenance`;
`EvidenceCaseVM.findings_weighed`; three `ReconciliationVM` fields; five
`EvidenceRunSettings` fields; `provenance=` on `write_evidence_html`,
`write_evidence_batch_html` and `write_evidence_document`. No schema changed
and no existing name moved.

## [1.1.3] - 2026-11-15

A compaction pass on the rendered page, from reading the first secure-side
output end to end. No assessment logic, no colour rule and no stored evidence
changed; the page says less about itself.

### Changed — the page explains itself in three lines instead of three paragraphs

The colour key, the drug table's ordering note and the adverse-event table's
ordering note together ran to about 250 words on every case, repeated for every
example in a collection. They are gone from the template. What survives is what
a reader cannot get anywhere else:

- the colour key itself;
- the two warnings that stop a misreading — colour shows what the assessment
  used rather than how severe it was, and a green drug or AE term is a member
  of a shared pattern rather than a term with its own score;
- the one glyph key that is not self-evident: a blank cell means the
  information was not reported.

The ordering rules, the treatment-period dash, the grey strengths and the
mirrored one-sided AE groups are all still true and all still documented — in
`docs/evidence_format.md`, which is where a reader who wants the rationale
should be sent, rather than on every page of a supplement.

### Changed — "Limitations" lists what is absent, not how evidence was obtained

The two gaps added in 1.1.2 say a date or a substance was read out of the run's
own description. That is provenance for something the tables *do* show, and it
cost a reader two paragraphs about a value sitting in front of them. Both are
now `is_provenance` gaps: still on `case.gaps`, still assertable in a test,
still in the record — simply not printed. The limitations list keeps the gaps
that name something missing.

### Changed — retained inconsistency rows are tinted red

Section 3's retained findings carry `row-mismatch`, the same row class the drug
and adverse-event tables use, so one visual language covers the page. Superseded
findings stay untinted: they contributed nothing, and a red row would say the
assessment weighed them.

### Changed — an `Unrelated` pair says why no same-case comparison was made

"Two minor adverse event inconsistencies were identified, but no same-case
decision was reached" reads as though the relationship assessment ran and
stalled. It never ran: the question is only reached once independence is
rejected. The sentence now says so, in both the prose summary and
`same_case_clause`:

> Two minor adverse event inconsistencies were identified, but not weighed
> against the same-case tolerance: that comparison is only reached once
> independence is rejected.

and, with nothing recorded, "No inconsistencies were identified, and the
relationship question was not reached, because independence was not rejected."
The assessment table's *Same case* row already said this ("Not assessed: the
relationship question is only reached once independence has been rejected");
the prose now agrees with it.

### Public surface

Unchanged. The two new properties — `EvidenceGap.is_provenance` and
`DiscrepancyVM.row_css_class` — sit on models that are **not** in
`docs/integration_guide.md` § Public surface and not in
`review_html.__all__`, so they are internal by this repo's own rule.
Everything a consumer can observe here is rendered text: the page says less
about itself. That is a refinement of a presentation shipped two patches
earlier, which is what makes it a patch.

A collection's own `note` is unaffected — that text is the manifest author's,
not the template's.

## [1.1.2] - 2026-11-15

Refinements from the first secure-side run of the evidence format. Nothing in
the assessment logic changed; what changed is how much of a stored run the
review can show.

### Fixed — a curated collection always said "Not evaluated" for screening

`build_collection` passed `screening_retrieved=None` unconditionally, so the
screening tile read "Not evaluated" on every curated review even when the
manifest named a screening artifact that covered the pair. Nothing anywhere
derived the verdict from the loaded entry: the CLI's evidence path did not pass
the entry at all, and `evidence_case_from_result` did not look at it.

The verdict is now read from the entry, by all three paths, with
`screening_verdict` in the new `review_html.evidence.screening` module. Because
a `ScreeningDetails` entry is untyped at the feature level, it is read from a
**named boolean** — `IsSuspectedDuplicate`, `screening_retrieved`, `retrieved`
or `flagged`, or whatever `screening_verdict_key` names, on the manifest's
dataset or as `--screening-verdict-key` — and never from an entry merely
existing. An export may carry a row per pair *considered*, verdict included, in
which case a present entry can mean "not flagged"; treating presence as the
verdict would read that export backwards. A key holding something other than
`true` or `false` is reported as a gap rather than coerced, and a pair with no
readable verdict reads "Not evaluated", never "Not flagged".

### Added — older runs now highlight their drugs and their dates

`Inconsistency.terms` / `.kind` and `FeatureContribution.matched_value` arrived
in 1.1.0, after runs had already been saved, and a run without them showed a
drug-set finding with no substance highlighted and matched clinical dates with
nothing green. Those runs are not short of the information, only of the field:
the drug-set check writes the offending substances into its own `description`
and each clinical-date contribution writes its matched date into its own, and
both have done so since well before 1.0.0.

Both are now recovered from there (`review_html.evidence.legacy`), so an older
run highlights its dates wherever they appear, names the matched date in the
contributions table, and leads its drug-set finding with the substance — the
same output a current run produces. Each recovery is footnoted as recovered
(`drug_set_terms_recovered`, `clinical_date_value_recovered`), and a
description that is not exactly its producer's format yields nothing and keeps
today's `_missing` gap rather than a guess.

Recomputing the finding from the two reports was considered and rejected: it is
a comparison performed by the renderer, which this format's colour rule
forbids, and it can disagree with what the run actually flagged. Recovering the
producer's own words cannot. What an older run still cannot supply is which
date *field* a match came from — dates are recorded per value — so a report
carrying one date in two fields shows both, and the footnote says so.

### Added — every matching contribution names the value that earned it

A contribution row said "Clinical date" or "Sex"; it now reads the matched date
itself, and `Sex (male)`, `Age (54 years)`, `Weight (80 kg)`. The demographic
value is taken from the characteristics table, so the row quotes exactly the
string the reader sees above it, units included, and works on every run
including ones saved before any of these fields existed. Where the two reports
hold different values for a feature the run still scored as matched — an age
within tolerance — both are shown, because neither is "the" matched value.

### Added — inconsistency rows carry their weight

Severity now reads `Moderate (+1.0)`: what the finding added to the
inconsistency penalty. The weight comes from the stored
`InconsistencyPenalty.terms`, which records the weight the run *actually
applied*, so a run with custom weights is described with its own numbers and
one that saved no classification shows severity alone rather than a default
that may not have applied. A superseded finding reads `+0.0`, which is what it
contributed. The retained findings' weights are what sum to the penalty.

### Confirmed

Drug and adverse-event findings both lead with the term — `decreased appetite
listed on report B without a matching HLT on report A`, and `aspirin — key drug
on report A absent from report B`. This was already true of any finding
carrying structured `terms`; with the recovery above it is now true of older
runs too, which previously fell back to the audit-trail description and its
embedded Python list.

### Public surface

Additive: `screening_verdict` and `ScreeningVerdict` are exported from
`llm_dup_dev.review_html`; `evidence_case_from_result` and the collection
manifest's dataset block gain `screening_verdict_key`; `DiscrepancyVM` gains
`weight` and `weight_text`; `ReferenceInfo` gains `screening_note`; three gap
codes are added. No schema changed and no existing name moved.

## [1.1.1] - 2026-11-15

### Changed — `bundles` is now `pair_frequencies` across the review interfaces

The artifact the secure side writes is `pair_frequencies.json`. The review
interfaces called it `bundles`, after the model inside it, so every invocation
read `--bundles pair_frequencies.json`. The names now say what the file is:

| Was | Is |
|---|---|
| `--bundles` | `--pair-frequencies` |
| `load_saved_run(bundles_path=...)` | `load_saved_run(pair_frequencies_path=...)` |
| `render_saved_run(bundles_path=...)` | `render_saved_run(pair_frequencies_path=...)` |
| curated manifest `bundles:` | `pair_frequencies:` |
| `bundles.json` in docs and examples | `pair_frequencies.json` |

**No aliases.** A stale `--bundles` is an argparse error, a stale
`bundles_path=` a `TypeError`, and a stale `bundles:` in a collection manifest
a validation error naming the key — the manifest models are `extra="forbid"`,
which is what makes the last one loud rather than silently ignored. All three
fail at the call, not by rendering something subtly wrong.

**What did not change.** `FrozenPairBundle`, its JSON structure, and
`FrozenStatsProvider` are untouched: they are the established offline
evaluation infrastructure, consumed on the secure side, and the model is still
accurately named after what it holds. Nor did the per-pair parameter change —
`render_pair(frozen_bundle=...)`, `PairRenderItem.frozen_bundle`,
`EvaluatedPairItem.frozen_bundle` and `build_evidence_case(frozen_bundle=...)`
all name one `FrozenPairBundle`, not the artifact. The rename is confined to
the names that denote the *file*: its CLI flag, its loader parameter, and its
manifest field.

No assessment logic, rendering output or schema moved. `schemas/` is
byte-identical and no page renders differently.

**On the version number.** This is a naming adjustment, so it takes a
**patch** — the level that admits limited backward-incompatible changes. The
three names that moved have no consumer: the secure side does not call
`--bundles` or `bundles_path`, and the evidence format that introduced the
manifest field has reached neither GitHub nor any consumer. What makes the
break safe is not the number but this entry and the table above it, which is
the whole point of sizing a version by significance rather than by how many
names moved.

*Shipped as 1.2.0 under the old strict-SemVer rule, where it had to be argued
as a deliberate exception to a major bump. Renumbered 2026-11-18; see
`docs/migration_notes_version_renumbering.md`.*

### Documentation

- Every doc now uses the current secure-side artifact convention —
  `reports.json`, `pairs.json`, `pair_frequencies.json`,
  `narrative_demographics.json` — dropping the `validation_` prefix left over
  from the method-validation phase. Besides `docs/integration_guide.md` and
  `docs/evidence_format.md`, this corrects `docs/study_eval.md` and
  `docs/reference_set_characteristics.md`. Directory and identifier names are
  not artifact filenames and keep the vocabulary the repo still uses elsewhere
  (`validation_sets/`, `vs_2026Q1`, the per-validation-set
  `dataset_manifest`), as does the historical record in
  `docs/migration_notes_canonical_pair_identity.md`.
- The integration guide's JSON-format table gains the `pair_frequencies.json`
  row it was missing, so the artifact is documented where the others are rather
  than only in the CLI's own `--help`.

## [1.1.0] - 2026-11-15

### Added — an `evidence` review format beside the existing `detailed` one

The existing pair review is built for annotation and debugging: dense, with
raw JSON panels and internal feature identifiers, and coloured by comparing
the two reports' displayed values. The new **evidence** format is for
communicating vigiTwin's reasoning to an external reader.

**Colour follows the assessment, not the diff.** The evidence builder performs
no comparison of its own. Green means the stored assessment scored a value as
matching evidence or named it in the anchor pattern; red means a *retained*
inconsistency named it; neutral is everything else — including agreements that
were never scored, and differences vigiTwin did not record as inconsistencies.
So a drug present in only one report is neutral unless a finding names it, two
identical report dates are neutral (report date is excluded from the
probability computation), and a retained inconsistency stays red on a pair
classified `Unrelated`, where no same-case decision was reached.

- `review_html.evidence` — `build_evidence_case`, the view models, a curated
  example manifest, and a collection builder.
- `build_assessment_summary` — a deterministic, readable account of one pair's
  assessment. No LLM, no I/O, same inputs always the same text. It quotes a
  threshold only when a run manifest supplies it, names the decision pathway
  actually recorded rather than assuming rarity, says explicitly when no
  inconsistency-penalty comparison was made, and never presents a chance-match
  probability as a probability of duplication.
- **Pair selection**: `load_saved_run(select=...)` takes a `PairSelection` or a
  bare iterable of pair ids, applied before any per-pair work. `select=None`
  behaves exactly as before. An id matching no pair raises regardless of
  `strict`; a *selected* pair that cannot be loaded follows the selection's own
  `on_missing` policy and raises by default, because silently dropping a pair
  someone named leaves a curated collection quietly short of an example.
- **CLI**: `--format {detailed,evidence}` (default `detailed`), `--pair-ids`,
  `--run-manifest`, `--set-label`; `--output-dir` now defaults per format —
  `outputs/review_html/` for detailed, unchanged, and
  `outputs/evidence_review/` for evidence, because both formats write an
  `index.html` and name a pair's page the same way; and
  `python -m llm_dup_dev.review_html.evidence --manifest ...` for a curated
  collection, with `--export-drafts` for editable summary drafts.
- `FeatureContribution.matched_value` / `.value_resolution` — the matched value
  for date-valued features, so a display can attribute a clinical-date match to
  a specific date field instead of parsing it out of `description`.
- Drug-set inconsistencies now populate the existing `terms` and `kind` fields
  with the offending substances and the direction.
- `docs/evidence_format.md` — the format's own reference: the colour rule and
  its consequences, suppression against downgrading, row ordering, the
  assessment summary, the score reconciliation, pair selection, the curated
  manifest, and every gap code. `docs/integration_guide.md` § Public surface
  now lists the new names, which is what makes them contract.

**The detailed format is unchanged, byte for byte.** `render_pair`,
`write_pair_html`, `write_batch_html` and `_safe_filename` keep their
signatures and their output, verified by diffing a rendered page against the
previous release; `study_eval.scle.packets`, which reimplements that filename
rule, is untouched. The two formats write into separate directories.

**Older features files still load and render**, with two documented gaps shown
as page footnotes rather than silently omitted: no per-substance drug-set
highlighting, and no per-date clinical-date highlighting. Both are absences of
recorded detail, not errors.

**Run settings are optional and never assumed.** `epsilon` lives on
`PipelineConfig` and the `L_match` zone boundaries on `DecisionThresholds`;
neither is in a saved run's features file, only in a `RunManifest`. Supplied
one, the review quotes the run's own numbers and can state the score ceiling —
which is what explains a pair whose contributions total more than its stored
score. Without one it names the evidence zone and quotes no threshold, rather
than describing a run with defaults that may never have applied to it.

**This is an internal review, not a publication-safe one.** It may carry
original identifiers, country names and narratives, and says so in its footer.
Displayed values are confined to a single `EvidenceCell.text` field, with
attribution carrying only assessment-level facts, so a later publication phase
can rewrite every displayed value without disturbing a colour — pinned by a
test that shifts every date by a shared offset and asserts the evidence tree is
unchanged.


## [1.0.2] - 2026-11-14

### Fixed — `dev_git_commit` could name the caller's repository

`study_eval.manifest.build_manifest` read git state from
`Path.cwd()` when no `repo_path` was given. A study-eval stage is normally
driven from the consumer's repository, so every manifest it wrote recorded the
**consumer's** commit in a field documented as "which precise dev code ran".
The failure was silent: the sha is well-formed, present, and wrong.

Reported from the secure side, whose own overarching finalization manifest
already resolved this correctly and so was unaffected.

- **Provenance now resolves from the package's own source**, not the working
  directory, in all four manifest builders (`run_manifest`, `dataset_manifest`,
  `characterization`, `study_eval`). Passing `repo_path` / `dev_repo_path`
  explicitly still overrides it — a caller naming a checkout means it.
- **Enclosure is no longer mistaken for provenance.** The three builders that
  already resolved from the package source were still wrong whenever the
  package was installed as a wheel rather than editable: `git` searches
  upwards, and a venv normally sits *inside* the consumer's checkout, so the
  lookup returned the consumer's sha. `try_dev_git_commit` now requires that
  git actually **tracks** the package's `__init__.py`, and returns `None`
  otherwise. For an installed wheel `dev_package.version` is the identity, and
  `None` states the absence honestly rather than inventing a sha.
- **`run_manifest._git` gains `dev_repo_path` and `try_dev_git_commit`**,
  replacing three identical private copies of the former. `try_git_commit` is
  unchanged and still answers the enclosure question, which remains the right
  answer for an explicitly supplied path.

No schema changed and no public name moved; `dev_git_commit`'s documented
meaning is what it always was. What changed is that the value now matches it.

### Infrastructure

- CI pins `runs-on: ubuntu-24.04` instead of `ubuntu-latest`, which began
  migrating to Ubuntu 26 on 2026-10-19. No effect on the package; recorded so
  that a CI result cited alongside a published analysis names the image it ran
  on.

## [1.0.1] - 2026-11-13

### `ruff` and `mypy` now block, at zero findings

1.0.0 shipped with both checks advisory, carrying 36 ruff findings and 33 mypy
errors inherited from before the config existed. Clearing them required
touching `src/`, which 1.0.0 deliberately did not do. They are cleared here and
both checks are blocking in the same change: an advisory check nobody has to
satisfy stops being a check.

Nothing in the schemas or the public surface moved — `git diff v1.0.0..v1.0.1
-- schemas/` is empty, and regenerating every schema from the models produces
no diff. The patch bump is for the behaviour fixes below.

### Fixed

- **`_parse_date_string` carried an unresolvable return annotation.**
  `probability.py` annotated `-> "date | None"` while importing `date` only
  inside the function body under a different name, so the module-level name
  never existed. `from __future__ import annotations` kept it from failing at
  runtime, but anything resolving the annotation — `typing.get_type_hints`, a
  type checker, a doc generator — would raise `NameError`. `date` is now
  imported at module level and the local re-imports are gone.
- **Seven `zip()` calls could truncate silently.** Each pairs sequences that
  are equal-length by construction (or asserted equal immediately above), so
  all seven now pass `strict=True`. A length mismatch raises instead of
  producing a short, wrong result. This affects
  `eval.metrics.confusion_matrix`, whose docstring already required "two
  aligned sequences": mismatched input is now an error rather than a quietly
  truncated matrix.
- **Two stale install instructions named an extra that no longer exists.** The
  `study` extra was removed when `openpyxl` became a core dependency, but
  `study_eval.cli`'s module docstring and the `ImportError` raised by
  `scle.workbook` both still told the reader to run
  `pip install 'llm-dup-dev[study]'`, which cannot succeed. Both now say
  openpyxl is core.
- **`_z_for_level` documented its accuracy five orders of magnitude too
  loosely** — "good to ~1e-4" against a measured worst-case relative error of
  1.1e-9 versus `statistics.NormalDist.inv_cdf`. It also carried an unreachable
  lower-tail branch: `p = 1 - (1 - level)/2` is at least 0.5 for every level in
  `(0, 1)`, while that branch needed `p < 0.02425`. The docstring is corrected
  and the dead branch removed, with a comment recording why only two of
  Acklam's three regions are reachable.
- **`review_html.cli` crashed with `AttributeError` on a bundles artifact that
  was a JSON array** rather than an object keyed by `pair_id`. It now raises
  `ValueError` naming the file and the type it found.
- **A test built a list of decisive findings and discarded it**, asserting only
  the aggregate count. It now also asserts the list length matches
  `n_decisive`, which is what the comment above it described.

### Changed

- `review_html.saved_run` builds its `PairResult` in the branch that loads the
  inputs, rather than in a second branch keyed off the same `source` string.
  Same behaviour, with `result`, `features` and `classification` all provably
  non-`None` at the point of use.
- `read_arbitration`'s two cell readers were closures over the loop variable
  `row`. Both were only ever called within the same iteration, so this was
  benign, but it is the shape that breaks the first time a caller defers the
  call. They are now module-level helpers taking `row` explicitly.
- `_check_artifact_keys_canonical` accepts `path=None`. The path is only
  interpolated into an error message, and callers legitimately hold `None` for
  an artifact that was not supplied.
- Four `l`/`r` locals in `review_html.compare` renamed (`l` is unreadable next
  to `1`), and `_scope` is typed `FrozenScopeStats` instead of `object` with a
  `type: ignore` naming the wrong error code.
- `actions/checkout` and `actions/setup-python` bumped v4/v5 → v7, off the
  deprecated Node 20 runtime.

### Documentation

- `CITATION.cff` carries the Zenodo concept DOI `10.5281/zenodo.24198670`, and
  the README has the matching badge and guidance on concept vs. version DOIs.
  Added after the v1.0.0 release, because Zenodo mints a DOI only once a GitHub
  Release exists — which is why these two files differ between the `v1.0.0` tag
  and `main` with no code change between them.
- `agents.md` § Checks rewritten: all three checks block, zero is the baseline,
  and a suppression needs a named code plus a reason.

## [1.0.0] - 2026-11-13

### The schema and API contract is now declared stable

**No code changed.** `git diff v0.9.3..v1.0.0 -- src/` is empty: this release is
the 0.9.3 tree with packaging metadata and documentation brought up to the
standard a stable, publicly installable release needs.

1.0.0 is cut because a full study cycle — the four `study_eval` steps of the
performance evaluation — completed without the contract moving underneath it.
That is the evidence the contract is stable; the version number now says so.

**Provenance of the performance-evaluation study.** Its artifacts record
`dev_package.version` `0.9.2` for steps 1–3 and `0.9.3` for step 4 (`finalize`).
Those are the versions that ran, and 1.0.0 deliberately appears in no artifact.
The entire 0.9.2 → 0.9.3 code difference is contained in `read_arbitration`
(`study_eval/scle/arbitration.py`), whose only caller is `finalize`; steps 1–3
have no call path to it. Re-running the whole study under 0.9.3 or 1.0.0 would
therefore reproduce the same results.

- **`umc-research` and `beautifulsoup4` are removed from the dependencies.**
  Neither was imported anywhere in `src/`, `tests/`, or `scripts/` — both were
  left over from the legacy `lib/` modules deleted earlier. `umc-research` is
  proprietary and resolvable only from an internal index, so its removal is
  what makes the package installable outside UMC. Consumers that use
  `umc-research` themselves must declare it directly; the secure repo already
  does, with its own `[tool.uv.sources]` index mapping.
- **`openpyxl` moves from the `study` extra to a core dependency, and the
  extra is removed.** Four `study_eval` modules import it at module level, and
  `study_eval` is a documented entry point, so `pip install llm-dup-dev`
  followed by `docs/study_eval.md` used to end in an `ImportError`. The extra
  had no consumer: the secure repo installed plain `llm_dup_dev` and declared
  `openpyxl` directly. `llm-dup-dev[study]` still resolves for anyone who has
  it written down — it is now a no-op rather than an error.
- **`requires-python` is now `>=3.11,<3.13`**, matching what `agents.md` has
  always claimed, what the development environment uses (3.12), and what the
  secure repo enforces. The metadata previously said `>=3.11` with no upper
  bound.
- **The package is licensed**, under the Apache License 2.0, copyright 2026
  Uppsala Monitoring Centre. It had no licence before, which made it legally
  unusable by anyone outside UMC however openly it was shared. Apache-2.0 over
  a shorter permissive licence for the explicit patent grant and the explicit
  non-grant of trademark rights. `LICENSE` carries the verbatim licence text
  and `NOTICE` the copyright assertion; per-file licence headers are
  deliberately *not* added, so that the no-code-changed invariant above holds
  at the `v1.0.0` tag.
- **The method is named vigiTwin**, and the repository is published as
  <https://github.com/Uppsala-Monitoring-Centre/vigiTwin>. The **Python package
  is deliberately not renamed** in this release: `DevPackageInfo.name` is a
  default baked into `characterization_report.schema.json` and
  `study_evaluation_run_manifest.schema.json`, the module path appears in
  generated schema descriptions across five more schema files, and every import
  in the companion repository would break. That is a contract change on three
  fronts, so it belongs to 2.0.0 with a migration note — alongside the
  regulator-pilot work already expected to be breaking. Until then the
  repository is `vigiTwin` and the import is `llm_dup_dev`, and the artifacts
  recording `dev_package.name: "llm-dup-dev"` continue to match the releases
  that produced them.
- **`docs/integration_guide.md` § Public surface** enumerates the contract for
  the first time. Previously it was described as "the public API the secure
  repo imports", which named no actual set. Everything not listed there is
  internal and may change in any release — that boundary is what keeps future
  major bumps affordable.
- **`agents.md` § Conventions → Versioning** gains the post-1.0 bump rules, an
  explicit no-deprecation-window policy, and the separation between releases
  and study cycles. § Repository Structure has been rewritten — it described a
  layout with five "(planned)" packages that shipped long ago and two legacy
  packages that no longer exist. § Dependencies now records that no
  proprietary or internal-index dependency may be added to this package.
- **`README.md` was empty and is now written**, aimed at a reader arriving from
  the publication: what the method does, what can be run without database
  access, and how the two repositories divide.

## [0.9.3] - 2026-08-15

### Fixed — a blank feature-domain arbitration cell is a decision, not a gap

`read_arbitration` dropped every blank consensus cell, which is right for the
dimensions that carry a single value and wrong for the one that carries a set.
"No feature domains are implicated" is a real arbitration outcome, and the
workbook has no way to render it other than an empty cell. Read-back therefore
reported the decision as missing, and `merge_consensus` refused to finalize a
pair the arbiter had in fact resolved.

Encountered during finalization of the frozen SCLE evaluation: reviewer A
implicated no domains, reviewer B implicated one, and the arbiter sided with A.

- **A blank but applicable `implicated_feature_domains` consensus cell is now
  retained** as an explicit empty value, so the consensus resolves to
  `implicated_feature_domains == []` with
  `dimension_sources["implicated_feature_domains"] == "arbitration"`.
- **Blank stays "undecided" everywhere else.** Every other disagreed structured
  dimension still fails validation when its consensus cell is empty — this is
  not a general relaxation, and is pinned by a test.
- **A dimension that never applied to the pair is still not an answer.** An
  `N/A` cell, and an absent column in an older workbook, are both dropped as
  before, so no empty decision is invented for a question that was never asked.

`merge_consensus` is unchanged: `_parse_domains("") -> []` already carried the
empty case correctly once the key survives read-back. No schema changed and no
public API shape changed; this is behaviour-only, and ships as a patch.

One narrow consequence worth knowing: where the two reviewers *agreed* on a
non-empty domain set and the arbiter *cleared* that pre-filled cell, read-back
now surfaces it as an edit of a do-not-edit cell and `merge_consensus` raises,
where before the clearing was silently dropped and the agreed value stood. That
matches the workbook's stated rule — agreed dimensions are resolved from the
reviews — but the error text still offers "clear the consensus cell" as a remedy,
which no longer applies to this one dimension.

## [0.9.2] - 2026-08-06

### Added — shared loaders for the two frozen artifact shapes

Driving `characterization` from a notebook against an existing frozen validation
set surfaced two papercuts, both from the same root: a reports file has two
equally-correct shapes in this project, and the pair models spell their id
fields differently. Both are now handled once, in the library, instead of by
every caller. Purely additive; no schema changed.

- **`data.offline.load_reports(path) -> {report_id: Report}`** accepts **either**
  frozen shape — a JSON array of `Report` objects (the secure runner's
  `validation_reports.json`) or an object keyed by `report_id` (what
  `review_html` writes). The tolerance stops at the shape: a duplicate id, a
  mapping key that disagrees with the record inside it, a non-object list item,
  or any other top-level type is still rejected.
- **`data.offline.index_by_id(...)`** is the generic helper underneath it,
  promoted from `review_html.saved_run._index_by_id`. It also serves pair-keyed
  artifacts (`FrozenPairBundle`), which is why it stays generic.
- **`review_html`'s `--reports` now accepts a list as well as a mapping**, and
  **`characterization`'s `--reports` now accepts a mapping as well as a list**.
  Both are widenings — every file that worked before still works.
- **`study_eval.loading.reference_set_from_report_pairs(pairs, set_id=...)`**
  builds a `ReferenceSet` from `ReportPair`s a caller already holds, handling the
  `report_1_id` → `report_id1` rename. Ids are carried across, never re-derived,
  so a noncanonical pair still fails rather than being quietly repaired.
  `load_report_pairs(path)` reads a frozen `pairs.json` for it. Both are
  re-exported from `llm_dup_dev.study_eval`.
- `role` defaults to `"recall"` there, because that is the cheapest legal set —
  no per-pair labels. Describing a set should not require first inventing a
  mapping from the annotation vocabulary (`duplicates` / `otherwise_related` /
  `unrelated`) onto the human-reference one (`duplicate` / `non_duplicate`);
  that collapse is a study decision and the helper will not make it. Conversely
  `screening_mode` keeps the model's `"observed"` default — `not_evaluated` is a
  claim about the estimand and is never inferred.

`scripts/extract_frozen_*.py` keep their own array-only loader on purpose: they
warn-and-keep-first on a duplicate `report_id` rather than raising, which suits a
batch extraction job and is a separate decision from this one.

## [0.9.1] - 2026-08-06

### Added — reference-set characteristics ("Table 1")

A new package, `llm_dup_dev.characterization`, describes **what is in a
reference set**: reporting years, countries, patient age (median/IQR and 10-year
bands) and sex, the ten commonest drugs and adverse events (PT and HLT level),
and pair-level agreement on country, reporting year and sex. Purely additive —
no existing model, schema or function changed. Full detail in
`docs/reference_set_characteristics.md`.

- **New consumer contract:** `schemas/characterization_report.schema.json` with
  `examples/characterization_report_example.json`, regenerated by
  `scripts/generate_characterization_schema.py` and drift-checked by
  `tests/test_schemas_examples.py`.
- **New public API:** `build_characterization_report` /
  `build_reference_set_characteristics` take a `ReferenceSet` plus either a
  `{report_id: Report}` mapping or a `ReportLoader`, and return an
  aggregate-only artifact containing no report content;
  `write_characterization_report` emits JSON and markdown (CSV on request).
- **New command:** `python -m llm_dup_dev.characterization`, reading the same
  `study_reference_set.schema.json` files `study_eval analyze` reads.
- **`study_eval` is untouched and does not import it.** It never handles
  `Report` objects, so this runs before it as its own command rather than as a
  subcommand or a `run_analysis` parameter; a test enforces that nothing in
  `llm_dup_dev` imports the new package.
- **The reduction rule is recorded in the artifact.** Reports carry several
  candidate values per demographic, so tabulation needs a documented rule:
  structured sources win, and disagreement that survives canonicalisation is
  counted as `conflicting` rather than silently resolved. `policy` travels with
  the numbers, for the same reason `EvaluationRunManifest` records
  `analysis_positive_labels` beside the variant name.
- **Missingness is a category, never a dropped row.** Every distribution's
  dispositions partition its denominator, and `unavailable_reason`
  (`missing_drug_role`, `missing_hierarchy`) separates "this question could not
  be asked of this data" from "the answer is empty".
- **No small-cell suppression, by design.** Counts are exact; whether the
  artifact may leave the secure environment is a human decision made with the
  numbers in view. A test pins the absence of suppression.

## [0.9.0] - 2026-08-05

The breaking changes below shipped together. `0.9.0` has been exercised
secure-side, so it now names this contract and nothing else — the additive
characterization package above is `0.9.1` rather than a re-cut of this number,
for the same reason `0.7.0` could not be reused (see below).

### 0.7.0 was retired, not released

The version was bumped to 0.7.0 for the SCLE form simplification, and a
secure-side dry run of the SCLE workbooks and orchestration then ran against it.
That run baked `0.7.0` into durable artifacts — `dev_package.version` in its
`evaluation_run_manifest.json`, and `package_version` in every `SamplingRecord`
inside its `scle_sample_*.json`, which is the file reviewer workbooks checksum
against. So 0.7.0 already denotes an exercised contract: the simplified form
with the **previous** workbook layout.

Reusing the number for the new layout would leave `dev_package.version: 0.7.0`
meaning two different workbook contracts. The next release is therefore 0.8.0,
and 0.7.0 is spent — it names the dry-run contract and nothing else. There is
deliberately no `[0.7.0]` heading below; artifacts carrying that version are
identified by their `dev_git_commit`.

### Breaking — SCLE form simplified and partly study-configured

The study team refined the Structured Case-Level Examination questions. Full
detail, including what the secure side must do, is in
`docs/migration_notes_scle_form_simplification.md`; the aggregate performance
analysis (Step 1) is untouched.

- **Case difficulty gained a `clear` category** between `trivial` and
  `non_trivial`. Duplicate identification is inherently ambiguous, so `trivial`
  is now reserved for pairs where nothing is left to judge; `clear` covers pairs
  whose correct label is obvious to a human but would still require reliable
  extraction by a computational method.
- **`never_event` and `unexpected_method_behaviour` are strictly `yes`/`no`.**
  `YesNoUnclear` is replaced by `YesNo`; existing `unclear` answers are no
  longer expressible.
- **`material_input_data_limitation` and `input_data_limitation_category` are
  replaced by one binary `incorrect_or_imprecise_input_data`** — the only
  input-data problem the team expects to identify reliably. The
  `InputDataLimitationCategory` enum is removed.
- **`primary_error_source` is replaced by a free-text `error_source_description`
  plus one tick-only flag per method stage.** Misclassifications usually involve
  an interaction between components, which a single-choice enum could not
  express. The `PrimaryErrorSource` enum is removed.
- **The stage list and the feature-domain list are now study configuration that
  generates columns.** `generate_reviewer_workbooks` takes `error_stages`
  alongside `feature_domains`; each entry becomes one tick-only yes/no column,
  and each list is embedded in the workbook so every later step recovers it from
  the artifact. The package-level `FEATURE_DOMAINS` default is removed —
  **omitting a list now drops that question from the examination**.
- **The structured-dimension set is no longer fixed.** Each configured stage is
  its own dimension (`error_stage::<stage>`) with its own kappa and arbiter
  consensus column; feature domains stay one set-valued dimension.
  `DiscrepancyReport` and `ArbitrationSubmission` gained `structured_dimensions`;
  `ReviewerSubmission` gained `error_stages` and `feature_domains`.
- **Tick-only answering changes an agreement denominator.** A blank cell means
  "no", so two reviewers who both ticked nothing on a tick-only dimension now
  count as agreeing rather than being skipped as inapplicable.
- **Every question carries its interpretation inside the workbook** — a comment
  on its header cell plus a `guidance` sheet.
- Module `scle/feature_domains.py` became `scle/vocabularies.py`;
  `augment_reviewer_workbook` and `validate_submission_feature_domains` are
  removed (an out-of-vocabulary domain is no longer expressible). The
  `study_eval` CLI's `workbooks` subcommand gained repeatable `--error-stage`
  and `--feature-domain` flags.

### Breaking — SCLE workbooks laid out for surveying

The reviewer and arbitration workbooks are the surface two co-authors work for
hundreds of pairs, so the pair sheet was rebuilt to fit on a screen. Full detail
is in `docs/migration_notes_scle_workbook_layout.md`. No model field, JSON
schema or Python identifier changed — this is the spreadsheet surface only —
but a workbook written by an earlier version no longer reads back.

- **The `cases` sheet is now `pairs`,** matching the entities of the reference
  sets; the metadata key `n_cases` is now `n_pairs`, and the reviewer-facing
  wording says "pair" throughout.
- **The `not_applicable` sentinel is now `N/A`** (plain text, not Excel's `#N/A`
  error value), and `Case difficulty` is now `Difficulty`.
- **The generated column prefixes are `S: ` and `F: `** instead of
  `Stage contributing: ` and `Implicated: `. The `instructions` and `guidance`
  sheets spell out what each letter means.
- **The pair id links to the pair's HTML packet,** so the separate `Packet`
  column is gone.
- **Auto-context columns are chosen per sample.** A column holding one value for
  the whole workbook — the component, the reference set of a single-set sample,
  the screening verdict on a precision set — is stated once on `metadata`
  instead; a column no sampled pair populates is dropped. `Misclassified` and
  the rest are never dropped for merely looking uniform in a small draw.
- **The pair sheet is formatted**: every cell top-aligned, labels centred, prose
  columns left-aligned and wrapped, per-column widths, and panes frozen below
  the header and right of the pair id.
- `read_reviewer_submission` now rejects a pair sheet whose `Pair ID` column has
  been renamed away, instead of reporting every row as missing.

### Breaking — the analysis variant is study configuration, not a dev-side enum

`AnalysisVariant` is removed. Which label collapses a study runs, what they are
called, and which one is the baseline are the study's prespecified choices, so the
package now owns only the adjudicator's output vocabulary
(`ADJUDICATOR_LABELS`) and the mechanism: a `PositiveClass(name,
positive_labels)` whose labels are validated as a non-empty subset of it. This is
the division `scle/vocabularies.py` already applies to the SCLE vocabularies.

The fixed enum could not express the protocol's own secondary analysis 2
("*Abstain* **and** *Otherwise related* positive"): `OTHERWISE_RELATED_POSITIVE`
is `{Duplicates, Otherwise_related}`, so that analysis was unrunnable without a
dev-side code change.

Full detail, including what the secure side must do and the checks to run before
the frozen analysis, is in `docs/migration_notes_positive_class.md`.

- **`run_analysis`, `run_precision_analysis` and `run_recall_analysis` take
  `positive_class: PositiveClass`** in place of `variant`, defaulting to `PRIMARY`.
  `PRIMARY`, `ABSTAIN_POSITIVE` and `OTHERWISE_RELATED_POSITIVE` remain as
  convenience constants for this study; a study may define its own instead.
- **`run_precision_variants` / `run_recall_variants` require an explicit
  `variants` sequence** and return dicts keyed by rule name. The
  `REINTERPRETATION_VARIANTS` tuple is removed — which rules to run is not a
  package default. Empty lists and repeated names are rejected.
- **The baseline is named explicitly.** `write_study_artifacts`,
  `write_secondary_comparison_csv` and `summary_to_markdown` take `baseline=<rule
  name>`, required wherever results are supplied. Both writers previously found
  the baseline by matching the literal string `"primary"` and, finding nothing,
  emitted a header-only `secondary_comparison.csv` and a title-only `summary.md`
  — silently, and reachable today by any run under a non-primary variant. A
  missing baseline now raises.
- **`secondary_comparison.csv` is written only when the results span two or more
  rules**, so a single-rule run no longer produces an empty comparison file. Its
  `primary_proportion` column is renamed `baseline_proportion`, and `summary.md`
  headings name the baseline rule rather than saying "primary".
- **The manifest records the rule's labels alongside its name.**
  `EvaluationRunManifest` gains `analysis_positive_labels`; because rule names are
  study-supplied, `analysis_variant` alone no longer identifies what was counted
  as positive. Schema and example regenerated.
- The CLI keeps `--variant` over the three named rules and gains a repeatable
  `--positive-label` for defining an ad-hoc rule.

### Fixed — SCLE auto-context could contradict its own stratum

`sample_scle_precision` and `sample_scle_recall` derived `methods_disagree`,
`is_misclassified` and `confusion_cell` under the **primary** rule regardless of
the rule the analysis actually ran under, while taking the strata from the
variant-aware result. Under any non-primary rule the two disagreed: a recall pair
whose adjudicator label was `Abstain` landed in the `both` stratum — positive
under `ABSTAIN_POSITIVE` — while being flagged `is_misclassified=True`. Since
`methods_disagree` and `is_misclassified` gate which conditional dimensions a
reviewer is asked, a reviewer would have been asked the wrong questions.

Both samplers now take a required `positive_class` and use it, so a sample is
internally consistent whichever rule produced it. `run_analysis` forwards the rule
in force.

Consequently **SCLE sampling and the collapse rule are now independent**, and the
warning that fired when `sample_scle=True` met a non-primary variant is gone: it
claimed the strata reflected that variant's rule, which was only half true, and
examining cases under a secondary rule is a legitimate study configuration.
Running the primary rule aggregate-only was, and remains, equally supported.

### Changed — an empty reference-set bundle is now an input error

`preflight_reference_sets` (and so `run_analysis`) rejects a bundle with no
reference sets. It previously returned cleanly, writing a manifest, an empty
`predictions_table.csv` and an estimate-free `study_results.json` — a run that
measured nothing was indistinguishable from a run that succeeded. Any bundle
holding at least one set is unaffected.

### Fixed — `secondary_comparison.csv` mis-attributed precision rows

Every precision row was labelled with the **first** primary result's `set_id`
and differenced against **that** set's baseline, whichever set the variant
result actually came from. A bundle pooling two precision sets across the
re-interpretation variants therefore filed half its rows under the wrong set,
against the wrong baseline: a row reading `prec_a, adjudicator_precision,
abstain_positive, 0.5714, 0.6667, 0.0952` was `prec_b`'s result, whose true
delta against its own 0.6667 baseline is `0.0000`. Recall rows were already
keyed per set and were never affected.

Precision now keys its baselines by `set_id` as recall does, and each row names
the set its figure came from. A variant result whose set has no primary result
in the bundle now raises `KeyError` rather than silently borrowing another
set's baseline.

The table is only populated by a caller that assembles several variants into
one `write_study_artifacts` call — via `run_precision_variants` /
`run_recall_variants` — so a study following the documented one-variant-per-run
workflow has an empty comparison table and no affected figures. Any consumer
that built one by pooling **more than one precision set** should regenerate it.

### Documentation and tests

- **Single-role bundles are supported, and now pinned.** A precision-only or a
  recall-only study runs through all four steps: the absent role's estimands,
  result tables and SCLE sample are not produced, and `summary.md` carries only
  the section that applies — an absent table is never a table of zeros. This
  already worked, but every workflow test used a both-roles bundle, so nothing
  stopped a future validator from quietly taking it away.
  `tests/test_study_eval/test_workflow.py` now covers each role alone
  (including steps 2–4 on a recall-only sample), and `docs/study_eval.md` §
  Workflow states it.

- Pinned and documented the exact scope of what a canonical `pair_id`
  guarantees about `PairFeatures` content. `generate_pair_features` derives a
  canonical id but leaves `r1` / `r2` in the caller's order, so a reversed
  direct call returns a payload differing in presentation under the same
  `pair_id` — `evidence_summary.report1_only` / `report2_only` swap, and
  descriptions quoting both sides quote them in the other order. Everything
  that scores, joins or decides is invariant, including the whole
  `ClassificationResult`.

  That invariance was emergent rather than enforced.
  `tests/test_pipeline.py::TestOrientationInvariance` now asserts it across the
  whole payload except those two exceptions, so a future orientation-dependent
  rule fails loudly instead of making one `pair_id` mean two different answers.
  No behaviour change; see `docs/data_contracts.md` § Canonical pair identity.

## [0.6.1] - 2026-08-01

Reviewers comparing an adjudication against the screening method that surfaced
the pair can now see what that method computed. A new optional artifact,
`ScreeningDetails`, carries the per-pair basis for the **screening**
algorithm's classification, and `review_html` renders it as a Screening
Algorithm section directly beneath Classification on each pair page. Nothing in
feature generation or the decision logic reads it — it is presentation only.

### Added

- `llm_dup_dev.data.offline.ScreeningDetails`, with `save_screening_details` /
  `load_screening_details`. The artifact is keyed by `pair_id`, optionally
  wrapped with the algorithm's name:
  `{"algorithm": "vigiMatch", "entries": {"<pair_id>": {"<feature>": <value>}}}`.
  The bare `{pair_id: {feature: value}}` form is accepted too and normalised on
  load, leaving `algorithm` unset.

  Screening algorithms vary, so the per-pair payload is deliberately **untyped
  at the feature level**: any set of `feature -> value` pairs validates, keys
  render verbatim (they are the algorithm's own identifiers), and key insertion
  order is the display order. Values must be scalars or flat lists of scalars;
  a nested object is rejected at load, since there is no sensible rendering for
  one in a two-column table. Coverage may be partial — pairs absent from
  `entries` render with no section at all.
  Schema: `schemas/screening_details.schema.json`.

- Pass-through on every `review_html` entry point: `--screening` on the CLI,
  `screening_path=` on `load_saved_run` / `render_saved_run` (whose canonical
  pair-key check now covers it, so `allow_noncanonical_pairs` governs it like
  every other pair-keyed artifact), `screening=` / `screening_algorithm=` on
  `PairRenderInput`, `ResultRenderInput`, `build_pair_view_model` and the
  `render_*_html` functions, and `screening=` on
  `study_eval.scle.packets.generate_packets` for SCLE reviewer packets.

  On the SCLE path this is a `generate_packets` parameter rather than a
  `PacketContent` field: the `PacketContentProvider` seam exists to keep
  confidential *report content* out of the generic schemas, and screening
  details are already a generic pair-keyed artifact the caller can load whole.

  Display formatting is fixed by the renderer so pages stay comparable across
  algorithms: floats at two decimals, extended only far enough to reach a
  significant digit (`0.0034` → `0.003`); booleans as Yes/No; nulls and empty
  lists as an em dash.

All additions are optional and backward compatible — existing callers,
artifacts and rendered pages are unaffected.

## [0.6.0] - 2026-07-31

Canonical pair identity: a report pair now has **one** identity regardless of
the order its two report ids are supplied in. The ids are stored in ascending
canonical order — numerically for numeric ids, so `9` precedes `10` — and
`pair_id` is always `{report_1_id}__{report_2_id}` derived from that order.

Previously nothing sorted the two ids, so `A__B` and `B__A` were different ids
for the same two reports. Because `PairFeatures` and `ClassificationResult`
carry only a `pair_id` and no report ids, that id is the sole join key between
features, classifications, annotations, reference sets and predictions — and a
flipped orientation silently scored zero pairs while still reporting a number.

Minor rather than patch: `ReportPair` and `study_eval.ReferencePair` now reject
input they previously accepted, and `eval.sweep.Coverage` loses a field.

See `docs/migration_notes_canonical_pair_identity.md` for the secure-side
migration, including which artifacts must be regenerated.

### Added

- `llm_dup_dev.models.pair_identity` (re-exported from `llm_dup_dev.models`):
  `canonical_pair_id`, `canonical_report_ids`, `report_id_sort_key`,
  `split_pair_id`, `validate_pair_identity`, `validate_report_id`,
  `PAIR_ID_SEPARATOR`.
- `ReportPair.from_report_ids(a, b)` and `ReportPair.from_reports(r1, r2)`,
  which canonicalise. These are the supported way to build a pair.
- `allow_noncanonical_pairs` on `load_saved_run` / `render_saved_run` and
  `--allow-noncanonical-pairs` on the review-HTML CLI: an escape hatch for
  *rendering* a run saved before canonicalisation. Not offered on the
  `study_eval` path, where a noncanonical set must be fixed at source.

### Changed (breaking)

- `ReportPair` validates its identity on construction and on `model_validate`.
  Noncanonical report order, a `pair_id` inconsistent with the ids, a self-pair,
  a blank id, and an id containing `__` are all rejected. Records are **not**
  silently rewritten: `pair_id` is a join key, so a rewrite would break the
  joins the invariant protects. `schemas/report_pair.schema.json` regenerated.
- `study_eval.ReferencePair` enforces the same invariant;
  `schemas/study_reference_set.schema.json` regenerated. Its separate
  unordered-pair check is gone — with canonical ids, two rows naming the same
  two reports collide on `pair_id` and are caught as a duplicate.
- `eval.annotations.derive_pair_id` is now canonical. Same name and signature;
  the returned id no longer depends on argument order. An annotation file
  listing a pair the other way round from the run therefore joins correctly, and
  the same two reports listed twice in opposite orientations now fails at load.
  A row's own `report_id1` / `report_id2` keep file order as provenance.
- `feature_generation.generate_pair_features` derives a canonical `pair_id` when
  none is passed. It does **not** swap `r1`/`r2`: feature content is
  orientation-dependent, and the roles are canonicalised upstream at `ReportPair`
  construction.
- `eval.sweep.Coverage.reversed_orientation` and `eval.sweep._reverse_pair_id`
  removed, along with the corresponding warning in `eval.report`. With both
  sides canonical a reversed annotation file is no longer possible; a *features*
  file predating canonicalisation now shows up under `unmatched_annotations`.
- `load_saved_run` rejects pair-keyed artifacts (`pair_results`, `features`,
  `classifications`, `bundles`) whose keys are pre-canonical pair ids. Keys that
  are not two report ids joined by `__` are left alone — `pair_id` remains
  free-form on `PairFeatures` and `ClassificationResult`.

### Migration

- Regenerate any `pairs.json`, `features.json`, `classifications.json`,
  `pair_results.json`, frozen bundle file, reference set, prediction set, SCLE
  packet or review HTML whose `pair_id` is not canonical.
- Features and classification artifacts carry no report ids, so their contents
  cannot be validated — regenerate them whenever their `pairs.json` fails.
- A stale bundle file surfaces as `FrozenStatsProvider`'s existing
  `No frozen bundle found for pair_id ...`.
- Review-HTML left/right column order flips for pairs previously supplied as
  (candidate, match) where the candidate sorts second.
- Self-pairs are now invalid; a report-against-itself sanity control must use
  two distinct report ids.
- Report ids must be `str` — pydantic does not coerce `int` from SQL.

## [0.5.0] - 2026-07-31

Explicit inter-report links: the adjudicator now reads the reports'
structured case-identifier fields (ICH E2B(R3) C.1.9 "Other case
identifiers in previous transmissions"). When one report of a pair names
the other, the sending organisation has declared them to be the same
case — the most direct evidence of shared provenance the data can offer,
and the pipeline previously ignored it.

Minor rather than patch: `IndependenceReason` gains a member that
consumers may switch on, and the review HTML gains a consumer-visible
section and index column.

### Added
- `Report.case_identifiers` and `Report.other_case_identifiers`, both
  optional and defaulting to `[]`, and deliberately asymmetric in shape.
  `case_identifiers` holds the report's own case numbers as
  `CaseIdentifierRef` entries typed by `CaseIdentifierKind`
  (`safety_report_id` / `company_number` / `authority_number`), since the
  source system records them in three named fields.
  `other_case_identifiers` holds **plain strings** — the C.1.9 field
  carries the identifier only, with no kind qualifier.
  **Secure-repo action required** to realise the feature:
  `ReportLoader.load_report` must populate both — a pointer on one report
  can only be resolved against the other report's own identifiers. See
  `integration_guide.md` § ReportLoader.
- `feature_generation.explicit_link_comparison.compare_explicit_links` —
  deterministic, LLM-free, DB-free pair comparator. Matches on the
  normalized value alone, testing each pointer against every identifier
  the other report holds regardless of which of its fields that value
  came from (any of the three can carry a worldwide-unique identifier).
  Drops self-pointers and degenerate values, and requires a *direct*
  A→B / B→A reference — a shared pointer at a common third case does not
  link.
- `PairFeatures.explicit_link` carrying an `ExplicitLinkAssessment`
  (`explicit_link` / `no_link` / `not_assessed`). `None` on features
  generated before this release; `not_assessed` means neither report
  carried identifier data and must be read as *no information*.
- `IndependenceReason.EXPLICIT_LINK`. An explicit link rejects
  independence outright and is recorded in preference to
  `SHARED_SOURCE` when both apply.
- Relationship-assessment **explicit-link zone** at priority 3 — below
  Identity and Distinct, above Unresolved — adjudicating at the
  compelling budget (`w_tot < w_tot_compelling_zone`, default 4.0) and
  failing to `Different_case` rather than `Abstain`. No new threshold
  parameter.
- `DecisionThresholds.explicit_link_enabled` (default `True`). Placed on
  `DecisionThresholds` so `classify` stays a pure function of
  `(PairFeatures, DecisionThresholds)` and `eval.sweep` can replay
  archived features with the signal on and off. This matters for
  evaluation: some regulator reference sets were partly assembled from
  E2B link fields, so recall on those pairs is circular while the signal
  is enabled — see `duplicate_adjudication_spec.md` § Calibrating them.
  Surfaced in run manifests as `feature_flags.explicit_link_enabled`.
- Review HTML: an "Explicit Case Links" section on the pair page (match
  table plus both reports' raw identifier lists, shown for `no_link` too
  so a reviewer can see why nothing resolved) and an "Explicit link"
  column on the batch index, present only when some pair is linked.

## [0.4.0] - 2026-07-31

Removal of the standalone LLM narrative-consistency assessment, which was
built but never wired into the decision logic.

### Removed
- `llm.tasks.narrative_comparison` (the `NarrativeVerdict` schema,
  `build_narrative_request` and `compare_narratives`). Its verdict reached
  `PairFeatures.narrative_consistency` and stopped there: unlike every other
  assessor it never produced an `Inconsistency`, so it contributed nothing to
  `inconsistency_counts`, the probability, or the final label. The classifier
  never read the field.
- `PairFeatures.narrative_consistency` and the `NarrativeConsistency` enum
  (also dropped from the `llm_dup_dev.models` public exports).
- `PairResult.narrative_verdict`, and the `narrative_verdict` key from the
  `pair_results.json` artifact.
- The `NarrativeProvider` type alias, `neutral_narrative_provider`, and the
  `narrative_provider` parameter of `PipelineRunner.run_batch`. The callback
  existed only to supply a value nothing consumed.
- The `narrative_consistency` parameter of `generate_pair_features`, the
  "Narrative consistency" row on the HTML pair page, and the unrendered
  `narrative_consistency` field on the review index view model.

### Changed
- A configured `llm_client` now costs **five** LLM calls per pair instead of
  six: the per-pair narrative comparison is gone, leaving the AE
  clinical-picture call plus the two per-report source/case and two per-report
  demographics extractions.

### Migration
Reading an existing `pair_results.json` or `features.json` still works — the
removed keys are ignored on load, since no model forbids extra fields. Callers
passing `narrative_consistency=` or `narrative_verdict=` to `run_pair`, or
`narrative_provider=` to `run_batch`, must drop those arguments.

## [0.3.0] - 2026-07-29

Support for reference sets where no screening-algorithm results exist, so that
adjudicator performance can be evaluated on the supplied set without screening
recall being observed or assumed.

### Added
- `ReferenceSet.screening_mode` (`observed` | `not_evaluated`), selected per
  reference set and never inferred. `not_evaluated` declares that screening
  performance is outside that set's estimand: every supplied pair goes directly
  to adjudicator evaluation, screening and end-to-end estimands are not
  computed, and no synthetic screening verdicts are created. It is not a claim
  that screening was perfect. Only a recall set may use it — the combination is
  rejected on a precision set, which *is* the screening candidate set.
- `study_eval.preflight`, a reusable pre-analysis validation module whose
  screening requirements are conditional on each set's mode. It accumulates all
  problems and raises them together, and is called by both the analysis
  functions and `workflow.run_analysis`.
- `RecallResult.screening_evaluated_set_ids` /
  `screening_not_evaluated_set_ids`, exposing the scope any screening figure
  covers when a run mixes the two modes.
- `EvaluationRunManifest.screening_modes` records the mode of every reference
  set in the run.
- `screening_mode` columns on `recall_metrics.csv`,
  `recall_joint_classification.csv` and `predictions_table.csv`, and
  `screening_mode` fields on `PrecisionResult` / `RecallSetResult`.
- `aggregate.ADJUDICATOR_STRATA`, the two-value stratification SCLE sampling
  uses for a set with no screening dimension.

### Changed
- **Breaking:** `ReferenceSet.screening_required` is replaced by
  `screening_mode`. It was only ever read to relax the precision-set candidate
  check, which is now unconditional for precision sets.
- **Breaking:** `PairPrediction.screening_retrieved` is now optional
  (`bool | None`), so a prediction bundle need carry no screening verdict.
  It remains required for every pair of an `observed` set, enforced by
  preflight.
- **Breaking:** `RecallSetResult.recall_screening`, `recall_serial`,
  `joint_counts` and `joint_proportions` are now nullable, and are `None` under
  `not_evaluated`. `None` means *not estimated*, and stays distinct from an
  estimate that is undefined at n=0 (a present estimate with `proportion: null`
  and `denominator: 0`). Screening rows are omitted from the recall CSVs for
  such a set rather than written blank.
- `SCLEAutoContext.methods_disagree` is nullable: without a screening verdict
  there is no second method to disagree with, so the disagreement dimension does
  not apply. Reviewer workbooks show `not_applicable` for it and for
  `Screening retrieved` on those cases.
- A `screening_retrieved` value supplied for a pair in a `not_evaluated` set is
  accepted and never read — the mode describes the estimand, not whether
  screening data exist — so reference sets may overlap across modes. Preflight
  warns rather than failing, so the value is not ignored silently.

## [0.2.0] - 2026-07-27

Baseline release. The version had remained `0.1.0` since the project's early
days while the schemas and public API evolved substantially; this is a one-time
honest catch-up bump to re-anchor the contract signal, and the point from which
`CHANGELOG.md` is maintained going forward.

### Added
- `CHANGELOG.md` and a documented versioning policy (`agents.md` § Conventions).
