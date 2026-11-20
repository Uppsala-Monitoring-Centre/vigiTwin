# The evidence review format

There are two HTML review formats, and they answer different questions.

The **detailed** format (`docs/integration_guide.md` § Generating HTML review
pages) exists so that someone working on the method can see everything: raw
JSON panels, internal feature identifiers, screening features verbatim, and
side-by-side values coloured by whether they differ. It is built for
annotation, debugging and precision testing.

The **evidence** format exists so that someone who did not build the method
can see *why vigiTwin decided what it decided*. It shows the same two reports,
but every colour on the page is a statement about the stored assessment, and
anything the assessment did not speak to is left neutral.

Both are generated from the same saved-run artifacts. Neither reaches a
database. They write into separate output directories, and the detailed
format's behaviour and output are unchanged by the existence of the second
one.

---

## The colour rule

**Colour follows the assessment, not the diff.** The evidence builder performs
no comparison of its own: it never asks whether report A's value equals report
B's. Every colour is a join from a stored record back onto the element that
record names.

| Colour | Meaning | Source records |
|---|---|---|
| **Green** | The assessment counted this **for** the pair | `matching_drugs`, `matching_AEs`, `anchor`, `feature_contributions` |
| **Red** | The assessment counted this **against** the pair | `inconsistencies` (not suppressed), `clinical_date_findings`, the anchor population adjustment |
| **Neutral** | Everything else | — |

Red covers two things, and a retained inconsistency is only the commoner of
them. The other is the **anchor population adjustment**, `-log10(C(N_anchor,
2))` — the multiplicity correction. A larger anchor population means more
report pairs that could have matched by chance, so the term pushes the
matching score down, towards independence. It is not a finding about anything
either report said, but it told against the pair, and showing it neutral said
it was not part of the argument.

The rule is a rule, so it holds wherever a figure appears: the same adjustment
is red in the panel at the top of a case and in the section-2 contributions
column, and the contributions themselves carry the sign colour too — a
positive term green, the column's total by whichever way it came out. A total
that comes out negative (the floor case) is red, correctly: at that point the
evidence no longer makes a chance match unlikely at all.

What the widening does *not* reach is anything that told neither way. A
suppressed finding contributed exactly zero and stays neutral; so does a term
of exactly zero, which is why a two-report anchor shows no adjustment row
rather than a grey one.

"Everything else" is the category that does the work, and it is deliberately
large. It holds agreements that were never scored, differences vigiTwin did
not record as inconsistencies, values absent from one or both reports, values
withheld by a later confidentiality transformation, and findings that were
suppressed.

Consequences worth stating, because each is a case where a diff-coloured page
would say something vigiTwin did not:

- **A drug or adverse event present in only one report is neutral** unless a
  finding names it. One-sidedness is not by itself evidence against the pair:
  reports differ in completeness all the time.
- **Two identical report dates are neutral.** `report_date` is excluded from
  the probability computation, so agreement there earns no evidential credit.
- **A shared country is neutral.** Country defines the reporting context — it
  selects the comparison population the rarity calculation runs against — and
  contributes no term of its own. (Structurally: `patient_country` has no
  entry in the domain-size map, so it can never yield a contribution.)
- **A retained inconsistency stays red on an `Unrelated` pair**, where the
  relationship question was never reached. The finding was still recorded;
  what did not happen is the weighing of it.
- **Red is not a severity.** The anchor adjustment is red and is not a
  finding; a minor inconsistency is red and a decisive one is no redder. What
  a red element weighed is in the figure beside it, never in the shade.
- **Green on a drug or AE term is coarser than it looks.** The drug set
  contributes one figure, not one per substance, so green on a term means
  "member of a shared pattern that contributed", not "this term contributed
  +X decades". The page's legend says so.

### Suppression and downgrading are different mechanisms

They are easy to conflate and must not be, because they colour differently.

| | Suppression | Downgrading |
|---|---|---|
| Record type | `Inconsistency` only | `ClinicalDateFindingSummary` only |
| Marker | `suppressed` (+ `suppressed_by`) | non-empty `downgrades` |
| Set by | the LLM AE clinical-picture assessor | the clinical-date module |
| Effect on the penalty | **zero** | **counted, at the reduced severity** |
| Colour | **neutral**, listed under *Superseded findings* | **red**, at its *final* severity |

A suppressed finding is neutral but not invisible: it appears in its own
subsection, so a reader can see that something was considered and set aside
rather than never noticed.

---

## Page structure

One report pair is one `<section class="case">`. A document holds one or more
of them, which is how a single page and a multi-example collection are the
same template.

A case opens with its title, optional human-authored lede, and a row of
outcome tiles — the reference label (with the set it came from), the screening
outcome, the vigiTwin classification, and the SCLE state. These are four
separate verdicts and the page keeps them separate; none is ever inferred from
another. A tile is muted only where there is **no verdict to show**: an
inconclusive SCLE assessment — somebody looked and found the reference label
genuinely ambiguous — reads as a value, because it is one.

Directly beneath that row, and nowhere else, is the line saying whether
vigiTwin **agrees with the reference label**. It compares two of the four
verdicts printed immediately above it, so it belongs to that row; below the
decision tiles — where it used to sit — a reader met it a stage later than
the thing it describes, and read it as a comment on how vigiTwin decided
rather than on what the tiles say. Agreement is computed in the binary space
the reference label lives in, under a named positive-class rule, and an
abstention on either side yields no verdict at all.

Then the **decision tiles** — one per stage, Independence and Relationship —
which are the most prominent thing under the heading, and new. The outcome
tiles above them are verdicts from four different places and say nothing
about how vigiTwin reached its own; these say it. Each carries the outcome, a
badge naming the **pathway** the decision took, the evidence that drove it
where that is not already the badge, and the threshold or tolerance clause.

**They are coloured by which way the decision went, not by which route it
took** — green where the outcome counts towards the reports being one case,
red where it counts against, neutral for an abstention or an absence. That is
the page's one colour rule, the same one the tables follow, so a reader who
has learned it does not learn a second. A badge coloured per pathway said
that a shared-source decision and a rarity decision were different *kinds* of
thing, which is not the distinction a colour is needed for.

**An absence of evidence is not written out as a sentence.** Where a signal
contributed nothing, the page says so once, as a value, in the place a reader
already looks for that signal: "Additional matching features — None" and
"Inconsistencies — None" in the panel, `None` under the Inconsistencies
heading in section 3, a muted tile where there is no verdict to show, a blank
cell where nothing was reported. What it no longer carries is prose asserting
the absence — that no explicit inter-report case link was found, that no
additional matching feature contributed, that vigiTwin recorded no
inconsistencies for this pair — which reads like a result while reporting
none, and takes a paragraph of a reader's attention to say nothing happened.
The generated summary follows the same rule: a slot with nothing in it is
dropped rather than filled with a sentence about nothing.

The underlying fields are unchanged on the view model, because the *kinds* of
absence differ and a consumer may need to tell them apart; it is the page
that stops printing them. A heading never goes with its sentence: section 3
keeps its *Inconsistencies* heading and answers `None`, so every case in a
supplement has the same skeleton and a reader comparing two of them finds the
same question in the same place.

The **Limitations of this review** list went the same way, and this is the
one place the rule has a cost worth stating. Every entry on it named
something the stored artifacts could not support, and most of those were
plain absences of input — no frozen frequency statistics, no run manifest.
But one was not: `score_unreconciled` says the contributions on *this page*
do not add up to the score the run stored. That no longer appears anywhere a
reader can see. It is still recorded on `EvidenceCaseVM.gaps`, with its code
and its message, where a caller, a test and the publication gate read it, and
a consumer that cares must read it there.

Both tiles always appear, in the same shape, whichever route each took. That
is the point of them: direct evidence of a shared source decides as firmly as
a rarity calculation, and a layout giving the arithmetic a panel and the
source context a sentence in passing told a reader otherwise. A reader
comparing two cases finds the same two tiles in the same place and reads the
difference off the pathway badge.

The pathways are `Shared source`, `Individual ids`, `Explicit reference`,
`Probabilistic matching`, `Inconsistency assessment`, `Not reached`
(independence was not rejected, so the relationship question never arose) and
`Not recorded` (no classification was saved).

`Shared source` and `Individual ids` are kept apart because a reader needs
both halves of that route. A shared source is what made the comparison
possible and is what rejects independence; the case labels *within* that
source are what the relationship turned on. So a pair sharing one trial
registration and naming different cases within it reads `Shared source` on
the independence tile, green, and `Individual ids` on the relationship tile,
red — which says in two badges both where the evidence came from and which
way it pointed.

They are *described* from what the run stored —
`independence_reason`, `relationship_assessment`, the penalty's `threshold`
and the source verdict — and never re-derived, so a tile cannot reach a
different answer from the classifier's.

**The outcome is named, not spelled.** `relationship_assessment` holds
`Same_case` and `Different_case`; the page shows **Same case** and
**Different cases**. The plural is deliberate — two reports found not to be
one case describe different cases — and the same table names the outcome in
the assessment section, so the page calls one outcome one thing. The stored
field is unchanged, and the data-model documentation quotes it as it stands.

Then the **at-a-glance panel**: the assessment as figures, in the order the
assessment itself proceeds — reporting context, anchor and anchor population,
the additional matching features with their contributions, the matching
score, the scored inconsistencies with their weights, and the inconsistency
penalty. The figures only: why each settled the question, or did not, is the
decision tile above, said once. It sits directly above the written summary so the same assessment is
available in figures and in prose, adjacent, and a reader meets the
conclusion before the evidence rather than assembling it from three sections
of tables.

Each piece of evidence is a **chip**, so the panel reads as countable items
rather than as a sentence, and both sides carry a figure in the same units —
`Female +0.3` against `31 vs 30 years 0.4`. Chips take one green and one red
and are never shaded by magnitude: colour says which way a piece told, the
figure inside says how much. Shading by size would make a pale chip and a
small number the same statement twice, and would put the page's one
quantitative claim somewhere a reader cannot measure.

A chip carries **the values and the weight, and nothing said about them**.
What feature a finding is about, what severity it was counted at and how the
assessment described it are three columns of the findings table in section 3;
repeating any of them here spent a line of the page on words the reader is
about to be given anyway. So `Adverse events: infection listed on report A
without a matching HLT on report B.` is `infection`, a drug-set finding is its
substances, a clinical-date finding is its two dates, and a repeated unit is
said once — `31 vs 30 years`, not `31 years vs 30 years`. A unit is factored
out only where both sides are bare figures once it is removed, so two country
names that happen to end alike are never spliced together.

A finding's weight is **unsigned** in a chip — `0.4`, not `+0.4`. Every
retained finding counts against the pair, which is what its colour says, and a
`+` beside a red figure reads as a contradiction. A contribution keeps its
sign, because the sign there is information: the anchor adjustment is the one
negative term in that column. The findings table keeps the signed form too,
where the weight sits among other figures.

The penalty row states **the total**, not the arithmetic behind it. `3 × 0.4 =
1.2` stood there until the chips above began carrying each finding's own
weight; with the terms listed a row above, a multiplication restating them was
the same sum twice.

The context row names the **population**, not the level — `SE 2003` rather
than `Country-year`. The level says how the population was chosen; a reader
comparing two cases wants which one it was. The country is shown exactly as
the run recorded it, usually an ISO code, because expanding a code into a
country name is a mapping this package has no business owning. It is a cell,
like any reported value, so the publication phase masks or substitutes it.

It carries no `Classification` row: the outcome tile says that a dozen lines
above, and a third statement of one label on one screen is noise. What it does
carry, under the two decision figures, is the clause that explains them — the
independence threshold and the zone, the penalty against its tolerance *named
with the zone that tolerance belongs to*, or the reason no comparison was
made. There is no single same-case threshold: the tolerance varies with how
strongly independence was rejected, so `below the same-case threshold of 4.0`
would leave a reader comparing two cases to find two different "the"
thresholds with no stated reason for either. The clause reads `below the
same-case threshold of 4.0 for the compelling range`. A pair whose
independence was decided on direct evidence rather than on rarity has no zone,
and none is named. Those clauses used to sit in a decision table
at the end of section 3. That table is gone; without the clauses travelling
with the figures, a case presented with a human interpretation in place of the
generated summary would state no threshold anywhere on the page.

The panel holds no copy of the contributions or the findings. It renders the
same objects sections 2 and 3 render, so the two cannot come to disagree, and
one publication rewrite of a value reaches both. `EvidenceCaseVM.assessment_rows`
is still built and still on the view model for consumers; it is simply no
longer how the page says this.

The screening tile has three states, and the third is not the second. It reads
**Flagged** or **Not flagged** only when the pair's screening entry carries an
explicit boolean verdict, and **Not evaluated** otherwise. The verdict is read
from a named key — `IsSuspectedDuplicate`, `screening_retrieved`, `retrieved`
or `flagged`, or whatever `screening_verdict_key` names — and never from an
entry merely existing: an export may carry a row per pair *considered*, in
which case a present entry can mean "not flagged". A key holding something
other than `true` or `false` is reported as a gap rather than coerced.

Then three numbered sections, named for the two questions vigiTwin answers
rather than for the material each happens to hold:

1. **Report comparison** — patient characteristics, drugs, adverse events,
   narratives.
   The characteristics carry the **overall adverse-event onset** each report
   yields, which is the date the case-level onset check actually compares.
   Without it a finding about that date named two values printed on no table.
   The value is the pipeline's own `effective_overall_ae_onset_date`, not a
   second reading of the reports, so the row cannot show a different date
   from the one the assessment used — and where the pipeline *derived* it
   rather than reading it off the report, the cell says so beneath the
   value, because the column is headed "Report A" and a derived date is not
   something report A stated.
   Event-level onset dates sit **under their own term** in the adverse-event
   table, as a drug's dates sit under its substance. They had a second table
   keyed by term, which made a reader match an event against another row to
   learn when it started — something the drug table never asked of them. The report values, coloured by the rule above. The page carries
   a two-line legend: the colour key, and the one glyph key a reader cannot
   infer — a blank cell is an absence of reported information. The caveats
   that colour is not severity and that a green term is a set member rather
   than a scored term are stated here, in this document, and not on every page
   of a supplement; so are the ordering rules and the rest of the rationale.
   Nothing on a page depends on hovering: every tooltip repeats evidence one
   of the tables also names, and no print rule hides content, so a printed
   copy loses nothing. That is a property the renderer tests hold to, rather
   than a claim the page makes about itself.
2. **Independence assessment** — the context table, the shared drug and AE
   terms spelled out, and the per-feature contributions in decades, totalled
   (below). The two shared sets sit **side by side**, drugs left and adverse
   events right, in the column order of the counts table directly beneath
   them; stacked, a reader held the drug set in mind while scrolling past the
   AE set to reach the row of numbers that counts both.
   Two paragraphs used to open this section: one naming the reporting context
   and why it applied, one naming the anchor and its size. Both described the
   table directly beneath them, so a reader's work was to match a sentence
   against a row. The **table says it itself**: the scope rows are named for
   the population they are — `Canada, 2022` rather than `Country-year`, since
   the level says how the population was chosen and not which one it was — the
   row the rarity was judged in is shaded, and the count that became the
   anchor population is boxed. Which of the shared sets defined the anchor is
   the panel's `Anchor` row, so the set list no longer captions it either. The
   shading and the box are decoded by one sentence under the table, and that
   sentence is omitted where no row is marked, which happens when the frozen
   bundle holds no statistics for the selected scope.
   A column whose set is **empty is left blank**. "How many reports carry
   every term in this set" has no answer for a set with no terms — the stored
   count is the whole scope, which is not a co-occurrence of anything — and
   printing it invited a reader to weigh a population size as evidence.
   *Both sets* goes blank as soon as either side does, since it is then a
   restatement of the one that is left rather than an intersection of two.
   With neither set shared there is nothing to count and the table does not
   appear at all; that is not recorded as a gap, because nothing is missing.
   `frequency_stats_missing` means the frozen bundle was absent.
   Neither mark is green or red. A population size is not evidence for the
   pair or against it, and spending one of the page's two colours on it would
   make the colour rule mean two things.
   `EvidenceCaseVM.context_note` and `.anchor_note` are still built and still
   on the view model for consumers; the page simply no longer prints them. One
   sentence did not survive the move: the explanation that a larger anchor
   weakens the evidence from an identical match. It is method, not evidence
   about this pair, and it lives here and in the panel's adjustment tooltip. Each
   contribution row names the value that earned it, not the kind of value: the
   matched date rather than "Clinical date", `Female` rather than "Sex", and
   the set members rather than "shared drug set". The value stands alone — a
   date never carried its kind, and the demographic values do not need theirs
   either. Where a run recorded no value, the kind is all there is and it is
   shown by itself. Where the two reports
   hold different values for a feature the run still scored as matched — an
   age within tolerance — both are shown, because neither is "the" matched
   value.
3. **Relationship assessment** — the inconsistencies, which are the evidence
   the relationship question turns on. The retained findings with their
   domain, severity and the element each names — and, where the finding is
   about a pair of values, those values rather than the difference between
   them: `31 years vs 30 years`, not "Difference of 1 years." The two are the
   very cells the characteristics table renders, so a finding cannot come to
   quote a value the table above it no longer shows. A finding with no single
   pair behind it, such as a drug-set or adverse-event-picture finding, keeps
   the prose the assessment recorded. This table keeps the **full** sentence —
   the direction, the entity, the distance apart — which is what the panel's
   chips are short enough to leave to it.
   A clinical-date finding's two dates are **cells inside that sentence**,
   held on `DiscrepancyVM` as `left`/`right` with the words either side in
   `lead` and `tail`. They used to be inside a prose string, where the page's
   colour rule could not reach them: an inconsistency about two dates showed
   neither of them in red, on a page whose visual vocabulary exists to mark
   exactly that. then *Superseded findings*
   for the suppressed ones. The verdict itself is in the panel at the top of
   the case, with both questions answered together; neither numbered section
   closes with its own conclusion, because the sections are the evidence and
   the panel is the finding.
   Retained rows are tinted red, with the same two row classes the drug and
   adverse-event tables use; a superseded row is not, because it contributed
   nothing and a red row would say the assessment weighed it.
   **The findings table carries no note.** It used to be followed by a
   paragraph explaining how to read its own two columns — severity as the
   level finally counted, the figure beside it as the weight this run applied
   to that level, the retained weights summing to the penalty. That is how
   the format works rather than evidence about this pair, so it is stated
   here, once, instead of under every table in a supplement. Where the
   penalty was not compared against a tolerance at all, the panel at the top
   of the case still says so — marking the figure `(not weighed)` and giving
   the reason, either that the question is reached only once independence has
   been rejected or that the reports were assessed on source context alone.
   That guarantee is a test over the panel. `EvidenceCaseVM.findings_weighed`
   (`InconsistencyPenalty.threshold is not None`) is still built and still on
   the view model for consumers; the page simply no longer prints a paragraph
   off it.
   So, read here rather than there: severity carries the weight the run
   applied to it, as `Moderate (+1.0)`; a finding whose severity was reduced
   is listed at its final level with the reason given; and the retained
   weights are what sum to the inconsistency penalty. The weight comes from
   the stored `InconsistencyPenalty.terms`, which records what the run
   actually used, so a run with custom weights is described with its own
   numbers and a run that saved no classification shows severity alone rather
   than a default that may not have applied.

### Row ordering

Patient characteristics keep a **fixed field order** (sex, age, country,
report date, height, weight) and are never reordered by evidence: a reader
comparing two cases should find the same field in the same place.

Drugs and adverse events are ordered by evidence category, not alphabetically
and not by whichever report happened to list them first.

**Drugs** sort by presence, then by role, then by name:

```
both reports          → key drugs and others together, by name
report A only         → key drugs (suspected, interacting) first, then the rest
report B only         → key drugs first, then the rest
```

**Adverse events** sort by presence, then by evidence, then by term — and the
two one-sided groups run in *opposite* directions:

```
both reports          → by term
report A only         → retained inconsistencies first, then neutral terms
report B only         → neutral terms first, then retained inconsistencies
```

The mirroring puts the terms vigiTwin did *not* treat as inconsistent into a
single band in the middle of the table rather than in two separate places.
Those are exactly the terms a reader wants to read against each other — a term
coded differently on the two reports, say — so they belong adjacent.

A cell carrying several attributions takes the strongest role:
`INCONSISTENT` > `ANCHOR` = `MATCHING` > `NEUTRAL`. Red wins because it is a
claim the assessment made *against* a value, where green is additive. Both
attributions stay on the cell, so a tooltip can show both.

A **row's** tint is a claim about the entity the row is about — the substance,
the adverse-event term — and not about the strongest thing in it. A drug both
reports carry, matched as part of the shared pattern, with a date
inconsistency on one of its records, is a **green** row with a red date in it.
Tinting the whole row red for a date difference overstated it into a finding
about the medication, which is not what the assessment said. Where the entity
carries no verdict of its own — in neither report's shared pattern, and
flagged by nothing — the strongest role among the row's cells still decides,
so an inconsistency is never left with no tint at all.

Note what row role does *not* do: it does not move the row. Position is set by
presence, because a reader scanning the drug table is first asking which
report listed what. Evidence orders rows only inside a presence group.

---

## Source context, and evidence that did not decide

Two things the format now keeps apart that it used to run together.

### Source context is structure, not a sentence

Section 2 carries a **Source context** block wherever the narrative
comparison reached a verdict: the verdict in the reader's terms, the shared
source identifier, and what became of the case labels *within* that source.

The identifier is an `EvidenceCell`, so it is coloured by the page's one
colour rule like any other value and a publication transform reaches it by
kind. The sentences name no identifier. That is the same division the
reporting context already keeps, and for the same reason: values live in
cells, and prose that carries no identity reads the same in both
presentations.

The identifier's *kind* is named in the reader's words, and source-neutrally
where the stored kind is a catch-all: `article_reference` is documented as "a
free-text citation when no more specific type fits", so labelling it a
literature citation claimed something specific about a source that need not
be a paper — a registry entry, a programme, a company's own case series. It
and `unknown` both read **Shared external source**; the specific kinds are
still named specifically.

The block states the case comparison rather than leaving a reader to infer
it from the verdict's name. Two reports can share a source and describe one
case, two cases, or two labels nobody can line up — and "same source
different case" is three nouns and no sentence.

### A pinned verdict's finding is not a weighed inconsistency

Where a definitive source-context verdict settles the relationship — a shared
source with different case labels within it — the classifier pins the outcome
*whatever the inconsistency penalty comes to*. The verdict also produces an
`Inconsistency` row, with a severity and a weight, and the page used to list
it in the table of weighed findings beside the ones that were weighed. A row
in that table, with a weight in its severity column, says the arithmetic
reached the conclusion.

So the page shows it under its own heading, **Decided on source context**,
with the severity the assessment recorded and no weight, and a note saying it
decided on its own. It is excluded from the scored trace: the findings table,
the panel's chips, and the generated summary's count of inconsistencies.

This is a **presentation change only**. The finding keeps its severity, its
weight, its retained flag and its place in `EvidenceCaseVM.discrepancies` and
in every stored artifact; `retained_discrepancies` is still the unfiltered
audit view. What moved is where the page puts it:

| Property | What it holds |
|---|---|
| `discrepancies` | every finding, as the assessment recorded it |
| `retained_discrepancies` | the retained ones — the audit view |
| `scored_discrepancies` | the retained ones the decision trace presents as weighed |
| `deferred_discrepancies` | the source-derived ones shown as source-context evidence instead |

`scored_discrepancies` equals `retained_discrepancies` except where
`source_context_decided` is true, which is the only case that differs.

### The panel's penalty is the total of what the panel lists

`InconsistencyPenalty.w_tot` is the stored total over every retained finding,
the source-derived one included. Where that finding is shown as source-context
evidence instead, the panel's Inconsistencies row says **None** — so its
Penalty row shows the sum of the findings it does list, which for a pinned
verdict is usually `0.0`. A pair carrying a source verdict *and* an ordinary
finding shows the ordinary one's weight; a run that recorded no weights at
all falls back to the stored total, because there is no sum to take and a
default this run may not have used would be worse.

The stored `w_tot` is untouched, in the artifacts and in the audit record.

### The tile does not discuss evidence that did not decide

The pathway badge says what the decision rested on, which is enough. An
earlier draft added a line to each tile naming the figure that had *not*
decided — "the matching score of 4.2 was computed and did not bear on this
decision" — and it answered a question the tile never raised. The shared
clause builders end theirs with "not on the matching score" for a reader who
has just been shown the score in a paragraph; on a tile that has not quoted
it, the qualifier reads the same way. Both are gone from the tiles.

The generated summary keeps its own wording, and agrees about the count: it
draws from the same exclusion, so a pair decided on source context is not
described as having "identified one decisive source and case inconsistency",
which would place the verdict among the weighed findings.

## The assessment summary

`build_assessment_summary(features, classification, *, run_settings=None)`
returns a short readable account of one pair's assessment. It is pure: no LLM,
no I/O, no templates, and the same inputs always produce the same text. Its
sentences fill fixed slots — shared pattern, anchor, additional matching
evidence, direct evidence, discrepancies, independence decision, same-case
decision — so a table and a paragraph render the same objects and cannot
drift apart.

Sentence *order* depends on the outcome, because the two outcomes read
naturally in different orders. Where independence was rejected, the findings
come before the decision that weighed them. Where it was not, the decision
comes first and the findings follow with the explicit note that they were
never weighed — which is the fact a reader most needs and the thing an
undifferentiated template would bury.

**An empty slot is dropped, not filled.** A pair with no additional matching
evidence, or no inconsistency, gets no sentence about it: "No additional
matching evidence was identified" and "vigiTwin identified no
inconsistencies" are gone, for the reason given under § Page structure. Where
independence was not rejected and nothing was found, what remains is the half
that says something — that the relationship question was not reached, because
independence was not rejected. One consequence for callers:
`AssessmentSummary.paragraphs(split_after=...)` splits on a slot being
*present*, so a summary with no sentence in that slot comes back as one
paragraph.

What the generator will not do:

- It never quotes a numeric threshold unless a run manifest supplied it.
- It names the decision pathway actually recorded, rather than assuming the
  pair was decided on rarity. A pair whose independence was rejected on a
  shared source says so, and does not cite its matching score as the reason.
- It says explicitly when no inconsistency-penalty comparison was made.
- It never presents a chance-match probability as a probability of
  duplication, and never prints `p_match`, `p_joint`, "probability",
  "L_match", "pipeline" or "algorithm". The reader-facing vocabulary is
  "matching score", "inconsistency penalty", "anchor population", "reporting
  context", and "vigiTwin".

### Human interpretations

A curated example may carry a `case_interpretation`, written by a person, and
`summary_mode` decides what the page shows: the generated account
(`deterministic`), the human's reading in its place (`interpretation`, the
default once one is supplied), or both (`both`). The human block is headed
**Human interpretation** — named for its author, since the block opposite is
headed "vigiTwin assessment summary" and which of the two a sentence came
from is the thing a reader needs to know.

In `both`, vigiTwin's own account comes **first**. It belongs with the panel
directly above it, which states the same assessment in figures; a human
reading is a response to that assessment, so it follows rather than standing
between the panel and the prose restating it.

Human text lives only in the manifest, and the generator has **no write path
to it** — not as a policy but by construction. `--export-drafts` writes the
generated summaries to a *separate* file as a starting point for editing, and
refuses to overwrite an existing one without `--force`, because by the second
run that file usually holds edited text.

---

## Run settings, and why the contributions do not sum to the score

The contributions table in section 2 ends in a **Total**: the per-feature
decades plus the anchor adjustment, which is shown as a row of its own
whenever it is non-zero so the column adds up to the figure beneath it.

    contributions total = -log10(p_joint)      (the per-feature decades)
    anchor adjustment   = -log10(M),  M = C(N_anchor, 2)
    Total               = -log10(p_joint x M)

That total is **not** the stored matching score, which is

    P_match = 1 - (1 - p_joint) ** M
    L_match = max(0, -log10(P_match + epsilon))

The two agree while `p_joint x M` is small, which is the ordinary case. They
part company at the score's two bounds — the ceiling at `-log10(epsilon)`
(15.0 at the default 1e-15) and the floor at zero, reached as `p_joint x M`
approaches one and a chance match stops being unlikely at all.

**The page does not explain either bound, and does not print the bounded
figure.** It explained them until 1.1.6, in a paragraph on every case.
Neither bound moves a classification: the zone boundaries that decide
independence and the same-case tolerance all sit far below the ceiling, and a
score at the floor is an absence of matching evidence either way. So from
1.1.8 the page states the **total** throughout — in the contributions
column, in the panel's *Matching score* row, and in the clause beside it. A
set of contributions implying 17.2 reads as 17.2, not as the 15.0 the stored
score was held at.

That is safe because the two cannot straddle a decision boundary. At the
ceiling both figures sit far above any independence threshold; at the floor
the stored score is zero and the total is negative, so both sit below one.
Between the bounds they agree to within the rounding tolerance.

**Except where they genuinely disagree**, and then the page quotes the stored
score instead — the figure the decision was actually made on. A total that
provably does not describe the decision would let a case say "the matching
score of -3.2 … exceeding the independence threshold of 1.0", which is worse
than quoting a number the column does not add to. `ReconciliationVM` carries
`reconciled` for exactly this choice, alongside `ceiling_reached` and
`floor_reached`, which record which bound was invoked.

**How "disagree" is decided.** Where `epsilon` is known the stored score is
*recomputed* from the contributions and the anchor — `p_joint = 10**-total`,
`P_match = 1 - (1 - p_joint) ** M`, `L_match = max(0, -log10(P_match +
epsilon))` — and compared against what the run stored. Where it is not, the
same arithmetic at `epsilon = 0` gives an upper bound on the stored score
instead, and a score sitting below that bound is unverifiable rather than
wrong; the gap says so and asks for the run manifest.

This replaced a test for whether the stored score sat *at* a bound, which was
the wrong instrument. `epsilon` does not switch on at the ceiling: it inflates
the denominator continuously as `P_match` approaches it, so a score of 14.1
against a stabiliser of 1e-15 is already about 0.05 below its additive figure
— past a tolerance built for rounding, and nowhere near the 15.0 a bound test
looks for. Ordinary pairs were being reported as unreconciled, in a sentence
quoting two figures that print identically.

An unreconciled case always carries a `score_unreconciled` gap on
`EvidenceCaseVM.gaps` — read there, not on the page, which no longer prints a
limitations list — naming both figures to **enough decimals to tell them
apart**: a note reading "totals +14.1, while the run stored 14.1"
contradicts itself, and the difference it is reporting is exactly the part one
decimal hides. Before 1.1.8 the gap was also recorded only when the run had
supplied no `epsilon`; a disagreement with a known `epsilon` passed silently,
which mattered little while the page printed the stored score and matters a
great deal now that it prints the total.

`epsilon` and the `L_match` zone boundaries are **not** in a saved run's
features file. They live on `PipelineConfig` and `DecisionThresholds`, and are
recorded only in a `RunManifest`. So:

```python
from vigitwin.review_html import load_evidence_run_settings

settings = load_evidence_run_settings("run_manifest.yaml")   # or .json
```

With settings, the review quotes the run's own numbers. Without them, it names
the evidence zone qualitatively and quotes no threshold and no ceiling. It
never reconstructs them from current defaults — a review that quoted today's
thresholds for a run from six months ago would be describing a run that never
happened. Supply `run_manifest` for every dataset in a collection you intend
to publish.

---

## Selecting pairs

Three ways to choose what gets rendered, in increasing order of deliberation:
every pair in the artifact, an explicit list of ids, or a curated manifest.

```python
from vigitwin.review_html import PairSelection, MissingPairPolicy, render_saved_run

render_saved_run(
    reports_path="reports.json",
    pairs_path="pairs.json",
    pair_results_path="pair_results.json",
    select=["R001__R002", "R001__R003"],      # or a PairSelection
    review_format="evidence",
    output_dir="outputs/evidence/",
)
```

`select` accepts a `PairSelection` or a bare iterable of ids, and is applied
before any per-pair work. `select=None` behaves exactly as before.

Two failure modes that look similar and are not:

- **An id matching no pair in the artifact raises, regardless of `strict`.**
  That is a mistake in the request, not a defect in the data. Every unknown id
  is reported at once, so a stale manifest costs one error rather than three
  edit-and-retry cycles.
- **A *selected* pair that cannot be loaded** follows the selection's own
  `on_missing` policy, which defaults to `ERROR`. This is a genuine semantic
  fork from `strict`: `strict=False` warns and skips an unselected pair, but a
  pair someone named explicitly still raises. Silently dropping it would leave
  a curated collection quietly short of an example, which is the one failure
  mode a collection cannot detect for itself.

`PairSelection.preserve_order` defaults to `True`: selection order is the
output order, because a curated sequence is a deliberate choice.

### Pairs from before canonicalisation

`load_saved_run` validates the whole pairs artifact, so selecting three good
pairs out of a pre-canonicalisation file still fails. Set
`allow_noncanonical_pairs: true` on the dataset (or pass
`--allow-noncanonical-pairs`) to view it as written. That is a visible choice
in the manifest rather than a render-time surprise; anything feeding an
evaluation must be regenerated instead.

---

## Curated collections

A collection manifest names the datasets to read and the examples to show, in
order, with the editorial framing that belongs in a file rather than on a
command line. YAML or JSON, by file suffix; YAML is read with `safe_load`
only. See [`examples/evidence_collection_example.yaml`](../examples/evidence_collection_example.yaml)
for a complete synthetic one — its `datasets` paths name a saved run that is
not committed here, since run artifacts are data files and this repository
holds none, so copy the manifest and point it at your own.

```yaml
schema_version: "1.0"
title: Illustrative report pairs from the performance evaluation of vigiTwin
intro: >
  Two examples illustrating how contextual matching evidence and retained
  inconsistencies inform the two-stage adjudication.
note: >
  Draft for author review. This is an internal evidence review.
positive_class: primary

datasets:
  synthetic_set:
    label: Synthetic set
    reports: synthetic_run/reports.json
    pairs: synthetic_run/pairs.json
    pair_results: synthetic_run/pair_results.json
    pair_frequencies: synthetic_run/pair_frequencies.json
    run_manifest: synthetic_run/run_manifest.json

examples:
  - example_id: S1
    dataset: synthetic_set
    pair_id: R001__R002
    title: Duplicates with extensive shared information
    lede: Extensive matching evidence despite differences in coding.
    scle_assessment:
      state: not_entered
    case_interpretation: >
      Human-authored interpretation goes here.
    review_status: draft
```

### Fields

**Collection:** `schema_version`, `title`, `intro`, `note`, `positive_class`,
`reference_label_map` (the default for datasets that declare none),
`datasets`, `examples`.

**Dataset** (`datasets.<dataset_id>`): `label` (the kicker shown on each
example from this set), `reports` and `pairs` (required), then either
`pair_results` **or** both `features` and `classifications` — the same
precedence `load_saved_run` applies, stated once in the manifest instead of
discovered at load time. Optional: `pair_frequencies`, `screening`,
`screening_verdict_key`, `annotations`, `narrative_demographics`,
`run_manifest`, `allow_noncanonical_pairs`, `explicit_link_enabled`,
`reference_label_map`.

**Example:** `dataset` and `pair_id` (required), `example_id` (also the output
filename, the section anchor and the label), `title`, `lede`, `kicker`,
`scle_assessment`, `case_interpretation`, `summary_mode`, `review_status`,
`notes`.

**`scle_assessment`:** `state` is one of `not_entered`, `not_assessed`,
`assessed`, `inconclusive` — four states, because three different absences
mean different things and a reader must not have to guess which applies.
`issue` is a `ReferenceLabelIssue` value, reused from
`study_eval.scle.form` rather than re-declared. `assessed` requires one and
rejects `genuinely_ambiguous` — that is the `inconclusive` state, and the
error says so. `inconclusive` takes `genuinely_ambiguous` or nothing at all,
filling it in either way. The two "no assessment" states reject an issue
outright, since there was no assessment to have an opinion.

**`explicit_link_enabled`** (per dataset) declares whether a run used
explicit inter-report case links, for runs whose manifest predates the
setting. A run configured without the feature still writes an `explicit_link`
field, and the page read it anyway — reporting "neither report carried
case-identifier data, so no explicit inter-report link could be checked",
which blames the data for a check nobody ran. With `false`, the note says the
run did not examine those fields and claims nothing about whether a link
exists, and the generated summary cites no declaration.

The note is still built for all four states and is still on the view model.
What reaches the page is only the state that found something:
`EvidenceCaseVM.explicit_link_found` is true where the case-identifier fields
produced a link, and the paragraph is printed then and only then. The three
absences — nothing found, nothing to check, the feature not used — are an
absence of evidence, and a reader does not need a paragraph for each.

It only **fills** what the run manifest did not record. Where both say
something and they disagree, the collection fails to build: the run manifest
is the run's own account of itself, and a declaration against it is an error
in the declaration, not a tie to break quietly.

**`reference_label_map`** carries a reference set's annotation vocabulary,
mapped to the study's binary one. It sits on the **dataset**, with the
collection-level field as the default for datasets that declare none — so a
single-set collection says it once at the top:

```yaml
reference_label_map:
  Otherwise related: non_duplicate

datasets:
  precision_set:
    reports: ...
```

and a collection spanning two reference sets puts each vocabulary where it
belongs:

```yaml
datasets:
  precision_set:
    reference_label_map:
      Otherwise related: non_duplicate
  recall_set:
    reference_label_map:
      Confirmed duplicate: duplicate
```

Per dataset for the same reason as `screening_verdict_key`: a reference label
is read out of *that* dataset's `annotations` file, and a collection spanning
two sets may be spanning two vocabularies. A dataset's mapping **replaces**
the collection default rather than adding to it, so a set that declares a
vocabulary gets exactly the one it declared. Merging would let a set inherit
a term its own annotators meant differently — and a wrongly mapped label is
shown as a human verdict, where an unmapped one is reported as a gap. Between
a silent wrong answer and a loud missing one, this takes the loud one.

Three terms are built in — `duplicate`, `non_duplicate`, `abstain` — because
those are the vocabulary every estimand knows. A reference set may annotate in
its own words, and how those words were read in the analysis is a study
decision that lives with the study: this package cannot infer that
`Otherwise related` was collapsed to non-duplicates, and guessing would be
inventing a human verdict. Keys are matched with case, spaces, underscores and
hyphens all folded, so `Otherwise related` and `otherwise_related` are one
term. Targets must be one of the three built-in values — `Non-duplicates` is
the study's *class* name, not a label value, and is rejected. A key that
redefines a built-in term is rejected too, so a collection cannot quietly make
`duplicate` mean its opposite.

An annotation whose term maps to nothing is **reported**, not dropped: the
tile still reads "Not supplied", and a `reference_label_unreadable` gap names
the term. Before 1.1.8 an unread label and an absent one were
indistinguishable, which is how a whole set's annotations can go unread with
nobody noticing.

**`positive_class`** names the rule used when comparing a binary reference
label against vigiTwin's four-label output: `primary` (only `Duplicates`
counts as positive), `abstain_positive`, or `otherwise_related_positive`.
These mirror `study_eval.aggregate`'s prespecified rules, which are a
*study-protocol* choice rather than a property of the method — so the page
footnotes the rule whenever it is not the primary one. Comparison happens in
the binary space the reference label actually lives in; `abstain` on either
side yields no verdict at all rather than a disagreement.

### How an example is identified

Two identifiers with different jobs, and the difference matters because one
of them ends up in a manuscript.

**`example_id` is the label, and it is citable.** It is the filename stem,
the HTML section anchor, and — since 1.1.5 — the heading the page shows. One
string doing all three is deliberate: a reference to "S3" in surrounding text
reaches `#S3`, which is headed `S3`, in the file `S3.html`. It changes only
when a curator changes it, so reordering a collection, inserting an example
or dropping one leaves every existing cross-reference pointing where it did.

Because it is an anchor, it is validated on load: it must start with a letter
and hold only letters, digits, `.`, `-` or `_`. `S 1` is rejected, rather
than rendering `id="S 1"` — a section no link can reach, in a document whose
purpose is being linked to. Ids that differ only in characters a filename
cannot carry (`S.1` and `S_1`) are rejected too, since one file would
overwrite the other.

Where an example gives no `example_id`, the collection invents `example-3`
for the anchor and shows `Example 3` as the label.

**The position is navigational, and it is not citable.** Each case also
carries where it sits in its collection, rendered as `3 of 12` beside the
kicker. It is counted over the cases that actually render, so an example
skipped under `MissingPairPolicy.SKIP` does not leave a reader hunting for a
section that is not there. It is deliberately kept visually apart from the
label: a position is a property of this document's current order, and citing
one would break the moment the order changed.

The position is carried on the case rather than derived from its place in
the rendered list, because the batch path writes one file per case — a
standalone `S3.html` must still say `3 of 12`, not `1 of 1`. A case rendered
on its own, outside any collection, shows no position at all.

### What the manifest may not say

Every model is `extra="forbid"`, and that is load-bearing rather than
fastidious: **labels come from the artifacts, never the manifest.** A stray
`reference_label:` or `final_label:` key is a hard error, not a silently
ignored one, so a collection cannot assert a verdict the saved run does not
support.

Validated on load, with every problem reported at once: unique dataset and
example ids, resolvable dataset references, no repeated pairs, the artifact
XOR above, SCLE state/issue consistency, and **filename collisions after
sanitisation** — two example ids differing only in punctuation would collapse
to one output file.

Relative paths resolve against the manifest's own directory, so a collection
and its artifacts move together.

> A real collection for a manuscript carries original report identifiers and
> clinical interpretations, so it belongs outside this public repository. Only
> synthetic manifests and synthetic reports are committed here.

---

## Gaps

Anything the stored artifacts cannot support becomes a test-assertable
record on `EvidenceCaseVM.gaps`, never a silent omission *in the record*.

**No gap is printed on the page.** The reader-facing "Limitations of this
review" list is gone: every entry on it named what was not there, which is
what the page no longer spends a reader's attention on. Gaps keep their codes
and their messages, `EvidencePageVM.gaps` still collects them across a
collection, and they are what a caller, a test and the publication gate act
on. The one entry a reader loses that was not a plain absence is
`score_unreconciled`; see § Page structure for why that matters and where to
read it instead.

Two of the codes below are **provenance** rather than absence: they say how a
value that *is* shown was obtained. `EvidenceGap.is_provenance` still says
which is which — a consumer may want to treat them differently — though it no
longer decides what a reader sees, since nothing on this list reaches the
page.

| Code | Means |
|---|---|
| `drug_set_terms_missing` | A drug-set inconsistency carries no `terms`, and none could be read from its description either, so the offending substances cannot be highlighted individually. |
| `drug_set_terms_recovered` *(provenance)* | The substances were read from the finding's own stored description, because the run predates `terms`. The highlighting is the run's finding, not a recomputed comparison. |
| `clinical_date_value_missing` | A clinical-date contribution carries no `matched_value`, and none could be read from its description either. |
| `clinical_date_value_recovered` *(provenance)* | The matched date was read from the contribution's own stored description, because the run predates `matched_value`. The date is highlighted wherever it appears; the run did not record which date *field* it came from, so a report carrying it twice shows both. |
| `screening_verdict_unreadable` | A screening entry's verdict key held something other than `true` or `false`, or a named `screening_verdict_key` was absent. The tile reads "Not evaluated". |
| `reference_label_unreadable` | An annotation carried a verdict in a vocabulary this review does not know. The tile reads "Not supplied"; the gap names the term and points at `reference_label_map`. |
| `unmapped_inconsistency` | An inconsistency whose `feature` this format does not know how to attach to a report element. It is still listed in section 3. |
| `unknown_entity_label` | A clinical-date finding whose `entity_label` does not parse into one of the four known forms. |
| `run_settings_missing` | No run manifest was supplied, so thresholds and the score ceiling are not quoted. |
| `frequency_stats_missing` | No frozen pair frequencies were supplied. Not recorded for a pair that merely shares no drug or adverse-event terms: there is then no co-occurrence to count. |
| `no_features` / `no_classification` | The pair's assessment was not saved. It renders as a report comparison with everything neutral — not with colours invented from a comparison this format does not perform. |
| `score_unreconciled` | The stored score is not what the contributions and the anchor imply, recomputed through the score's own formula. The page then quotes the stored score. |

### Runs saved before the structured fields

`Inconsistency.terms` / `.kind` and `FeatureContribution.matched_value` were
added after runs had already been saved. A run without them is not short of
the information, only of the field: the drug-set check wrote the offending
substances into its own `description`, and each clinical-date contribution
wrote its matched date into its own. Both are read from there, so an older run
highlights its drugs and its dates like any other, and the recovery is
recorded as such (`drug_set_terms_recovered`,
`clinical_date_value_recovered`). Those two are provenance gaps, so they stay
on the case for a caller and a test without being printed on the page.

Two things this deliberately does *not* do. It does not recompute the finding
from the two reports — key drugs on one report absent from the other would be
a comparison performed by the renderer, which the colour rule forbids, and it
can disagree with what the run actually flagged, since the drug-set check
applies a combination-product rule and may have changed since. And it does not
guess: a description that is not exactly the shape its producer writes yields
nothing, and the page says so with the `_missing` gap instead.

What an older run genuinely cannot supply is *which date field* a matched date
came from. The date is recorded per value, not per field, so a report carrying
the same date as both a drug start and an AE onset shows both as matched. The
recovered-date footnote says this.

---

## The footer

Every page ends with what produced it. The rendering version always appears,
read from installed distribution metadata — the same source
`build_run_manifest` uses for `dev_package.version`, so a page and a manifest
from one run cannot disagree. The rest comes from the run manifest when one was
supplied: the run id and date, the dev commit that produced the run, and the
decision settings it used (`EvidenceRunSettings.configuration_text` — the
`L_match` thresholds, `L_patient`, `epsilon` and whether explicit links were
enabled, each only if recorded).

A collection may span several runs. Where the cases disagree about the run or
its configuration, the footer says "several pipeline runs; see each case"
rather than attributing the page to whichever came first;
`page_provenance` makes that determination and `ProvenanceVM.several_runs`
records it. The footer also keeps the statement that this is an internal
review and not a publication-safe document.

### And once per sheet, on paper

That footer appears once, at the end. So does the MASKED band, at the start.
A sheet lifted out of a printed bundle carries neither, which is the one way
a reader can end up holding a page of transformed values with nothing on it
saying so. A second, print-only footer repeats on every sheet: the internal
warning, the publication candidate's caveat, or — once the document has been
signed off for release — the collection's own title, like a journal's running
head — and, once released, the fact that displayed values were transformed,
which is the one claim a cleared supplement's loose sheet must not lose.

It carries static text and no page number. The mechanism is a fixed-position
element, which Chrome paints once per sheet; the `@page` margin boxes and the
`counter(page)` that would give a page number are not reachable from one. It
sits in a deepened bottom margin so it cannot cover a line of the document,
and it is hidden on screen by a rule outside the print block — deliberately
outside, because no rule inside that block may hide content and the test
holding that line cannot tell one `display:none` from another.

A long collection title is clipped to the one line the band allows, so keep
it under about 110 characters if the document will be released.

---

## Printing and PDF

A journal asking for supplementary material usually wants a PDF. Nothing
here generates one, and that is deliberate: a PDF engine means either a
headless browser or a stack of system libraries, in a package that has to
stay installable by anyone and whose checks run with no network. The format
is instead *written* to print well, and the browser already on the machine
does the conversion faithfully, because it is the engine the HTML was built
for.

Open `combined.html` in Chrome or Chromium and print it:

- **Destination** — Save as PDF.
- **Paper size** — A4, which is what `@page` declares.
- **Scale** — 100%. "Fit to printable area" rescales the 10pt print type and
  throws away the A4 geometry the stylesheet sets up.
- **Headers and footers** — **off**. Chrome's own footer prints the `file://`
  path and a page count; a filesystem path can name a study, a drive or a
  person, and it would print beside a disclosure warning.
- **Background graphics** — **on**. The MASKED band is a background wash, and
  a page that loses it loses the one element whose absence changes what the
  document claims. The print rules force `print-color-adjust: exact` for the
  same reason, but the checkbox still governs.

Every example starts a fresh sheet, including the first, so the collection's
front matter gets a sheet of its own. Table headings repeat across sheets,
rows and callouts are not split, and no print rule hides content — all four
are asserted by tests rather than left as claims.

**The footer band has been checked on paper, in Chrome on macOS, on
2026-11-20.** It is recorded because it is the one property here that no test
can hold: Chrome positions a fixed element against the same box the
document's content flows through, so a footer placed in that box would
overlay the last line of every sheet. This one is pulled down into the margin
instead, and the margin is deepened to make room — which either works in a
given engine or does not, and cannot be asserted from the markup.

**Re-check it if you change print engine**, or if the `@page` margin or the
band's offset moves. Print a two-case collection and look at sheet two: the
last line of body text must be complete, and the band must sit below it. If
the band is missing, the right answer is to drop the repetition and keep the
single footer at the end — not to give the band an opaque background, which
would quietly cover the bottom of every published sheet.

---

## What this format is not

**It does not show the assessment's own field names.** Verdicts, identifier
kinds and pathways are named in the reader's words throughout. The stored
values are field names — `article_reference`, `same_source_different_case`,
`trial_registration` — and a parenthetical field name in a reader-facing line
is the clearest sign a page is showing its internals. A test asserts that
none of them reaches the rendered page.

**It is not publication-safe.** An evidence review may carry original report
identifiers, country names and narrative text, and its footer says so. Omitting
the debug panels does not make a page anonymised, and nothing here should be
described as publication-ready.

The confidentiality transformations — date and age offsets, country masking,
identifier replacement, narrative review — and the fail-closed publication
export built on them live in `vigitwin.review_html.evidence.publication`,
and are described in `docs/evidence_publication_mode.md`. They produce a
*second* presentation from the same cases and the same template; this one is
unchanged by them and stays the default.

Publication mode does not make a page publication-safe either. It produces a
candidate for disclosure review, which is what its footer and its manifest
say.

The design commitment that makes it possible: after the builder returns, the
only report value anywhere in the evidence view model is `EvidenceCell.text`.
Attribution records *why* a cell is coloured — feature name, severity,
contribution in decades — and never a source value, so it is non-identifying
and a publication transform is a pure `text` rewrite. This is pinned by a test
that shifts every date in a case by a shared offset and asserts the evidence
tree is byte-identical once the `text` fields are stripped.

---

## Entry points

### Python

```python
from vigitwin.review_html import (
    build_evidence_case,          # one pair -> EvidenceCaseVM
    build_assessment_summary,     # the deterministic prose
    build_collection,             # a loaded manifest -> EvidencePageVM
    load_curated_manifest,        # YAML/JSON manifest + path resolution
    load_evidence_run_settings,   # thresholds and epsilon from a run manifest
    write_evidence_html,          # one case -> one file
    write_evidence_batch_html,    # many cases -> files + index
)
```

`build_evidence_case` takes `report_1`, `report_2`, `pair_id` and optionally
`features`, `classification`, `frozen_bundle`, the two narrative-demographics
extractions, a `ReferenceInfo` (reference label, set name, screening outcome,
SCLE state, positive-class rule), an `ExamplePresentation` (the curated
framing) and `EvidenceRunSettings`.

### A whole saved run, or a selection from one

```bash
.venv/bin/python -m vigitwin.review_html \
    --reports reports.json --pairs pairs.json \
    --features features.json --classifications classifications.json \
    --pair-frequencies pair_frequencies.json --run-manifest run_manifest.json \
    --format evidence --set-label "Synthetic set" \
    --output-dir outputs/evidence/
```

The CLI reads the split `features.json` / `classifications.json` artifacts; a
combined `pair_results.json` is read by `load_saved_run` and by a collection
manifest's `pair_results`, not by this CLI.

`--format` defaults to `detailed`, so every existing invocation is unchanged.
`--pair-ids ID [ID ...]` narrows to a selection in the order given.
`--run-manifest` and `--set-label` apply to the evidence format only. Output
goes to `outputs/evidence_review/` by default, not to the detailed format's
`outputs/review_html/`: both formats write an `index.html` and name a pair's
page the same way, so one directory cannot hold both.

### A curated collection

```bash
.venv/bin/python -m vigitwin.review_html.evidence \
    --manifest collection.yaml \
    --output-dir outputs/supplement/
```

**The combined document is written by default**, as `combined.html` inside
`--output-dir`, and the index links it above its table. A curated collection
is read as one piece — ordered by hand, with its own intro and note — so the
combined document is the artifact and the per-example pages are the way round
it. That is the opposite of a saved run, where a directory someone navigates
pair by pair is the point, so the other entry point (`--format evidence`)
writes no combined document and is unchanged.

`--no-single-document` skips it. `--single-document PATH` writes *another*
copy at PATH, so `--no-single-document --single-document PATH` puts the
combined document somewhere else entirely and leaves the directory without
one. A case whose example id would resolve to `combined.html` or `index.html`
is refused rather than silently overwritten.

Also takes `--no-index`, `--export-drafts PATH` with `--force`, and
`--skip-missing` (off by default: a curated collection quietly missing an
example is worse than one that does not build). Recorded gaps print to
stderr.
