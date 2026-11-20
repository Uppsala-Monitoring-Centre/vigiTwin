# Migration note: the package is `vigitwin`, 1.3.0

**Audience:** everything that imports this package or reads its serialized
output — in particular the secure-side deployment (`llm_dup_pv_secure`), and
any tooling that keys on `dev_package.name`, pins a version constraint, or
configures logging by logger name. This document describes the contract
changes only; no secure-side code is modified from this repository.

The distribution and the import path were both renamed in 1.3.0:

| | Through 1.2.7 | From 1.3.0 |
|---|---|---|
| distribution (`pip install`) | `llm-dup-dev` | `vigitwin` |
| import path | `llm_dup_dev` | `vigitwin` |

The hyphen/underscore split is gone: the new name is one word, so the
distribution and the import are spelled identically.

**There is no compatibility shim.** `import llm_dup_dev` raises
`ModuleNotFoundError` from 1.3.0 onward, deliberately — `agents.md` § Versioning
sets no deprecation window, and a shim shipped to a public index is a package
nobody should import. This note is the migration path instead.

## Why it is a minor, not a major

`agents.md` § Conventions → Versioning reserves a major for "a substantial new
generation of the method" and states that breaking API or schema changes alone
do not earn one, however many names move; a minor "may carry
backward-incompatible API or schema changes, including package restructuring or
renaming". This is that case exactly. The 1.0.0 changelog entry deferred the
rename to 2.0.0 under the strict-SemVer rule that applied then; that rule was
replaced on 2026-11-18 (see `migration_notes_version_renumbering.md`), and the
README's promise of a 2.0.0 went with it.

So **the number is not the warning here.** This note is.

## What a consumer has to do

### 1. Rewrite the imports

Every import is affected: there were 886 of them in this repository alone, all
absolute. From the root of a consumer checkout:

```bash
git ls-files -z | xargs -0 perl -pi -e 's/\bllm_dup_dev\b/vigitwin/g; s/\bllm-dup-dev\b/vigitwin/g;'
```

Then read the diff rather than trusting it. Two kinds of hit need a human:

- **Prose.** The method and the repository are `vigiTwin`, capitalised; only
  the package is `vigitwin`. A sweep gets backticked module paths right and
  sentences wrong.
- **Historical statements.** A line recording what some past version *was*
  ("upgrade the installed `llm-dup-dev` to 0.9.0") is still correct as
  written and should be left alone.

### 2. Reinstall — and make sure the old install is really gone

```bash
pip uninstall -y llm-dup-dev
pip install vigitwin        # or: pip install -e /path/to/vigitwin
```

**Do not skip the uninstall.** This is the one step whose omission fails
silently, in two separate ways:

- A stale `llm_dup_dev` left importable keeps satisfying any import you have
  not migrated, so the sweep looks complete when it is not.
- A stale `llm-dup-dev` *distribution metadata* entry keeps resolving a
  version, so provenance looks correct locally and goes null in production.

`pip uninstall` can also leave an `_editable_impl_llm_dup_dev.pth` behind in
some pip/hatchling combinations, which is the first failure in its purest
form. Assert the negative rather than assuming:

```bash
python - <<'PY'
import importlib.metadata as md, importlib.util as iu, sys
bad = []
if iu.find_spec("llm_dup_dev") is not None:
    bad.append("old import still resolvable")
try:
    bad.append("old metadata still present: " + md.version("llm-dup-dev"))
except md.PackageNotFoundError:
    pass
print("DIRTY: " + "; ".join(bad) if bad else "CLEAN " + md.version("vigitwin"))
sys.exit(1 if bad else 0)
PY
```

If it reports DIRTY, delete the leftovers from `site-packages` by hand, or
rebuild the virtual environment.

### 3. Remap version constraints

`llm-dup-dev>=1.2.0` becomes `vigitwin>=1.2.0`. The version *numbers* are
continuous across the rename — 1.3.0 follows 1.2.7 — so only the name changes.
A constraint in the 1.3.x–1.7.x range written before 2026-11-18 is a different
problem; see `migration_notes_version_renumbering.md`.

### 4. Repoint anything that configures logging by logger name

**This is the change most likely to pass unnoticed.** Loggers are named from
`__name__`, so the rename moved them:

```
llm_dup_dev.pipeline.runner   ->   vigitwin.pipeline.runner
```

A `dictConfig` whose `loggers:` map keys on `"llm_dup_dev.pipeline.runner"`, or
a `getLogger("llm_dup_dev.pipeline.runner").setLevel(...)`, simply stops
matching. Python creates the stale name as an orphan logger and the real one
inherits from root instead — so the handler or filter you configured no longer
applies. What that loses is documented in `integration_guide.md`: the
`WARNING` the runner emits when `llm_client=None` and no frozen bundle covers
a pair, which is the only signal that source-context evidence has been
silently disabled. **A warning about a silent failure, silently lost.**

Search for the string, not just for imports.

### 5. Accept both values of `dev_package.name` in archived artifacts

Four artifact types carry it — run manifest, dataset manifest,
characterization report, study evaluation run manifest:

```json
"dev_package": { "name": "vigitwin", "version": "1.3.0" }
```

Artifacts written by 1.2.7 and earlier say `"llm-dup-dev"`, and that remains
**correct for those runs**: the field records the distribution name at the time
of the run, and both values name the same method lineage. Anything that
partitions, joins or filters archived runs on this field has to accept both.
The 1.0.0 entry already reasoned about exactly this when it deferred the
rename; the conclusion is unchanged, there are now simply two values in the
wild.

### 6. Note the two schema defaults

`characterization_report.schema.json` and
`study_evaluation_run_manifest.schema.json` each carry `dev_package.name` as a
`"default"`, which changed from `"llm-dup-dev"` to `"vigitwin"`.

**`SCHEMA_VERSION` is deliberately not bumped.** A changed default is not a
changed shape: every document valid under the old schema is valid under the
new one, and the only difference is what an omitted field becomes. Bumping
would imply a shape change and force consumers to branch on it for nothing.

### 7. Cosmetic: rendered HTML footers

Review pages generated by `vigitwin.review_html` state the module that
produced them, so the footer text changed. Nothing reads it programmatically;
it is listed here only so a diff of regenerated pages is not a surprise.

## What did not change

- **No decision, score, label or threshold.** The classification logic is
  untouched; a pair adjudicated under 1.2.7 adjudicates identically under
  1.3.0.
- **No schema shape**, no `$id`, no `$ref`, no `title` — schema *identity* was
  never namespaced by the package name.
- **`VIGITWIN_PUBLICATION_SALT`** keeps its name, and the publication layer's
  key derivation is unchanged, so **previously derived key ids and scrambled
  values still reproduce**. The HKDF info string was already `vigitwin`.
- **Zenodo DOIs, `CITATION.cff` and every existing tag.** `CITATION.cff`
  describes the last *archived* release and moves only when a Release is cut.
