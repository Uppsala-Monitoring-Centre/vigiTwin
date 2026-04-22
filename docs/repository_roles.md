# Repository Roles and Controls
We will maintain two physically separate repositories with distinct roles:
llm_dup_dev:	Prompt engineering, structured output design, evaluation logic
llm_dup_pv_secure: Annotation support, evaluation on real data, Azure execution

## llm_dup_pv_secure (Secure Environment)

**Purpose**
<ul>
<li>Annotation notebooks for eval set creation</li>
<li>Access to real report extracts + summary statistics</li>
<li>Storage and analysis of eval sets</li>
<li>Azure-based LLM execution against real data</li>
<li>Metric computation</li>
<li>Structured Case-Level Examination (conducted exclusively within the secure environment)</li>
</ul>

**Controls**
<ul>
<li>Used only in VS Code without external AI coding assistants</li>
<li>No Claude extension installed</li>
<li>No external AI coding assistants connected to repo</li>
<li>Never opened in workspace or environment where external AI coding assistant is enabled.</li>
<li>.claudeignore file added as an additional precautionary safeguard, not the primary security control</li>
</ul>

## llm_dup_dev (AI-Assisted Development Environment)
**Purpose**
<ul>
<li>Prompt design and iteration</li>
<li>Decision logic (structured reasoning schema) development</li>
<li>Post-processing logic</li>
<li>Evaluation harness development</li>
<li>Analysis of aggregated metrics</li>
</ul>
**Controls**
<ul>
<li>Accessed via the Claude Code app</li>
<li>Contains no real pharmacovigilance data</li>
<li>Uses only:
<ul>
<li>Synthetic, public, or abstracted examples
<li>Aggregated performance metrics
<li>Non-identifiable feature summaries
</ul>
</ul>

## Derived narrative signals

The secure side provides **plain narrative text** as part of the Report contract. Turning that text into structured signals (source identifiers, case labels, and — in the future — patient age/sex/height/weight) is a dev-side responsibility: the prompt, the Pydantic schema, the post-parse normalization, and the deterministic downstream pair-level comparison all live in `llm_dup_dev`. The secure side only supplies narratives and an `LLMClient` implementation; extracted outputs are *derived narrative signals*, not raw source data, and can be frozen to JSON for offline reuse. See `docs/data_contracts.md § Derived Narrative Signals`.

Downstream pair-level reasoning over extracted fields is deliberately deterministic (no pair-level LLM step).

## Data Flow Model
llm_dup_dev (Claude-assisted)
        ↓
Code / prompt updates
        ↓
llm_dup_pv_secure (no AI)
        ↓
Results of Azure execution on real data
(aggregated, non-identifiable metrics only)
        ↓
llm_dup_dev (Claude-assisted)
 
## Additional Safeguards
<ul>
<li>Physical directory separation (~/llm_dup_dev/ and ~/llm_dup_pv_secure/)</li>
<li>No shared workspace roots</li>
<li>No multi-root workspaces spanning both repositories</li>
<li>.claudeignore files added to all folders in llm_dup_pv_secure</li>
<li>No symlinks or mounts between repositories</li>
<li>No copy-paste of real case text into llm_dup_dev</li>
</ul>