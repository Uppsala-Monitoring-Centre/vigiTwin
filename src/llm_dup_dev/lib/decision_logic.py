from typing import Dict, Tuple, List, Any
import re
import html

# ======================== utilities & normalizers ========================


def _as_dict(x: Any) -> Dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _s(x: Any) -> str:
    return "" if x is None else str(x).strip()


def _norm_key(val: str) -> str:
    """Lowercase and remove spaces/underscores/hyphens."""
    return re.sub(r"[_\-\s]+", "", (_s(val)).lower())


def _tri_bool(x: Any) -> Tuple[bool, bool]:
    """
    Tri-state to (value, is_unclear).
    Accepts booleans, 1/0, yes/no/true/false strings; 'unclear'/'unknown'/'' => unclear.
    """
    if isinstance(x, bool):
        return x, False
    if isinstance(x, (int, float)):
        if x == 1:
            return True, False
        if x == 0:
            return False, False
    xs = _s(x).lower()
    if xs in {"true", "yes", "y"}:
        return True, False
    if xs in {"false", "no", "n"}:
        return False, False
    if xs in {"unclear", "unknown", "na", "n/a", ""}:
        return False, True
    return False, True


# ======================== feature extractors ========================


def _ae_similar(ae_json: Any) -> Tuple[bool, str]:
    """
    AE similar?  'consistent' or 'partially_consistent' -> True; otherwise False.
    Missing/unknown => False (not similar).
    """
    m = _s(_as_dict(ae_json).get("ae_consistency")).lower()
    if m in {"consistent", "partially_consistent"}:
        return True, f"AE similarity: {m} ⇒ similar=True."
    if m == "inconsistent":
        return False, "AE similarity: inconsistent ⇒ similar=False."
    return False, f"AE similarity: {m or 'unknown'} ⇒ similar=False."


def _same_patient(same_patient_json: Any) -> Tuple[str, str]:
    """
    Normalize to 'same' | 'different' | 'unclear' with flexible mapping.
    """
    raw = _norm_key(_as_dict(same_patient_json).get("same_individual_assessment"))
    mapping = {
        "same": "same",
        "match": "same",
        "duplicate": "same",
        "samepatient": "same",
        "different": "different",
        "distinct": "different",
        "notsame": "different",
        "unclear": "unclear",
        "unknown": "unclear",
        "": "unclear",
    }
    val = mapping.get(raw, "unclear")
    return val, f"Same-patient: {val}."


def _timing_ok(same_patient_json: Any) -> Tuple[bool, bool, str]:
    """
    Optional timing check (only used when same-patient = 'same' per diagram).
    Expects any of: True/False/Yes/No or fields like 'timing_consistent'.
    Returns (value, is_unclear, explanation).
    - If missing/unclear, we treat as NOT confirmed (diagram's 'Y?' arrow means: only YES leads to Duplicates).
    """
    sj = _as_dict(same_patient_json)
    # Accept either 'timing_consistent' or 'timing' or 'consistent_timing'
    for key in ("timing_consistent", "timing", "consistent_timing"):
        if key in sj:
            val, unk = _tri_bool(sj.get(key))
            expl = f"Timing check via '{key}': {'yes' if val else ('unclear' if unk else 'no')}."
            return val, unk, expl
    return False, True, "Timing check: missing/unknown ⇒ treat as not confirmed."


def _cluster_flag(clustering_json: Any) -> Tuple[bool, str]:
    """
    cluster_assessment ∈ {"possible_cluster","no_evidence_of_cluster"} with flexible parsing:
    underscores/hyphens/spaces/case are ignored.
    """
    raw = _s(_as_dict(clustering_json).get("cluster_assessment"))
    norm = _norm_key(raw)
    if norm == "possiblecluster":
        return True, f"Cluster: {raw or 'possible_cluster'} ⇒ True."
    if norm == "noevidenceofcluster":
        return False, f"Cluster: {raw or 'no_evidence_of_cluster'} ⇒ False."
    return False, f"Cluster: {raw or 'missing/unknown'} ⇒ default False."


# ------- (Optional) literature shortcuts—leave out if you don’t want them --------


def _literature_shortcuts(literature_json: Any) -> Tuple[str, List[str]]:
    """
    Single case in same paper => Duplicates.
    Multiple cases in same paper => Related (upgrade to Duplicates if same-patient later).
    If fields unclear, fall back to main tree.
    """
    reasons: List[str] = []
    sj = _as_dict(literature_json)
    single, su = _tri_bool(sj.get("Single_patient_study"))
    multi, mu = _tri_bool(sj.get("Multi_patient_study"))
    cohort, cu = _tri_bool(sj.get("same_study_cohort"))

    mot = _s(sj.get("motivation"))
    if mot:
        reasons.append(f"Literature note: {mot}")

    if single and cohort:
        reasons.append("Literature: single case in same paper ⇒ Duplicates.")
        return "Duplicates", reasons
    if multi and cohort:
        reasons.append(
            "Literature: multiple cases in same paper ⇒ Otherwise_related (upgrade possible with same-patient evidence)."
        )
        return "Otherwise_related", reasons

    if su or mu or cu:
        reasons.append(
            "Literature fields partly missing/unclear ⇒ fall back to main tree."
        )
    return "", reasons


# ======================== main: exact diagram logic ========================


def classify_pair(
    literature_json: Any,
    ae_json: Any,
    clustering_json: Any,
    same_patient_json: Any,
    use_literature_shortcuts: bool = True,
) -> Dict[str, str]:
    """
    Implements the flow in the provided 'Decision logic' diagram.
    Returns {"label": "...", "explanation": "..."}.
    """

    reasons: List[str] = []

    # (0) Optional literature pre-rules
    early_label, lit_reasons = ("", [])
    if use_literature_shortcuts:
        early_label, lit_reasons = _literature_shortcuts(literature_json)
        reasons.extend(lit_reasons)

    # If literature already forces Duplicates, exit.
    allow_upgrade = early_label == "Otherwise_related"
    ae_sim, ae_expl = _ae_similar(ae_json)
    reasons.append(ae_expl)
    cluster, cl_expl = _cluster_flag(clustering_json)
    reasons.append(cl_expl)
    sp, sp_expl = _same_patient(same_patient_json)
    reasons.append(sp_expl)

    if early_label == "Duplicates":
        return {"label": "Duplicates", "explanation": " | ".join(reasons)}

    if allow_upgrade and sp == "same":
        reasons.append("Upgrade: multi-case same paper + same patient ⇒ Duplicates.")
        return {"label": "Duplicates", "explanation": " | ".join(reasons)}

    # ---------------- flowchart ----------------
    # AE similar?
    if not ae_sim:
        # N → Same patient?
        if sp == "same":
            # Y → Related (then 'Timing?' may upgrade to Duplicates)
            reasons.append("AE not similar + same patient ⇒ Otherwise_related (check Timing?).")
            t_ok, t_unc, t_expl = _timing_ok(same_patient_json)
            reasons.append(t_expl)
            if t_ok:  # 'Y?' path to Duplicates
                reasons.append("Timing consistent ⇒ Duplicates.")
                return {"label": "Duplicates", "explanation": " | ".join(reasons)}
            else:
                reasons.append("Timing not confirmed ⇒ keep as Otherwise_related.")
                return {"label": "Otherwise_related", "explanation": " | ".join(reasons)}
        else:
            # N? → Non-duplicates
            reasons.append(
                "AE not similar + not same patient/unclear ⇒ Unrelated."
            )
            return {"label": "Unrelated", "explanation": " | ".join(reasons)}

    # AE similar == True
    # → Same patient?
    if sp == "different":
        # N → Cluster?
        if cluster:
            reasons.append("AE similar + different patients + cluster ⇒ Otherwise_related.")
            return {"label": "Otherwise_related", "explanation": " | ".join(reasons)}
        else:
            reasons.append(
                "AE similar + different patients + no cluster ⇒ Unrelated."
            )
            return {"label": "Unrelated", "explanation": " | ".join(reasons)}

    if sp == "same":
        # Y → (diagram doesn't show timing here; treat as duplicates directly)
        reasons.append("AE similar + same patient ⇒ Duplicates.")
        return {"label": "Duplicates", "explanation": " | ".join(reasons)}

    # sp == 'unclear' (the '?' branch in the diagram)
    # ? → Cluster?
    if cluster:
        reasons.append("AE similar + same patient unclear + cluster ⇒ Otherwise_related.")
        return {"label": "Otherwise_related", "explanation": " | ".join(reasons)}
    else:
        reasons.append("AE similar + same patient unclear + no cluster ⇒ Duplicates.")
        return {"label": "Duplicates", "explanation": " | ".join(reasons)}


def classification_result_to_html(
    result: Dict[str, str], include_style: bool = True
) -> str:
    """
    Convert a classification result like:
      {"label":"Related", "explanation":"a | b | c"}
    into an HTML snippet you can drop into an existing page.
    """
    label = (result or {}).get("label", "Unknown")
    expl = (result or {}).get("explanation", "")

    # Split explanation on " | " and escape HTML
    steps = [html.escape(s.strip()) for s in expl.split("|") if s.strip()]

    # Simple label-to-color map
    color_map = {
        "Duplicates": "#198F8A",  # teal
        "Otherwise_related": "#F4B400",  # amber
        "Unrelated": "#D93025",  # red
        "Unknown": "#5f6368",  # gray
    }
    badge_color = color_map.get(label, color_map["Unknown"])

    style = ""
    if include_style:
        style = f"""
<style>
.cr-card {{ font-family: system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,'Helvetica Neue',Arial; 
            border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px 14px; 
            box-shadow: 0 1px 2px rgba(0,0,0,.04); background: #fff; }}
.cr-header {{ display:flex; align-items:center; gap:.6rem; margin-bottom:.4rem; }}
.cr-badge {{ display:inline-block; padding: 2px 10px; border-radius:999px; 
             font-weight:600; font-size:.9rem; color:#fff; background:{badge_color}; }}
.cr-title {{ font-weight:600; color:#111827; }}
.cr-list {{ margin:.25rem 0 0 0; padding-left: 1.1rem; }}
.cr-list li {{ margin:.2rem 0; color:#374151; line-height:1.3; }}
</style>
"""

    # Build list items
    items_html = (
        "".join(f"<li>{s}</li>" for s in steps) or "<li>No explanation provided.</li>"
    )

    # Wrap up
    html_snippet = f"""
{style}
<div class="cr-card" role="region" aria-label="Classification result">
  <div class="cr-header">
    <span class="cr-badge">{html.escape(label)}</span>
    <span class="cr-title">Decision trace</span>
  </div>
  <ul class="cr-list">
    {items_html}
  </ul>
</div>
""".strip()

    return html_snippet
