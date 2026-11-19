# Migration notes — publication disclosure checks (1.2.5)

Three breaking changes in `llm_dup_dev.review_html.evidence.publication`, all
small to act on. None changes the offset any existing supplement was published
with.

## 1. A candidate cap below the band it caps is now refused

`PublicationPolicy` validates `date_offset_candidates` against every band the
policy produces. A cap smaller than the largest band raises `ValidationError`
at construction, naming the band and the size it needs.

**What breaks:** any code that constructs a policy with an explicit
`date_offset_candidates`. In particular the previous default:

```python
PublicationPolicy(date_offset_candidates=2048)   # now ValidationError
```

**Why:** 2048 was documented as exceeding every band, and the clinical band is
3594 candidates, so the pool was sampled rather than searched. A sample drops
whole-year offsets, and a mixed-precision history is often solvable only at
those — so a case could fall through to a weaker offset, or to zero, while a
candidate that would have worked sat outside the sample. Publishing a pair's
real timeline because of a shuffle is the failure the number was there to
prevent.

**What to do:** drop the argument and take the new default of 3600. If you set
it deliberately, either raise it to at least
`policy.offset_band_sizes()` → largest value, or narrow the offset range so
the bands fit under your cap:

```python
# inspect what this policy needs
PublicationPolicy().offset_band_sizes()
# {'report': 742, 'report:below_floor': 58,
#  'clinical': 3594, 'clinical:below_floor': 58}

# a narrower clinical range may use a smaller cap
PublicationPolicy(clinical_offset_max_days=400, date_offset_candidates=742)
```

A policy stored as JSON secure-side and loaded back needs the same treatment:
a stored `2048` will now fail to load.

## 2. A real country beside a small contextual count now blocks

A case whose frequency table shows a scope *naming a country* with a count at
or below `min_contextual_count` (five by default) raises
`retained_country_small_count`, which is a blocking finding.

**What breaks:** a page that cleared before may now block, so a secure-side
run that wrote a supplement unattended will raise `PublicationBlocked`.

**Why:** the counts include the two reports under review, so a co-occurrence
count of two in a named country and calendar year is this pair and nobody
else. The combination was described in the limitations section and checked
nowhere.

**What to do**, in order of preference:

1. **Mask that example's country.** The remedy the finding recommends, and the
   one that actually reduces the risk rather than recording it:

   ```python
   PublicationApprovals(
       country_masking=[
           CountryMaskingOverride(
               example_id="...",
               mask=True,
               decided_by="...",
               decided_at="2026-11-19",
               rationale="a co-occurrence count of two in one country-year",
           )
       ]
   )
   ```

2. **Mask every country** for the run with `PublicationPolicy(mask_countries=True)`.

3. **Sign for it** with a `FindingResolution` naming the code, the example and
   a reason the population is wide enough. The resolution is an audit record,
   not a switch.

4. **Raise or disable the threshold** with `min_contextual_count`. Zero
   switches the check off; do that only with a reason, because it is the only
   automated check on the one combination this layer calls its main residual
   risk.

A reminder from the new § Threat model: these defaults are tuned for a large
multi-national database. On a national dataset the country is a constant and
a higher threshold is the cheaper instrument.

## 3. The public manifest no longer carries `counts`

`PublicationTransformManifest.counts` and the `TransformCounts` model are
removed, and `counts` is gone from
`schemas/evidence_publication_manifest.schema.json`.

**What breaks:** reading `manifest.counts`, or validating an old manifest
against the new schema. Manifests already written are unaffected as documents;
they simply carry a field the current model will reject under
`extra="forbid"`.

**Why:** it was the one field in the public manifest that told a reader of the
manifest *alone* something the supplement did not already tell them — anyone
holding the pages can see every transformed value and very nearly count them.
Against that, for a single-case supplement the run-level aggregate is that
case's own profile, and `date:day → 16` is close to a fingerprint.

**What to do:** read the secure `ScramblingKeyRecord` instead, where
`cases[].substitutions` carries every mapping with its `kind`, so per-kind
counts are a `Counter` away:

```python
from collections import Counter
Counter(s.kind for case in record.cases for s in case.substitutions)
```

If you need a count in a methods section, take it from the key record
secure-side and write the number into the prose. Do not reintroduce it into
the published manifest.

## Not a migration, but worth knowing

Cases may now carry a `date_offset_pool_thin` advisory, reporting that the
accepted offset was drawn from a set small enough to enumerate from the
published page. It is advisory, so it does not block the export, and it is
reported for the same reason `date_offset_below_floor` is: the salt chooses
within the surviving set and does nothing to hide the set, so a thin set is a
residual disclosure risk that no transform can fix. Tune or disable it with
`PublicationPolicy.date_offset_min_survivors`; zero switches it off.
