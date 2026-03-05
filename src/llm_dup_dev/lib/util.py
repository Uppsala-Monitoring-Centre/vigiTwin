import pandas as pd
import json
import re
import math
from typing import Optional, Dict, Any
import llm_dup_dev.lib.fetch_study as fs
# ---- computations (same logic as your text version) ----


def read_file(file_name):
    try:
        with open(file_name, "r") as file:
            text = file.read()
        return text
    except FileNotFoundError:
        print(f"Error: File '{file_name}' not found.")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None


def extract_json_from_GPTresponse(resp: str) -> dict:
    # First try fenced code blocks
    match = re.search(r"```json\s*(\{.*?\})\s*```", resp, re.DOTALL)
    if match:
        json_str = match.group(1)
    else:
        # Otherwise try to grab the first {...} block
        match = re.search(r"\{.*\}", resp, re.DOTALL)
        if not match:
            raise ValueError("No JSON object found in response")
        json_str = match.group(0)

    return json.loads(json_str)


def is_missing(value):
    """Return True if the value should be treated as missing (None, NaN, NaT, or literal 'None')."""
    if value is None:
        return True
    # Pandas null-like types
    if isinstance(value, (pd._libs.tslibs.nattype.NaTType, pd._libs.missing.NAType)):
        return True
    # NaN from float conversions
    if isinstance(value, float) and math.isnan(value):
        return True
    # Literal string 'None' or empty string
    if isinstance(value, str) and value.strip().lower() in {
        "none",
        "",
        "unk",
        "unknown",
        "unk()",
        "(unk())",
        "()",
        "(())",
        "(unk ())",
    }:
        return True
    return False


def _normalize_title(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    # Lowercase, remove punctuation/extra spaces, collapse unicode spaces
    import re

    t = title.casefold()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s]", "", t)  # keep letters/digits/underscore/space
    t = t.strip()
    return t or None


def _same_study(meta1: Dict[str, Any], meta2: Dict[str, Any]) -> bool:
    """
    Decide if two metadata blobs describe the same study.
    Priority: DOI -> PMID -> PMCID -> normalized title (optional).
    """
    doi1 = (meta1.get("doi") or "").strip().lower()
    doi2 = (meta2.get("doi") or "").strip().lower()
    if doi1 and doi2 and doi1 == doi2:
        return True

    pmid1 = (str(meta1.get("pmid")) if meta1.get("pmid") is not None else "").strip()
    pmid2 = (str(meta2.get("pmid")) if meta2.get("pmid") is not None else "").strip()
    if pmid1 and pmid2 and pmid1 == pmid2:
        return True

    pmcid1 = (
        (str(meta1.get("pmcid")) if meta1.get("pmcid") is not None else "")
        .strip()
        .lower()
    )
    pmcid2 = (
        (str(meta2.get("pmcid")) if meta2.get("pmcid") is not None else "")
        .strip()
        .lower()
    )
    if pmcid1 and pmcid2 and pmcid1 == pmcid2:
        return True

    # Fallback: normalized title match (useful when IDs are missing)
    t1 = _normalize_title(meta1.get("title"))
    t2 = _normalize_title(meta2.get("title"))
    if t1 and t2 and t1 == t2:
        # Optional: if both have a year, require they match
        y1 = meta1.get("year")
        y2 = meta2.get("year")
        if (y1 is None) or (y2 is None) or (y1 == y2):
            return True

    return False


def fetch_meta(report, threshold=0.85):
    # Collect any candidate strings from the report (tolerate typos/variants)
    fields = ("studyname", "literatureeference")
    parts = []
    for f in fields:
        val = getattr(report, f, None)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())

    if not parts:
        return None

    merged = " | ".join(dict.fromkeys(parts))  # dedupe, preserve order

    # Pass merged to both fields so DOI/year/title can be found no matter where it lives
    return fs.fetch_article(study_name=merged, reference=merged, threshold=threshold)


def Is_SameStudy(report1, report2):
    if (report1.literatureeference or report1.studyname) and (
        report2.literatureeference or report2.studyname
    ):
        report_1_meta = fetch_meta(report1, 0.7)
        report_2_meta = fetch_meta(report2, 0.7)

        if report_1_meta["ok"] and report_2_meta["ok"]:
            if _same_study(report_1_meta["metadata"], report_2_meta["metadata"]):
                return report_1_meta["metadata"]
    return False
