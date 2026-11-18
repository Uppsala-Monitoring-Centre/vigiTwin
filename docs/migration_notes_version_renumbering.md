# Migration note: version renumbering, 2026-11-18

The versioning convention changed from strict SemVer to a **significance**
signal (`agents.md` § Conventions → Versioning), and the unpublished history
between `v1.0.2` and the tip was renumbered to match. Eleven versions became
three: one minor for each meaningful addition, patches for the refinement
rounds between them.

**Nothing published moved.** `v1.0.2` and everything before it — every tag,
every commit on `github/main`, the `v1.0.0` GitHub Release, both Zenodo DOIs
and `CITATION.cff` — is untouched. The rewrite covered only the twenty commits
after the public tip, none of which had reached GitHub and none of which backs
a study run whose provenance must be preserved.

This note exists because two things that left the machine still name the old
numbers: experimental artifacts written secure-side carry a
`dev_package.version` that no longer exists anywhere in history, and anything
pinning one of the rewritten commits names a SHA that no longer resolves.

## Why

The old rule bumped the version whenever a `schemas/*.schema.json` or a name in
`docs/integration_guide.md` § Public surface moved, in the same change. Three
days of evidence-presentation work (2026-11-15 → 2026-11-17, twenty commits)
therefore spent eleven version numbers — and twice had to argue in the
`CHANGELOG` against the rule it was following: old 1.2.0 explained why a
renamed public name was shipping as a minor rather than the major the rule
demanded, and old 1.3.1 explained why a behaviour change was a patch.

A version number is more useful saying how far the method has moved. Under the
new convention the same three days read as what they were: the evidence review
format arriving (1.1.0), eight rounds of refining it, and publication mode
arriving (1.2.0).

## Version map

| Old | New | What it is |
|---|---|---|
| 1.1.0 | **1.1.0** | the `evidence` review format — *meaningful addition* |
| 1.2.0 | 1.1.1 | `bundles` → `pair_frequencies` across the review interfaces |
| 1.3.0 | 1.1.2 | more of a stored run: screening verdict, legacy dates and drugs, weights |
| 1.3.1 | 1.1.3 | compaction pass on the rendered page |
| 1.4.0 | 1.1.4 | both bounds of the score, unweighed findings, footer provenance |
| 1.5.0 | 1.1.5 | a case says what it is called and where it sits |
| 1.5.1 | 1.1.6 | the right independence threshold; the score as a total |
| 1.5.2 | 1.1.7 | two readability fixes |
| 1.6.0 | 1.1.8 | red means "counted against"; the at-a-glance panel; the reference vocabulary |
| 1.7.0 | **1.2.0** | publication mode — *meaningful addition* |
| 1.7.1 | 1.2.1 | reader-facing names for the relationship outcome and shared sources |

**The current version is 1.2.1.** No version number above it was ever public,
so a constraint written against one — `>=1.3.0`, `>=1.7.0`, `==1.7.1` — is now
unsatisfiable and should be remapped through this table (`>=1.1.2`, `>=1.2.0`,
`==1.2.1`).

An artifact already written secure-side keeps the `dev_package.version` it
recorded. Read it through this table; the `dev_git_commit` beside it is the
authoritative identifier either way, and resolves through the SHA map below.

## SHA map

The first three commits were content-identical after renumbering and kept their
hashes. The remaining seventeen were rewritten.

| Old | New | Old → new version | Subject |
|---|---|---|---|
| `c0c2d69a43` | *(unchanged)* | 1.1.0 → 1.1.0 | Add an evidence review format that colours by the assessment, not the diff |
| `03b762631f` | *(unchanged)* | 1.1.0 → 1.1.0 | Write down when a version gets a tag and when it gets a DOI |
| `f9e13043a9` | *(unchanged)* | 1.1.0 → 1.1.0 | Point the install line at the newest tag, not the newest archive |
| `8bc95840d5` | `a226d95216` | 1.2.0 → 1.1.1 | Name the artifact, not the model: bundles -> pair_frequencies |
| `15bf679ed2` | `ae10e85531` | 1.2.0 → 1.1.1 | Drop the validation_ prefix from the last two docs that carried it |
| `53bd33e2fd` | `710485b360` | 1.3.0 → 1.1.2 | Show more of a stored run: screening verdict, legacy dates and drugs, weights |
| `171f436e46` | `41df7022d8` | 1.3.1 → 1.1.3 | Say less on the page: compact the legend, the notes and the limitations |
| `f3bd6a7ec2` | `c6a425c52d` | 1.4.0 → 1.1.4 | Account for both bounds of the score, and for findings nothing weighed |
| `ea1b8b344a` | `f49f5433b0` | 1.5.0 → 1.1.5 | Say which example this is, and where it sits in the collection |
| `d5f3e89249` | `560719422b` | 1.5.1 → 1.1.6 | Name the two questions, total the column, and quote the right threshold |
| `e0c6484d6f` | `e83f4b6836` | 1.5.2 → 1.1.7 | One name for the classification, and say the unweighed case once |
| `e745d178ef` | `8df8a5aad1` | 1.6.0 → 1.1.8 | Open each case with the assessment, and widen what red means |
| `989675e5d1` | `49bbfbb22e` | 1.6.0 → 1.1.8 | Show the values, not descriptions of them, and the total, not the bound |
| `5a8877a436` | `3189e438b9` | 1.6.0 → 1.1.8 | Let the table say what two paragraphs said about it |
| `e1311169d0` | `8ecfe2aedc` | 1.6.0 → 1.1.8 | Stop the page saying things the assessment did not |
| `9f028f339e` | `d8b7b65348` | 1.6.0 → 1.1.8 | Put the reference vocabulary where the annotations are |
| `74db36e87b` | `168e2e5da5` | 1.6.0 → 1.1.8 | Show the reader what the assessment actually looked at |
| `077613d8cb` | `0ed4a94a69` | 1.7.0 → 1.2.0 | Let the evidence review be published, and say what decided each pair |
| `ccc0c2f832` | `57761ad007` | 1.7.1 → 1.2.1 | Name the relationship outcome, and stop assuming a shared source is a paper |
| `93a6513019` | `e895fa6dac` | 1.7.1 → 1.2.1 | Say what the GitHub mirror is actually for |

**The old commits no longer exist anywhere.** They were never on `github`, they
are gone from `origin` since the force-update, and the local backup branch that
held them was deleted on 2026-11-18 once this map was verified against it. A
reflog entry on the one machine that did the rewrite may still resolve an old
hash until it is garbage-collected; nothing else will. This table is the record
— don't go looking for `backup/pre-renumber-2026-11-18`.

## What a consumer has to do

1. **Remap any version constraint** on `llm-dup-dev` through the version map
   above. A constraint naming 1.3.x–1.7.x cannot be satisfied by any version
   that exists.
2. **Remap any commit pin** — a `git+…@<sha>` requirement, a recorded
   `dev_git_commit` you intend to check out, a SHA in a notebook or a run log
   — through the SHA map above.
3. **Reinstall** so `importlib.metadata` reports the new number, since that is
   what lands in `dev_package.version` on every manifest the pipeline writes:

   ```bash
   pip install -e . --no-deps
   ```

4. **Nothing else.** No schema, no public name, no classification result and no
   rendered page changed in the renumbering: the rewrite touched version
   strings only. `git diff` between the old and new tips is twenty-eight lines,
   every one of them a version number.

## What this changes going forward

A break can now arrive in a minor or a patch, so the number is no longer the
warning. The `CHANGELOG.md` entry is, and a `docs/migration_notes_*.md` like
this one wherever updating takes steps. If you automate anything against this
package, pin a commit rather than inferring compatibility from the version.
