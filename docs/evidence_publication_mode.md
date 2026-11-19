# Publication mode for evidence presentations

The internal evidence review (`docs/evidence_format.md`) shows report values
as reported: original identifiers, country names, real dates, verbatim
narrative text. Every page says so in its footer, and it is not publishable.

Publication mode is a second presentation of the same cases, built from the
same view model and the same template, with the displayed values transformed.
It lives in `llm_dup_dev.review_html.evidence.publication`.

Two things it is not:

- **It is not a new presentation.** The internal review is unchanged — the
  same builder, the same colour rule, the same template. The only difference
  in the rendered internal page is one additional CSS selector, for a state
  internal pages never carry.
- **It is not an anonymisation certificate.** A cleared candidate is one
  where every transform succeeded and the residual scan found nothing. That is
  a statement about this layer's own checks, not a disclosure assessment.
  Publication output is a candidate for disclosure review, and both the page
  footer and the public manifest say so in those words.

## What is kept and what is transformed

| | |
|---|---|
| **Kept verbatim** | Drug substances, roles, forms and strengths; adverse event terms; sex; contextual frequency counts and the country-year they belong to; matching scores, contributions, penalties and weights; every evidence role, attribution and classification |
| **Shifted** | Report dates, drug start and end dates, adverse event onsets, and the reporting context's year — one whole-day offset per pair |
| **Offset** | Ages, heights and weights — one offset per pair and per unit |
| **Aliased** | Report identifiers → `Report A`, `Report B`; other identifiers → `Identifier 1`…; shared source identifiers → `Source 1`…; the case → `Pair 3` / `pair-03`; the provenance path → a policy label. Countries → `Country 1`, `Country 2` **only when asked** (see below) |
| **Held for review** | Human-authored interpretations, verbatim narrative snippets, and curator-written titles, ledes and kickers |

vigiTwin is never re-run. The input is the built `EvidencePageVM`, so this
layer has no access to the evidence a verdict was made from and no way to
compute a different one.

## Why the structured model and not the HTML

`EvidenceCell.text` is the only field in the evidence view model that holds a
report value, and `ValueKind` exists so a transform can dispatch on what a
cell contains. Both were put there for this layer, and
`TestSubstitutionInvariance` has proved since phase 2 that rewriting every
`text` leaves the rest of the tree byte-identical.

Transforming the model rather than the markup buys three things a
find-and-replace over HTML cannot:

1. A value is transformed **by its class**, so a date written into a drug-name
   cell is not shifted by accident and a drug name that happens to look like a
   country is not masked.
2. The model's deliberate object sharing does the distribution.
   `DiscrepancyVM.left` *is* the characteristics row's cell; a chip's text is
   derived from those cells at render time. One rewrite reaches the table, the
   chip and the finding, and they cannot drift.
3. The validation has something to validate. Relationships between dates are
   computable from the model; they are not recoverable from rendered markup.

The rendered HTML is still read before it is written, as a last check that
nothing a template composes escaped the model walk. Read, never rewritten: a
page that only looks clean after its markup was edited is a page whose model
was wrong.

## Consistency: one table, not a sequence of edits

Each case gets a **substitution table** mapping original display value →
published display value. Everything downstream reads from it: cell text,
prose rewriting, the residual scan, the secure key record and the
side-by-side review. Because it is a table, one original cannot come out two
ways — a second attempt to map it differently is a conflict and a conflict is
a finding.

Cell values are multi-valued where a report carried several candidates
(`build._sourced_text` joins them with `"; "`), so the table's keys are the
*components*, and a cell is transformed component by component and rejoined.

## Dates: the hard case

Two whole-day offsets apply to the pair — one to its **report dates**, one to
its **clinical history** (drug start and end dates, adverse-event onsets).
See § Two offsets, not one for why they are separate. Within each block the
offset is uniform, so for complete dates that is the whole story. For partial dates the shift is defined on the timeline and
written back at the date's **original precision**:

```
shift(d, delta) = truncate(earliest_plausible_date(d) + delta, resolution(d))
```

- `day` — `2023-11-13` + 30 → `2023-12-13`; an exact shift.
- `month` — the first of the month, shifted, truncated: `2023-11` → `2023-12`.
- `year` — the first of January, shifted, truncated: `2023` + 30 →
  `2023-01-31` → `2023`.

The worked example from the specification, at +30 days:

| Feature | Original | Published |
|---|---|---|
| Age, both reports | 81 years | 79 years |
| Adverse event onset, A | 2023-11-13 | 2023-12-13 |
| Adverse event onset, B | 2023-11-12 | 2023-12-12 |
| Drug start, both | 2023 | 2023 |
| Report date, both | 2024-02-20 | 2024-03-21 |

The one-day difference between the onsets survives, and the year-only drug
start stays `2023` because thirty days from 1 January 2023 is still 2023.

**The rule guarantees representability and nothing else.** Whether the
evidential meaning survives is checked separately, offset by offset.

### Two offsets, not one

A report date and a clinical date are independent in the source. The first
says when a report reached the centre; the second says when something
happened to a patient, and reports routinely arrive years after the event.
They are also differently constrained in the page:

- a **report date** is pinned near its reporting-context year, because the
  contextual counts are real and belong to that country-year (§ What an
  offset must preserve);
- a **clinical history** mixing year-, month- and day-precision dates can
  usually only move by *whole years*, because anything else moves a bare
  `2006` relative to a `2006-05-30` and changes what the page says about
  their order.

Under one offset those two demands met, for a long history, only at zero. A
real pair carrying sixteen dates across twelve years had exactly three
offsets that satisfied its invariants — `-365`, `0`, `+365` — and its
published supplement therefore carried the patient's real timeline. Solved
separately, the same pair takes a report offset of a few weeks and a clinical
offset of four years.

**What the split gives up is the absolute interval between an event and its
report.** That interval is no longer the real one, the footer says so, and
the key record holds both offsets. **What it preserves is every interval the
adjudication reads**: both reports' clinical dates move together and both
reports' report dates move together, so each report's own latency changes by
the same amount and the difference between them — the duplicate signal —
survives exactly. Where a finding states a separation *across* the two
blocks, its figure has to survive exactly, which only a shared offset can
promise, so such a case takes one offset however the policy is set.

The clinical block moves in **either** direction. The thing a forward shift
could break — carrying an event past the report that describes it — is
checked directly rather than avoided by rule: every clinical date keeps its
ordering against the report date of **its own report**. Per report, because a
relationship between one report's event and the other report's arrival date
is not something the page states, and holding an offset to it would reject
candidates for no reader's benefit. A date shown in neither column, such as a
drug both reports carry, is held to both.

### A coarse date may stay where it is

A year-only value does not move under a shift smaller than a year: thirty
days from 1 January 2006 is still 2006. So in a history mixing precisions, a
modest offset moves the day-level dates and leaves the year-only ones, and
where a day-level date then leaves a year's span — `2006-12-05` becoming
`2007-01-04` beside a bare `2006` — their order has changed.

Treated as a hard failure, that left whole-year offsets as the only ones a
mixed-precision history could take. It is an advisory instead,
`coarse_date_drift`, because an unmoved year is close to no disclosure on its
own: it is the dates beside it, known to the day, that identify, and those do
move. Two things keep the relaxation narrow — where a finding *states* the
separation it is still a hard failure, and an offset that avoids the drift
entirely is still preferred, since the first pass takes an advisory-free
candidate. A whole year therefore wins wherever a whole year is available.
`allow_coarse_date_drift` turns it off.

### Zero is a last resort, not a preference

Zero stays in the candidate pool wherever the policy allows it. A transform
obliged to change every value tells anyone holding the policy that the
published date is *not* the true one, and every excluded offset narrows the
list further; "it looks unshifted" and "it is unshifted" have to be states an
observer cannot tell apart, which means the first must be possible.

But keeping zero in the pool is not the same as letting it win, and for a
long time it won far more often than the design believed. Zero is the one
offset guaranteed to raise no advisory — nothing moves, so no relationship
can move — and the search's first pass accepted only advisory-free offsets.
In any date-rich case where few offsets are advisory-free, zero took the
case by construction, which is precisely the population whose real timeline
identifies best. It is now **tried last**, after every other candidate in
every band has failed. The pool is unchanged, so an observer learns nothing
new.

The floors are the other half. `date_offset_min_days` and
`clinical_offset_min_days` default to 30 days, and they are **preferences,
not refusals**: the search walks bands, strongest first, so a case whose
relationships survive nothing larger than four days is published four days
out rather than refused, carrying a `date_offset_below_floor` advisory that
says the obfuscation is weaker than the policy asks for. A floor that
refused outright would turn every hard history into manual handling, and the
case that motivated the floor carried sixteen dates.

Where a case still comes out entirely as reported, the **review reports it**:
a `date_pattern_unchanged` advisory, a line above the tables on the
disclosure review page, and dates-moved against dates-total in the key
record. The judgement is a reviewer's; the obligation of this layer is to put
it to them.

### What an offset must preserve

Over every ordered pair of dates in the case:

| Rule | Scope |
|---|---|
| Precision unchanged | every date |
| Display equality — alike stays alike, different stays different | every pair |
| The pipeline's own resolution-aware match verdict, and at which resolution | every pair |
| Exact day difference | every pair of complete dates |
| Separation — exactly where it is zero or short, within tolerance and inside its severity band otherwise | every pair of complete dates, and every pair a finding states a separation between |
| Interval order — lower and upper bound ordering, and strictly-before / strictly-after | every pair |
| The reporting context's year still coincides with a report date's year, where it did | the case |
| Every date stays inside the policy's era window | every date |

The interval primitives are the pipeline's own
(`clinical_date_alignment.min_day_difference`,
`matching._compare_partial_dates`), so the publication layer measures dates
exactly the way the assessment measured them.

### What a separation has to preserve, and what it does not

The objective is the evidential meaning of a discrepancy, not the number of
days. Three rules, in order of how directly a reader uses the figure:

1. **Zero stays zero.** Two dates that could be the same date are a different
   statement from two that are a week apart.
2. **Short separations are preserved to the day** — at or below
   `separation_exact_days`, seven by default, which is the whole minor band.
   A one-day discrepancy between two onsets is evidence a reader weighs
   directly, and it can never be rounded into something else.
3. **Larger separations may move by `separation_relative_tolerance` of
   themselves — 20% by default — provided they stay in the same severity
   band.** The bands come from the pipeline's own clinical-date thresholds
   (`_CLINICAL_DATE_MINOR_DAYS`, `_DECISIVE_IMPOSSIBILITY_GRACE_DAYS`,
   `_CLINICAL_DATE_MODERATE_DAYS`), imported rather than re-spelled, so the
   transform cannot move a finding into a severity the assessment never gave
   it. Near a boundary the band binds; far from one, the tolerance does. The
   direction is held by the interval-order rule, which admits no tolerance at
   all.

A separation of 51 days may become 56 (same band, +10%); it may not become 81
(crosses the 75-day boundary and leaves the tolerance). A separation of 796
days may become 900; it may not become 1200.

Where one side is a partial date, the rule is enforced for the pairs a finding
states something about, and a breach elsewhere is recorded as an **advisory**.
That distinction is what makes the feature usable: a year-only date keeps its
anchor while a complete date moves, so their *plausible* separation shifts
under almost any offset, and requiring every unasserted bound to survive is
unsatisfiable the moment a case mixes the two — which is most real pairs.
Advisories do not block by default: nothing has leaked, and what moved is a
reading the page does not offer. `PublicationPolicy.advisories_block` treats
them as blockers.

### The quoted figure is recomputed

Because a separation may move inside its tolerance, the figure a finding
quotes — "at least 51 days apart" — is **recomputed from the dates now
printed beside it**, so the sentence and the two values cannot disagree.

This is presentation only, and the boundary is firm: the finding's severity,
its weight, the penalty it contributed to and every count in the assessment
are the pipeline's and are untouched. Each recomputation is recorded in the
key record and shown in the review page as "as assessed" against "as
displayed", so a reviewer can see which figures moved and by how much.

### Choosing the offset

Candidates are whole-day offsets of either sign between `date_offset_min_days`
(zero by default) and `date_offset_max_days`. Nothing is excluded — not zero,
not whole years — for the reason given above. The pool is built in sorted
order and then shuffled with
`random.Random(f"{salt}:{policy}:dates:{pair_id}")`, the house determinism
style from `study_eval.sampling`.

The search makes **two passes over the same order**. The first accepts only an
offset that raises no advisory at all; the second accepts any offset that
passes the invariants. So an offset that leaves every relationship intact is
preferred to one that leaves only the stated ones intact, without either
becoming a requirement, and the choice stays reproducible from the seed. The
default candidate count exceeds the pool, so the search is exhaustive: a case
is never flagged merely because a sample missed the one offset that would have
worked. The whole pool costs well under a tenth of a second per case.

## Ages and measurements

One offset per pair and per unit, so the difference between the two reports —
what a discrepancy finding quotes and a reader checks — comes through
untouched. Also preserved: the precision as written (the pipeline's numeric
match grid is derived from the decimals), the pairwise match verdicts, the
sign, and the coarse age band. An offset that turns a 17-year-old into a
19-year-old is refused.

A zero offset is a candidate here too, and so is an offset that publishes one
report's age as the age the other report gave — for the reason in § An offset
of zero is a legitimate draw.

**The imperial-origin rule.** A weight stored as `47.63 kg` was `105 lb`
before somebody converted it. Shifting it by a kilogram produces a figure no
scale and no clerk would have produced, and the oddity draws the eye. Where
every value of a kind in the pair is consistent with a whole number of pounds
(or, for heights, inches), the shift is applied in pounds and converted back
at the original number of decimals. The decision is per pair and per kind,
never per value: shifting one side in pounds and its counterpart in kilograms
would break the difference between them.

## The residual scan

After every transform has run, the output is searched for anything
recognisably original. It assumes the transforms were incomplete, which is the
only safe assumption to make about them — and it has to do that without
crying wolf, because a report nobody can read through is a report nobody
reads. Four rules keep the difference between a finding and noise:

- **Dates are compared as whole tokens.** `2023` sits inside `2023-12-05`, so
  a substring search for an unchanged year-only date finds it in every
  complete date that shares its year. Dates are extracted as maximal tokens
  and compared as units.
- **Deliberately retained values are not leaks.** A country shown as reported,
  and the frozen context year beside it, are registered as unchanged mappings,
  which puts them in the published set the scan checks against. The scan
  reports an *untransformed* value, not a value the policy chose to keep.
- **A figure is not a date.** A co-occurrence count of 2019 reports is not a
  year. The fields holding counts, scores, contributions and weights are named
  in `NUMERIC_TEXT_FIELDS` and exempted from date-shape detection. They are
  still searched for original *values*, which is what would catch a date
  written into one of them by mistake.
- **A bare four-digit token is only a year inside the policy's era window.**
  Outside it there is nothing a reader could read as a date.
- **A value that is both an original and a published value is not searched
  for.** Finding it proves nothing either way, and published values may
  legitimately equal reported ones.

Cases are scanned with their **own** substitution table, never a merged one.
Each pair carries its own offset, so one case's original is routinely
another's published value, and a merged table reports that as a leak.

Run provenance — the run id, the commit, the render timestamp — is exempt
throughout: those are not anybody's values.

## Free text

Every string-bearing field of the view model has an explicit policy, and the
inventory is exhaustive by test — a field added to the model with no policy
fails the suite rather than reaching a page.

- **Pass through** — labels, severities, formatted figures. Nothing of the
  patient in them. Still scanned.
- **Substitute** — derived prose that quotes values: a finding's sentence, the
  deterministic summary, the panel's clauses, the frequency table's scope
  label. Rewritten through the case's table, longest key first so a composite
  is replaced before one of its parts.
- **Review** — human-authored interpretations, verbatim narrative snippets,
  and curator-written titles, ledes and kickers. These need a reviewer's
  decision — replacement text, approval as written, or withholding — or they
  block. Automatic redaction is never treated as sufficient. See § Three
  decisions, and why the cheap one is not copying.

A withheld block says so in the policy's own words rather than disappearing: a
reader must be able to tell "nobody wrote this" from "this was not
published", and a silently empty section says the first. The same applies to
`EvidenceCell.withheld`, which renders as `Withheld` and is distinct from a
`missing` cell, which renders blank.

## Three decisions, and why the cheap one is not copying

The review gate is **categorical**: it fires on every field of its classes,
not on evidence that one of them carries something. The policy cannot tell a
safe curator title from one naming a clinician or a town, and "this one
carries nothing identifying" is a conclusion a person reaches, not one the
transform can reach for them. So a collection of ten worked examples blocks
on twenty fields the curator wrote and, usually, nothing else.

That makes the *cost* of a decision the thing to get right, because almost
every one of them will be "this is fine". An `ApprovedText` offers three, and
exactly one must be chosen:

| | What is published | When |
|---|---|---|
| `replacement` | the reviewer's text, **verbatim** | the original cannot be published as it stands |
| `withhold` | nothing, and the page says so | there is nothing publishable in it |
| `approve_as_written` + `text_sha256` | the manifest's text, through the substitution table | it is already publishable |

**Copying the manifest's own words into `replacement` is the wrong habit**,
even though it is the obvious one. A replacement is published verbatim and
deliberately bypasses the substitution table — it is the reviewer's own
words, and rewriting them would make the page say something nobody approved.
So a title that quotes a report date keeps the *real* date, and the residual
scan is left to catch it as a `residual_original_value` finding. Approving
that same title as written puts it through the table like any other derived
prose, and the date comes out shifted.

The digest is what makes the approval an approval rather than a standing
permission on a path. Edit the manifest afterwards and `text_sha256` stops
matching, so the case blocks again with `approval_digest_mismatch`. Without
it, the stale approval would publish the text that *was* reviewed in place of
the text that is now there, and neither artifact would say so.

`--export-approvals` writes the form: one `approve_as_written` entry per
field that blocks, digests filled in, before the salt is read — so a reviewer
can be handed it before anyone opens the key store. It carries paths and
digests and not the text, because the artifact for *reading* the text is the
side-by-side review, which prints the originals and is written on every run.
The form comes out unsigned, with `approved_by` and `approved_at` empty, and
therefore does not load until somebody fills them in: a form that validated
as generated would make the signature a formality, and the signature is the
only thing the gate is actually asking for.

What a rationale is worth writing in that case is the check, not a
justification: which classes of material were looked for — dates, places,
identifiers, ages, quoted narrative — and where the words came from. The
field is read by no code, only by whoever asks later why human-authored text
left the secure environment.

## Fail-closed export

`write_publication_evidence` raises `PublicationBlocked` while any finding is
unresolved, and writes nothing — a blocked run leaves no artifact to be
mistaken for a cleared one. There is no `force` argument. The ways past a
finding are an `ApprovedText` — replacement text, approval as written, or a
withholding — or a `FindingResolution` naming a reviewer, a date and a
rationale.

The rendered scan reads the markup minus its static regions: the stylesheet,
any script, and HTML comments. Attributes stay, because a value composed into
an `id`, a class or a `data-` attribute is precisely the leak the model walk
cannot see; the stylesheet goes, because no value reaches it and its own
comments quote figures — one explains a spacing rule with *"2005 1.2" read as
one number*, which a pair whose year-only date of 2005 moved matched. A test
asserts the property rather than trusting it: every Jinja construct inside
such a region must be an include of a partial carrying no Jinja.

**A `FindingResolution` does not reach every finding.** `partition` is
applied to the findings the transform raised; the rendered-HTML scan runs
afterwards, in `verify_rendered_output`, and its findings are appended to the
report without passing through it. A `residual_original_value` reported at
`<rendered html>` therefore cannot be resolved by signature, only by the
value genuinely leaving the page. That is deliberate — it is the last check
before the supplement is written, and its whole value is that nothing can
wave it through — but it means a reviewer reading
`code [example_id at field_path]` has to look at the path to know whether a
resolution is even an option.

Finding codes fall into four groups: could-not-transform (a value that did not
parse, or for which no offset satisfied the invariants), needs-a-human (free
text), residual (something recognisably original still in the output after
every transform ran), and advisory.

## Reproducibility, and what is deliberately not recorded

The salt comes from `VIGITWIN_PUBLICATION_SALT` or the secure key store.
**There is no default**: a default salt would make every offset in every
supplement guessable from this source alone.

Two artifacts, deliberately written by two calls to two paths:

- **`ScramblingKeyRecord`** (`schemas/evidence_scrambling_key.schema.json`) —
  secure. Per case: the offsets, the full original → published table, the
  withheld and replaced paths, and every finding a reviewer was asked about.
  It names the salt by `key_id`, a keyed digest, and does not hold it: a file
  holding both the salt and the mappings it produced could regenerate every
  past and future supplement.
- **`PublicationTransformManifest`**
  (`schemas/evidence_publication_manifest.schema.json`) — travels with the
  supplement. The policy and its version, the `key_id`, the offset *bounds*
  for a methods section, per-class counts of values that moved, and the
  disclosure status. No offset, no mapping, no original value, no salt.

Recording the seed verbatim is right for a sampling draw —
`study_eval.records.SamplingRecord` does exactly that, because the protocol
requires the randomisation to be reproducible from the published record. Here
it would be the opposite: the published record would carry the means to
reverse its own transform.

## Side-by-side review

`write_side_by_side` writes `internal/`, `publication/` and
`disclosure_review.html`: per case, the two renderings to open together, and
then the three outcomes kept apart, because they are three different
questions —

- **Transformed — moved**: the original beside the published value, and how.
- **Transformed — unchanged**: the transform ran and the value came out as
  reported, or the policy chose to keep it. Not a failure, and on show as
  reported, so it belongs in the risk assessment.
- **Failed transformations and findings**: what no transform could reach, and
  what still needs a decision.

Above them sits the case's date profile — *n* of *m* date values moved — with
a highlighted statement where none moved, and a line saying whether the
countries on this page are aliases or as reported. Then the figures recomputed for presentation, the text
decisions, the advisories and the findings.

It is written whether or not the candidate is clear, because it is how a
reviewer clears a block.

## The page says MASKED before it says anything else

A publication page opens with a band carrying the word **MASKED** and one
line: *Select displayed values have been transformed.* It is drawn only in
publication mode, from `EvidencePageVM.mode`, and it is the first element
inside `<main>`.

The line says **select** values because that is what is true. A transform may
leave a value as reported — zero is a candidate offset like any other — and a
band claiming everything had moved would be the one piece of publication
metadata a reader has no way to check. What this run actually did, and the
caveat that the page is a candidate for disclosure review rather than a
document certified as anonymised, stay in the footer, which is built from the
effective policy.

It complements the footer rather than replacing it. The footer is the full,
policy-derived account of what this run did, and it stays; what it cannot do
is travel. A page gets screenshotted into a slide, cropped into a figure,
printed as the first sheet of a bundle, or read for ten seconds by someone
who never scrolls — and in every one of those a footnote at the bottom is
gone while the transformed values are still on show. The band is at the top
for that reason.

It is a band and not a tint behind the text. A diagonal wash over a page of
tables is what a print engine is free to drop and what a screen reader never
had, and this is the one claim on the page that must not be lost; the word is
capitalised in the markup rather than only by CSS, so a copy with its
stylesheet stripped still carries it. It says *masked*, not *anonymised*:
what ran is a transform, and whether the result may be released is a
disclosure reviewer's conclusion.

The side-by-side disclosure review carries no band. It prints originals and
says so — it is a secure-side artifact, and labelling it masked would be the
one wrong thing to say about it.

## The footer says what this run did

The publication footer is built from the effective policy rather than written
once as prose. A run that masks sources and keeps countries says so; a run
that masks both says that instead. A footer naming every attribute category
would be wrong for any run that left one of them as reported, and it is the
one piece of publication metadata a reader has no way to check. The same
sentence is recorded in the manifest as `transform_note`.

It prints originals, so it is a secure-side artifact and says so on the page.

## Running it

```
VIGITWIN_PUBLICATION_SALT=… python -m llm_dup_dev.review_html.evidence \
    --manifest collection.yaml \
    --mode publication \
    --output-dir outputs/publication/ \
    --side-by-side-dir secure/review/ \
    --key-out secure/scrambling_key.json
```

The secure outputs are written first and unconditionally. The supplement is
written last, and only if nothing is outstanding; otherwise the command
reports each finding and exits non-zero.

A first run on a fresh collection always blocks, because every curator-written
field needs a decision. The intended shape is two passes — the first writes
the form and the review page, the second publishes:

```
… --mode publication --export-approvals secure/approvals.json \
    --side-by-side-dir secure/review/        # blocks; writes both
# read secure/review/, fill in approved_by and approved_at
… --mode publication --approvals secure/approvals.json      # publishes
```

## Secure-side integration

This repository holds the generic transformation logic and synthetic
fixtures. The secure companion repository supplies, for a real study:

- the salt, from its key store, and the directory the key record is written to;
- the approvals file — the decision for each case's curator-written fields
  (usually `approve_as_written`, generated by `--export-approvals` and then
  signed), the resolutions for any finding a reviewer accepts, and any
  per-example country-masking overrides;
- the policy file, where the defaults are not wanted — the two masking
  switches in particular, which are independent: countries off, sources on;
- the curated manifest pointing at the real run's artifacts.

Nothing in that list is code. The transform, its validation and its gate all
live here and are exercised against synthetic data.

## Known limitations

- **Contextual frequencies are retained** as instructed, so a very small count
  in a narrow country-year scope remains a residual disclosure consideration
  that no transform here addresses. The reviewer sees the counts in both
  presentations.
- **Age, report-date and clinical-date offsets are drawn independently.** The
  layer validates the relationships the presentation displays; a quantity
  derived from two of them but not shown — an implied birth year, say — is not
  guaranteed coherent. The interval between an event and the report describing
  it is in this category by design, and the footer states it.
- **A relationship between a precise date and a coarse one may move.** A
  year-only value holds still under anything smaller than a year, so a
  day-level date can leave its span; `coarse_date_drift` names it, and a
  finding that states the separation still blocks.
- **A hard case may still take a small offset.** Where few offsets survive the
  invariants, the surviving ones can all be below the policy's floor; the
  search prefers the floor and reports `date_offset_below_floor` when it
  cannot reach it. Zero is tried last and is reported as
  `date_pattern_unchanged` when nothing else worked at all.
- **A separation may differ from the one the assessment computed**, by up to
  the configured tolerance and never across a severity boundary. The displayed
  figure is recomputed to match the published dates; a reader comparing a
  published supplement against an internal review will find the two figures
  differ, which is why both are recorded in the key record.
- **Aliases are per case.** `Country 1` in two examples need not be the same
  country, which is a disclosure property rather than an oversight: a reader
  cannot pool values across the supplement.
- **Retaining the country raises the stakes on the counts.** A real country, a
  real calendar year and a small contextual count together narrow the
  population considerably, and someone with database access could work back
  from them. The counts are retained by instruction and the country is retained
  by default, so this combination is the main thing a disclosure reviewer
  should be weighing; `mask_countries` and the per-example override are there
  for the cases where it is too much.
- **A country-year context bounds the date offset.** Where a report date sits
  close to a year boundary, few offsets keep the context year shared by both
  reports, so those pairs get less date obfuscation than others. The review
  page's date profile shows how many dates moved, which is where this becomes
  visible.
- **Case labels within a shared source are not aliased, because they are not
  presented.** The pair-level `SourceCaseAssessment` records *whether* the
  case labels were compared and whether they agreed, not what they were, and
  the evidence presentation is built from that assessment. If a future change
  surfaces the per-report labels, they will need their own alias series
  (`Case A` within `Source 1`) and their own policy entry — the
  `ValueKind` exhaustiveness test will say so.
- **The gate proves its own checks passed.** It does not certify anonymity,
  and no part of this layer should be quoted as though it did.
