# Publication mode for evidence presentations

The internal evidence review (`docs/evidence_format.md`) shows report values
as reported: original identifiers, country names, real dates, verbatim
narrative text. Every page says so in its footer, and it is not publishable.

Publication mode is a second presentation of the same cases, built from the
same view model and the same template, with the displayed values transformed.
It lives in `vigitwin.review_html.evidence.publication`.

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

## Threat model

The scheme has **one secret: the salt** in `VIGITWIN_PUBLICATION_SALT`, held
in the secure key store, never serialised, and named in artifacts only by
`key_id`. Everything else — this document, the invariants, the candidate
pools, the policy defaults, the code — is assumed public. That is not a
concession to the decision to publish; it is how the layer was built, and the
reason `PublicationSecrets` has no default salt is precisely that a default
would make every offset guessable "by anyone holding this source".

Stating who the scheme does and does not resist matters more here than in most
places, because a transform that preserves every relationship a reader needs
cannot also conceal the structure those relationships form.

### Four readers

- **A reader with no special access.** Sees transformed dates, ages and
  measurements and aliased identifiers. They cannot invert any of it: the
  offsets are drawn under the salt and no artifact that travels with the
  supplement records one.
- **A reader holding this source.** Anyone, once this is on the public
  mirror, and the layer assumes it already. They can enumerate the
  candidate offsets a block was drawn from, run the invariants over the
  *published* dates, and recover the set of offsets that would have been
  accepted. They cannot tell which member of that set was chosen — that is the
  salt's job, and the only thing the salt does. Where the set is small the salt
  is doing correspondingly little, which is what `date_offset_pool_thin`
  exists to report (§ How many offsets would have worked). **This is the whole
  of what publishing the algorithm costs.**
- **A reader holding the comparison database.** The strong adversary, and the
  one this layer does not defend against. They do not need to recover an
  offset. Every interval the adjudication reads is preserved exactly, by
  design, and so are the drug substances, the adverse event terms, the sex,
  the scores, the contextual counts and — by default — the country and
  calendar year. Those together are a strong fingerprint, and a database
  holder can match on them and then read the real dates out of their own
  records. No parameter in `PublicationPolicy` closes this, because the
  property being exploited is the property that makes the page evidence:
  a reader who cannot trust the intervals cannot read the discrepancies, and
  the footer has to tell them the intervals are faithful.
- **An insider with the key store.** Holds the salt and `ScramblingKeyRecord`,
  which is to say everything. Out of scope: the key record is the audit
  artifact and exists to be complete.

### What that means in practice

The dominant residual risk is **not** reverse engineering. It is matching on
the values that are retained on purpose, by someone who already holds the
database — and someone who holds the database has less need of the supplement
than anyone. For FAERS the comparison data is public, but so are the cases, so
a successful match links a published page to a record that was already public
and already de-identified. For VigiBase the comparison data is not public, so
the match needs access that makes the supplement redundant.

That asymmetry is the reason this layer is worth having and also the reason it
should not be oversold. Scrambling is a **disclosure-risk reduction measure
applied to data already held to be non-identifying**: it removes the easy
read-off, it makes a casual link materially harder, and it does not make the
page anonymous. The manifest and the footer both say so in those words, and
nothing in this document should be quoted as saying more.

### For anyone adopting this on their own data

The defaults are tuned for a large multi-national database and a reference set
drawn by country. Two of them deserve a second look on a smaller population,
and both are one field each:

- **`mask_countries` is off.** The argument for showing the country is that it
  is the population the counts belong to and, in a country-drawn reference
  set, already known from the design. Neither half of that holds for a
  national centre publishing from its own database, where the country is a
  constant and naming it narrows nothing *because* it narrows everything.
- **`min_contextual_count` is 5.** A small count in a named country-year is
  the combination most likely to identify, and it now blocks the export
  rather than sitting in a limitations paragraph. On a database of a few
  hundred thousand reports, a higher threshold is the cheaper instrument.

The gate proves its own checks passed. It does not know how large your
database is, and it will clear a page that is far more identifying on a
national dataset than the same page would be on VigiBase.

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
(thirty days by default) and `date_offset_max_days`. Nothing is excluded — not
zero, not whole years — for the reason given above. The pool is built in sorted
order and then shuffled with
`random.Random(f"{salt}:{policy}:dates:{pair_id}")`, the house determinism
style from `study_eval.sampling`.

The search makes **two passes over the same order**. The first accepts only an
offset that raises no advisory at all; the second accepts any offset that
passes the invariants. So an offset that leaves every relationship intact is
preferred to one that leaves only the stated ones intact, without either
becoming a requirement, and the choice stays reproducible from the seed.

The search is **exhaustive, and that is now enforced rather than asserted.**
`date_offset_candidates` caps how many candidates a band may offer, and the
cap used to be 2048 against a clinical band of 3594 — so the band was
sampled, and a sample drops whole-year multiples, which a mixed-precision
history is frequently solvable only at. Measured over twenty seeds it lost
four of the ten whole-year offsets on average and eight at worst; a case left
with none of them falls through the below-floor band, which holds no
year-shaped offset either, onto zero. The number existed to prevent exactly
that, and was set below the band it was protecting.
`PublicationPolicy.offset_band_sizes` now computes every band the policy
produces and the validator refuses a cap that would truncate one, so the
arithmetic the guarantee rests on is the arithmetic the pool uses. The whole
pool costs well under a tenth of a second per case.

### How many offsets would have worked

The accepted offset is the first in trial order, as before. What the search
also reports is **how many candidates survived** — because publishing the
method changes what a reader can work out. The salt chooses *within* the set
of offsets the invariants accept; it does nothing to hide the set. Anyone
holding this source and a published page can run the invariants over the
published dates and recover that set, so a case with three survivors narrows
the real timeline to one of three however good the salt is.

So the scan does not stop at the winner: it continues until it has seen
`date_offset_min_survivors` survivors (four by default), and a case that runs
out first takes a `date_offset_pool_thin` advisory naming the count. It stops
at the threshold rather than counting the set out, because the question is
only whether the pool is wide enough to be worth anything, and a wide pool is
the common case. The threshold is deliberately low: almost every real pair
mixes a year-only date with a complete one and is solvable only at whole-year
multiples, of which the clinical band holds ten, so a threshold near ten
would fire on nearly every case and tell a reviewer nothing.

The advisory does not block — nothing identifying has been left in the output
— and it sits beside `date_offset_below_floor` as a statement that the
obfuscation on this case is weaker than the policy asks for. Reporting a
weakness rather than hiding it is the only posture available once the
algorithm is public, and it is the one the rest of this layer already takes.

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

## Retaining the country

`mask_countries` is **off by default**, and the reasoning is in
`PublicationPolicy`: the country is the population the contextual counts were
judged in, and in a study whose reference set was drawn by country it is known
from the design, so masking it costs the reader the population and buys
little. A shared source identifier is decided separately and *is* masked by
default, because a named study or publication is a small and often public set
of patients rather than a population.

What the default leaves standing is the combination this document has long
called the main thing a reviewer should be weighing: **a real country, a real
calendar year and a small contextual count.** The counts are retained by
instruction, so nothing upstream objects; and the counts **include the two
reports under review**, so a co-occurrence count of two in `SE, 2024` is this
pair and nobody else. Someone with database access can work back from it.

That used to be a paragraph in § Known limitations, which is not where a
reviewer is looking when they clear a case. It is now a check.
`retained_country_small_count` fires where a scope that *names a country*
shows a count at or below `min_contextual_count` (five by default, the
conventional small-cell threshold; zero switches it off), and it **blocks**.

It blocks rather than advising because, unlike the date advisories, there is
something a reviewer can do:

- mask this one example's country with a `CountryMaskingOverride`, which
  removes the half of the combination that narrows the population; or
- sign a `FindingResolution` saying why the population is wide enough.

Whether a remedy exists is the line between a blocking finding and an advisory
throughout this layer — `date_offset_pool_thin` and `date_pattern_unchanged`
advise because nothing can be done about them, and `human_text_review_required`
blocks because something can. The global scope is not reported, because the
population a global count narrows is one the page never names; § Known
limitations says what that leaves open.

## The residual scan

### What is scanned

Three renders, because a page written is a page that must be read back.

Each case is rendered as the writer renders it — with the collection's title,
intro, note and transform note, which the probe page used to leave out while
the writer put them on every page — and scanned with that case's own table.

The combined document is then rendered once and scanned in slices: each
case's `<section>` with that case's table, and the shared front matter and
footers with the union of every case's table. Never one merged table over the
whole document: cases disagree by construction, each pair having its own
offset, so a value case B published is routinely a value case A reported, and
a merged table would report B's legitimate published date as a leak of A's.
The union is safe for the shared regions only because it drops any original
that is also somebody's published value — the same compromise the page-level
scan already lives with.

If the document's case-section count ever disagrees with the transform count,
the scan raises rather than carrying on: a misaligned slice would quietly
check the wrong case.

### What it looks for

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

## Three decisions per field, and why the cheap one is not copying

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

**The finding names the digest to put back**, because the remedy is a sha256
and the alternative is reimplementing `digest_text` by hand — including the
part where a string and a one-item list digest differently, so a
`paragraphs` field is a *list* and digesting the joined text gives the wrong
answer. Re-run and the message says `set text_sha256 to <value>`; the
side-by-side review page carries it as well, beside the old pin.

Regenerating the form does **not** refresh it, and that is the point: the
merge keeps signed entries untouched, so an edited field needs a human to
look at the new text and either repin it or decide again. An automatic
refresh would re-approve prose nobody had read, which is the hazard the
digest exists to create friction against.

`--export-approvals` writes the form. It carries paths and digests and not
the text, because the artifact for *reading* the text is the side-by-side
review, which prints the originals and is written on every run. The form
comes out unsigned, with `approved_by` and `approved_at` empty, and therefore
does not load until somebody fills them in: a form that validated as
generated would make the signature a formality, and the signature is the only
thing the gate is actually asking for.

**Two lists, built at two different times**, because the two decisions become
knowable at different points in the run:

- `approved_texts` — one `approve_as_written` entry per free-text field that
  blocks. Every `REVIEW` field blocks categorically, so this list is derivable
  from the page alone and is written *before the salt is read*. A reviewer can
  be handed it before anyone opens the key store, and a run that dies for want
  of a salt still leaves it behind.
- `resolutions` — one entry per finding a check actually raised, which is
  knowable only after the transform. The form is written a second time once
  the run has its report, and the two writes merge. Entries are regenerated
  for findings a reviewer has **already resolved** as well as for outstanding
  ones: a working resolution moves its finding out of the unresolved list, so
  a form built from that list alone would report every resolution that is
  doing its job as orphaned — which reads as "obsolete, delete me", and
  deleting one re-blocks the run on the finding it was clearing.
- `release` — the one decision about the whole document (§ Releasing the
  document). Scaffolded with its digest filled in, and only once nothing else
  is outstanding, because a release signed over a candidate that is about to
  change would pin a document nobody will publish. Left unsigned it loads as
  *no release*, rather than as an error: a candidate is the ordinary state,
  and a form that became unloadable the moment it offered a release would
  stop the sign-then-re-run loop dead.

`country_masking` is always empty, and that is not an omission: `mask: true`
and `mask: false` are both decisions, so there is no neutral entry to
generate. Pre-filling either arm would make the choice on the reviewer's
behalf — and for `retained_country_small_count`, where masking is one of the
two remedies, generating only the signature arm would quietly recommend
publishing the country. The masking remedy is named in the generated entry's
`rationale` instead.

**Two findings get no resolution entry**, because for them a resolution is
not a remedy. The free-text codes already have an `approved_texts` entry, and
a resolution would be a second, weaker way past a field whose whole point is
that it must be decided on its actual words. Anything reported at
`<rendered html>` is appended after `partition` has run (§ Fail-closed
export), so an entry written against one would never be consulted —
generating it would read as an available remedy that silently does nothing.

### Re-running the form

A second run is the normal case rather than the mistake: findings change as
approvals land and as the manifest is re-curated, so the form a reviewer
needs is rarely the one generated first. Re-running merges, under one rule —
**a signed entry is a record and is never touched; an unsigned entry is
scaffolding and is regenerated.** Keeping an unsigned entry would hand back a
digest that no longer matches anything, so the form would be guaranteed to
fail on load; rewriting a signed one would move the subject out from under a
signature. Entries the run no longer asks for are kept and counted as
orphaned rather than dropped — a reviewer's record is not ours to discard
because the manifest moved on, and it may be the only trace of a decision
about a case that has since been removed. An orphaned count therefore means
something specific: a field path moved, or an example left the collection.
It is worth looking into rather than tidying away.

The release merges under the same rule, as a single object rather than a
list: signed, it is kept; unsigned, it is replaced along with its digest. A
write that was given no digest did not look at the release at all, so it
passes whatever is there through untouched — which is what the pre-transform
write does, and why it does not report a signed release as orphaned.

`--force` still regenerates from scratch, and now really does discard
signatures. It is the deliberate reset, not the way to get a second form.
Each run prints what it did (`3 new, 5 signed kept, 1 unsigned refreshed, 0
orphaned`), because the exit code says nothing about whether the signatures
survived, which is the only question a reviewer has about a merge.

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
withholding — or a `FindingResolution` naming a reviewer, a date, a rationale
and a digest of the finding's detail.

**A resolution is pinned the same way an approved text is, and for the same
reason.** `code`, `example_id` and `field_path` all survive a re-curation
that changes the risk: the signature given when a country sat beside a count
of 5 would go on clearing the same finding when the count becomes 2. So
`detail_sha256` pins what the reviewer actually weighed — the finding's
`detail` carries the specifics — and a changed detail voids the signature,
exactly as an edited manifest voids an `approve_as_written`. The finding then
lands wherever it would have landed unsigned, and a
`resolution_digest_mismatch` advisory says so and carries both digests, so
re-signing does not mean computing a sha256 by hand.

That advisory is deliberately advisory rather than blocking. A voided
signature withholds nothing on its own: where it failed to clear a blocking
finding, that finding is already doing the withholding, and where it failed
to clear an advisory, a stale signature must not leave the run worse off than
never having signed at all.

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

### Releasing the document

Everything above is about one field or one case. The release is about the
whole document, and it is the only thing that removes the candidate caveat
from a published page — the footer stops saying *"a candidate for disclosure
review, not a document certified as anonymised"* and the manifest's statement
changes with it.

It is a `ReleaseApproval` in the approvals file: a name, a date, a reason,
and `candidate_sha256`. There is no command-line flag for it, and the reason
is worth stating because the flag is the obvious design. A flag records
nobody — and every other decision in this layer carries who decided and when
— and a flag set once in a deployment script would keep the candidate wording
from ever appearing again. The pin earns its place the same way
`FindingResolution.detail_sha256` does: without it, a signature given for one
document goes on clearing a later, different one.

It costs the signer almost nothing. They are already in that file putting a
name and a date onto every text approval, and `--export-approvals` scaffolds
the release with the digest already computed, so the two fields they fill are
the two they are filling anyway. The form holds it back until nothing else is
outstanding, because a release signed over a candidate that is about to
change would pin a document nobody will publish.

**Two refusals, and both raise rather than quietly falling back to the
candidate wording** — which would hand a signer a candidate document they had
asked to release:

- **A stale pin.** The document changed after it was read, so the signature
  was given for something else. The refusal names the digest the current
  candidate hashes to, so the value is readable from the terminal, and
  passing `--export-approvals` **resets the release block** to an unsigned
  skeleton carrying it.

  That reset is the one place a signed entry is not kept, and the asymmetry
  with an edited text approval is deliberate. A text approval covers one
  field, so a stale one leaves a reviewer something to compare against and
  re-pin. A release covers the whole document, and a changed document is a
  different one — repinning in place would leave a record saying the reviewer
  read the new document on the day they read the old one. So releasing again
  is a fresh decision, and it needs a fresh name and date. The superseded
  signature stays in the previous run's key record.
- **An unsigned advisory.** `date_pattern_unchanged` is advisory, so a run
  can publish a pair's real timeline, print one line to stderr and succeed —
  and a release would then call that *cleared for public sharing*. Advisories
  do not block the ordinary gate, by design: reporting a weakness beats
  hiding it, and these are inferences rather than leaks. A release is
  different, because it is a person asserting the document may go out, and
  nobody can assert that about a weakness they have not seen. An advisory a
  reviewer *has* accepted is no longer an advisory — it moves to
  `report.resolved` through a signed `FindingResolution` — so the requirement
  is not "no weaknesses" but "a signature on each one".

The digest covers the transformed content with `generated_at`, `provenance`,
`mode` and `approved_for_release` left out. The first is a wall-clock stamp
and would void every release on the next run; the second would void one on
any commit between signing and re-running, which is not a change to the
document; the last two are what the release itself decides, so pinning them
would make signing change the thing signed. The first two are exactly what
the residual scan already exempts, so the pin covers precisely what this
layer already treats as content.

**Who released it is recorded in the key record, not in the supplement.**
The public manifest carries `approved_for_release` and `released_at`. The
reviewer's name and their rationale stay secure-side: a rationale may name
real cases, exactly as a finding resolution's may, and publishing an
individual's name in an automated artifact is a disclosure decision of its
own that belongs in the manuscript.

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
  for a methods section, and the disclosure status. No offset, no mapping, no
  original value, no salt.

  It used to carry per-class counts of the values transformed, aggregated over
  the run. Those are gone. They were the one field here that gave a reader of
  the *manifest alone* something the supplement did not already give them: a
  reader holding the pages can see every transformed value and very nearly
  count them, so the counts added nothing for them — while for a single-case
  supplement the run-level aggregate is that case's own profile, and
  `date:day → 16` is close to a fingerprint. A field that is derivable where
  it is useful and identifying where it is not is a field worth losing.

Recording the seed verbatim is right for a sampling draw —
`study_eval.records.SamplingRecord` does exactly that, because the protocol
requires the randomisation to be reproducible from the published record. Here
it would be the opposite: the published record would carry the means to
reverse its own transform.

## Side-by-side review

`internal/` and `publication/` each hold the **combined document** as well as
the per-case pages, with the collection's own intro and note. That is not a
convenience. A release is a decision about the whole compiled document, and
the published copy of that document is written only by a run that has
already cleared — so before the decision is made, which is every run that
matters, it exists nowhere else. This directory is written unconditionally,
blocked run or not, which is why the document a reviewer signs for lives
here.

The review page links both copies at the top and names the digest the pin
must carry, so the two things a reviewer needs — the document, and the value
that attaches their signature to it — are in the artifact they already work
from. If a signed release has gone stale, the page says so there too.

The `publication/` subtree states whatever is true of the run — candidate
before a release is signed, cleared after. It once showed the candidate
footer always, on the reasoning that somebody deciding whether to release
should not be shown a document claiming to have been released; that cannot
arise, because the footer says released only when a signed release pins this
candidate, and by then the decision has been made. What it did instead was
put two artifacts on disk making contradictory claims about one document.

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

### Once per page, or once per example

A single page carries one band, the first element inside `<main>`, which is
what makes it survive a screenshot or a crop.

A **collection** carries one at the top of each example and none on its cover
sheet, which follows from how it paginates: every example opens a fresh
sheet, so a page-level band would sit alone with the title, intro and note,
above no example at all. One per example puts it where a reader of any
example meets it.

Per example rather than per sheet, deliberately. An example is the unit
somebody lifts out of a supplement, and one band per example costs a tenth of
what one per sheet would cost in a collection of any size. A band repeated on
every sheet would also have to be a fixed-position element, like the
per-sheet footer, which means another ~12mm off every sheet's content area
and a coloured wash across a document that has been cleared for publication
— which reads as a draft, and a page of warnings is a page nobody reads.

**The cover sheet has no band, and its prose is not untransformed.** The
collection's intro and note go through the substitution table like any other
derived text, so they may carry transformed values. What says so on that
sheet is the per-sheet footer, in words rather than in colour. Keeping a band
there for the screen and hiding it in print is not available: no rule inside
the print block may hide content, and a test holds that line.

The per-sheet footer is what covers the sheets inside a long example as well.
In the candidate state it says so outright; once released it carries the
collection's title *and* that displayed values were transformed — because
clearing a document settles the disclosure question and leaves the
interpretive one, and a reader must not take a shifted date for a real one.

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
VIGITWIN_PUBLICATION_SALT=… python -m vigitwin.review_html.evidence \
    --manifest collection.yaml \
    --mode publication \
    --output-dir outputs/publication/ \
    --side-by-side-dir secure/review/ \
    --key-out secure/scrambling_key.json
```

The supplement directory holds a page per pair, an `index.html` linking them,
the combined `combined.html` — written by default, and named by the manifest
so the two cannot drift — and the manifest itself. `--no-single-document`
skips the combined document; `--single-document PATH` writes an extra copy
wherever you point it, which the manifest deliberately does *not* record,
because a public manifest should name no filesystem location.

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

Pass both `--approvals` and `--export-approvals` at the same path to do it in
one loop: the run reads the signed file, and writes back a merged form
carrying whatever is still outstanding. That is the usual shape after the
first pass, because clearing one finding routinely reveals the next.

**Drop `--export-approvals` on the final run.** By then it can add nothing —
anything still missing would have blocked the run rather than let it be the
final one — and leaving it off means the run that produced the supplement
read the signed file without rewriting it. That file is the record of who
decided what, so the archived copy is better left exactly as the reviewer
saved it; the form writer reformats what it merges, which is harmless and
still a change to a file worth being able to diff against itself. Keeping the
flag on is safe, and the summary line it prints —
`0 new, N signed kept, 0 unsigned refreshed, 0 orphaned` — is a last check
that nothing was outstanding and no signature was voided. Either is
defensible; the published supplement is identical either way, bar the render
timestamp in its footer.

## Secure-side integration

This repository holds the generic transformation logic and synthetic
fixtures. The secure companion repository supplies, for a real study:

- the salt, from its key store, and the directory the key record is written to;
- the approvals file — the decision for each case's curator-written fields
  (usually `approve_as_written`), the resolutions for any finding a reviewer
  accepts, any per-example country-masking overrides, and the document-level
  release once a reviewer signs one. Three of the four are generated by
  `--export-approvals` and then signed; the country-masking overrides are
  written by hand, because there is no neutral masking entry to generate;
- the policy file, where the defaults are not wanted — the two masking
  switches in particular, which are independent: countries off, sources on;
- the curated manifest pointing at the real run's artifacts.

Nothing in that list is code. The transform, its validation and its gate all
live here and are exercised against synthetic data.

## Known limitations

- **The per-sheet footer carries static text and no page number.** It is a
  fixed-position element, which is the only thing a browser repeats on every
  printed sheet; `@page` margin boxes and `counter(page)` are not reachable
  from one. A long collection title is clipped to the single line the band
  allows.

- **Contextual frequencies are retained** as instructed, so no transform here
  addresses a small count. A small count in a scope that *names a country* now
  blocks the export (`retained_country_small_count`, § Retaining the country),
  which is a decision forced rather than a risk removed. A small count in the
  **global** scope is not reported: the population it narrows is one the page
  does not name, so the country half of the combination is absent — but a
  global co-occurrence count of two is still two reports worldwide, and the
  reviewer sees the counts in both presentations.
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
- **Retaining the country raises the stakes on the counts**, and this is now
  checked rather than described. See § Retaining the country below: the
  combination blocks the export at or below `min_contextual_count`, and
  `mask_countries` and the per-example override are the remedies the finding
  names.
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
- **A thin surviving set is a real residual risk, and the salt does not
  address it.** The salt chooses among the offsets the invariants accept; it
  does not conceal which offsets those are, and a reader holding the method
  and the published page can recompute the set. `date_offset_pool_thin`
  reports a case whose set is small enough to enumerate, but reporting is all
  it does — the remedy is a reviewer's judgement, or a wider range for the
  block in question.
- **The gate proves its own checks passed.** It does not certify anonymity,
  and no part of this layer should be quoted as though it did.
