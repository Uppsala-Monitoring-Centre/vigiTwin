#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetch_study.py (high-confidence + grants/info fallback)
------------------------------------------------------
Changes:
- Only returns high-confidence matches. Low-confidence hits are discarded.
- If no article is found with high confidence, tries to find GRANTS/FUNDERS/INFO
  (Europe PMC Grants, Crossref Funder registry) and returns a concise summary.
- Always returns an abstract/summary for high-confidence results; otherwise returns nothing.
- Confidence threshold is configurable per call and via CLI flag --threshold,
  and via env FETCH_STUDY_HIGH_CONFIDENCE (default 0.85).
- DOI/year detection now looks across BOTH 'study_name' and 'reference' inputs.

Confidence:
- Tunable via threshold (default DEFAULT_HIGH_CONFIDENCE).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher

# --------------------------
# Configuration & Utilities
# --------------------------

DEFAULT_TIMEOUT = 20
HEADERS = {
    "User-Agent": "StudyFetcher/1.2 (+github.com/your-org) Python requests",
    "Accept": "application/json, text/plain, */*",
}

UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL", "").strip()
NCBI_TOOL = os.getenv("NCBI_TOOL", "study-fetcher")
NCBI_EMAIL = os.getenv("NCBI_EMAIL", UNPAYWALL_EMAIL or "example@example.com")
NCBI_API_KEY = os.getenv("NCBI_API_KEY", "").strip()

DOWNLOAD_DIR = os.path.abspath(os.path.join(os.getcwd(), "downloads"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPE_PMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
CROSSREF_BASE = "https://api.crossref.org"

# Confidence threshold default (can be overridden per-call or CLI)
DEFAULT_HIGH_CONFIDENCE = float(os.getenv("FETCH_STUDY_HIGH_CONFIDENCE", "0.85"))

# --------------------------
# Data classes
# --------------------------


@dataclass
class StudyHit:
    kind: str  # "article" | "grant" | "funder" | "info"
    source: str
    title: str | None
    authors: list[str] | None
    year: int | None
    doi: str | None
    landing_url: str | None
    pdf_url: str | None
    pmid: str | None
    pmcid: str | None
    confidence: float
    abstract: str | None = None  # For grants/info this is a summary string


# --------------------------
# Small helpers
# --------------------------


def _safe_get(
    url, headers=None, params=None, accept_json=True, timeout=DEFAULT_TIMEOUT
):
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, params=params, timeout=timeout)
        r.raise_for_status()
        if accept_json:
            return r.json()
        return r
    except Exception:
        return None


def _safe_get_text(
    url, headers=None, params=None, timeout=DEFAULT_TIMEOUT
) -> str | None:
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, headers=h, params=params, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception:
        return None


def _extract_year_from_reference(ref: str | None) -> int | None:
    if not ref:
        return None
    this_year = datetime.utcnow().year + 1
    m = re.search(r"\b(18|19|20)\d{2}\b", ref)
    if m:
        y = int(m.group(0))
        if 1800 <= y <= this_year:
            return y
    return None


def _extract_doi(text: str | None) -> str | None:
    if not text:
        return None
    doi_pattern = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
    m = doi_pattern.search(text)
    if m:
        return m.group(1).rstrip(" .;,)")
    return None


def _normalize_title(title: str | None) -> str | None:
    if not title:
        return None
    return re.sub(r"\s+", " ", title).strip()


def _title_confidence(query_title: str | None, candidate_title: str | None) -> float:
    """Fuzzy title similarity in [0,1]."""
    if not query_title or not candidate_title:
        return 0.0
    a = _normalize_title(query_title).lower()
    b = _normalize_title(candidate_title).lower()
    return SequenceMatcher(None, a, b).ratio()


# --------------------------
# Crossref
# --------------------------


def _authors_from_crossref(item) -> list[str]:
    authors = []
    for a in item.get("author", []) or []:
        given = a.get("given") or ""
        family = a.get("family") or ""
        name = (
            (given + " " + family).strip()
            if (given or family)
            else a.get("name", "").strip()
        )
        if name:
            authors.append(name)
    return authors


def _year_from_crossref(item) -> int | None:
    for key in ("published-print", "published-online", "created", "issued"):
        if key in item:
            try:
                parts = item[key].get("date-parts", [[]])[0]
                if parts:
                    return int(parts[0])
            except Exception:
                pass
    return None


def search_crossref(title: str, year_hint: int | None = None) -> StudyHit | None:
    url = f"{CROSSREF_BASE}/works"
    params = {
        "query.title": title,
        "rows": 5,
        "select": "DOI,title,author,issued,published-print,published-online,created,link,type,URL",
        "sort": "score",
        "order": "desc",
    }
    data = _safe_get(url, params=params)
    if not data or "message" not in data or "items" not in data["message"]:
        return None

    items = data["message"]["items"] or []
    best: StudyHit | None = None

    for item in items:
        item_title = " ".join(item.get("title") or []).strip()
        if not item_title:
            continue

        title_match = _title_confidence(title, item_title)
        yr = _year_from_crossref(item)
        year_bonus = 0.05 if (year_hint and yr == year_hint) else 0.0

        doi = item.get("DOI")
        landing_url = item.get("URL")
        pdf_url = None
        for ln in item.get("link", []) or []:
            if (ln.get("content-type") or "").lower() == "application/pdf":
                pdf_url = ln.get("URL")
                break

        conf = min(1.0, title_match + year_bonus)

        hit = StudyHit(
            kind="article",
            source="crossref",
            title=item_title or None,
            authors=_authors_from_crossref(item) or None,
            year=yr,
            doi=doi,
            landing_url=landing_url,
            pdf_url=pdf_url,
            pmid=None,
            pmcid=None,
            confidence=conf,
        )
        if (best is None) or (hit.confidence > best.confidence):
            best = hit

    return best


# --------------------------
# PubMed (NCBI E-utilities)
# --------------------------


def _pubmed_summary(pmid: str):
    esummary = f"{NCBI_BASE}/esummary.fcgi"
    params = {
        "db": "pubmed",
        "id": pmid,
        "retmode": "json",
        "tool": NCBI_TOOL,
        "email": NCBI_EMAIL,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    res = _safe_get(esummary, params=params)
    if not res:
        return None
    return (res.get("result") or {}).get(pmid) or None


def _year_from_pubmed_summary(meta) -> int | None:
    if not meta:
        return None
    for key in ("pubdate", "sortpubdate", "epubdate"):
        if key in meta and meta[key]:
            try:
                dt = dateparser.parse(meta[key], default=datetime(1900, 1, 1))
                return dt.year
            except Exception:
                pass
    return None


def _authors_from_pubmed_summary(meta) -> list[str]:
    out = []
    for a in meta.get("authors") or []:
        name = a.get("name") or ""
        if name:
            out.append(name)
    return out


def _doi_from_pubmed_summary(meta) -> str | None:
    for idobj in meta.get("articleids") or []:
        if idobj.get("idtype") == "doi":
            return idobj.get("value")
    return None


def _pmcid_from_pubmed(pmid: str) -> str | None:
    elink = f"{NCBI_BASE}/elink.fcgi"
    params = {
        "dbfrom": "pubmed",
        "db": "pmc",
        "id": pmid,
        "retmode": "json",
        "tool": NCBI_TOOL,
        "email": NCBI_EMAIL,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    res = _safe_get(elink, params=params)
    try:
        linksets = res["linksets"][0]["linksetdbs"]
        for ls in linksets:
            if ls["dbto"] == "pmc":
                for l in ls.get("links", []):
                    pmcid = l.get("id")
                    if pmcid and not pmcid.startswith("PMC"):
                        pmcid = "PMC" + pmcid
                    return pmcid
    except Exception:
        return None
    return None


def search_pubmed(title: str, year_hint: int | None = None) -> StudyHit | None:
    esearch = f"{NCBI_BASE}/esearch.fcgi"
    params = {
        "db": "pubmed",
        "term": f"{title}[Title]",
        "retmode": "json",
        "retmax": "5",
        "tool": NCBI_TOOL,
        "email": NCBI_EMAIL,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    res = _safe_get(esearch, params=params)
    if not res:
        return None
    ids = (res.get("esearchresult") or {}).get("idlist") or []
    if not ids:
        return None

    # Pick best ID by fuzzy title + year if available
    best = None
    best_conf = -1.0
    chosen_meta = None
    for pmid in ids:
        meta = _pubmed_summary(pmid)
        if not meta:
            continue
        cand_title = (meta.get("title") or "").strip().rstrip(".")
        conf = _title_confidence(title, cand_title)
        y = _year_from_pubmed_summary(meta)
        if year_hint and y == year_hint:
            conf = min(1.0, conf + 0.05)
        if conf > best_conf:
            best_conf = conf
            best = pmid
            chosen_meta = meta

    if not best or not chosen_meta:
        return None

    cand_title = (chosen_meta.get("title") or "").strip().rstrip(".")
    authors = _authors_from_pubmed_summary(chosen_meta)
    yr = _year_from_pubmed_summary(chosen_meta)
    doi = _doi_from_pubmed_summary(chosen_meta)
    landing = f"https://pubmed.ncbi.nlm.nih.gov/{best}/"
    pmcid = _pmcid_from_pubmed(best)

    pdf_url = None
    if pmcid:
        pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf"

    abstract = _pubmed_abstract_by_pmid(best)

    return StudyHit(
        kind="article",
        source="pubmed",
        title=cand_title or None,
        authors=authors or None,
        year=yr,
        doi=doi,
        landing_url=landing,
        pdf_url=pdf_url,
        pmid=best,
        pmcid=pmcid,
        confidence=best_conf,
        abstract=abstract,
    )


# --------------------------
# OA helpers (Unpaywall, OpenAlex)
# --------------------------


def unpaywall_best_oa(doi: str) -> tuple[str | None, str | None]:
    if not doi or not UNPAYWALL_EMAIL:
        return (None, None)
    url = f"https://api.unpaywall.org/v2/{quote(doi)}"
    params = {"email": UNPAYWALL_EMAIL}
    data = _safe_get(url, params=params)
    if not data:
        return (None, None)

    landing = data.get("best_oa_location", {}) or {}
    landing_url = landing.get("url")
    pdf_url = landing.get("url_for_pdf") or None

    if not pdf_url:
        for loc in data.get("oa_locations", []) or []:
            if loc.get("url_for_pdf"):
                pdf_url = loc["url_for_pdf"]
                if not landing_url:
                    landing_url = loc.get("url")
                break

    return (landing_url, pdf_url)


def openalex_best_oa(doi: str) -> tuple[str | None, str | None]:
    if not doi:
        return (None, None)
    url = f"https://api.openalex.org/works/https://doi.org/{quote(doi)}"
    data = _safe_get(url)
    if not data:
        return (None, None)

    pl = data.get("primary_location") or {}
    if pl.get("is_oa") and (pl.get("landing_page_url") or pl.get("pdf_url")):
        return (pl.get("landing_page_url"), pl.get("pdf_url"))

    for loc in data.get("locations") or []:
        if loc.get("is_oa"):
            landing = loc.get("landing_page_url") or None
            pdf_url = loc.get("pdf_url") or None
            if landing or pdf_url:
                return (landing, pdf_url)

    return (None, None)


# --------------------------
# DOI resolution & publisher discovery
# --------------------------


def resolve_doi_landing(doi: str) -> str | None:
    if not doi:
        return None
    url = f"https://doi.org/{doi}"
    try:
        r = requests.get(
            url, headers=HEADERS, allow_redirects=True, timeout=DEFAULT_TIMEOUT
        )
        if 200 <= r.status_code < 400:
            return r.url
    except Exception:
        pass
    return None


def discover_publisher_pdf(landing_url: str) -> str | None:
    if not landing_url:
        return None
    try:
        r = requests.get(landing_url, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
        if not (200 <= r.status_code < 400):
            return None
        if "text/html" not in r.headers.get("Content-Type", "").lower():
            return None
    except Exception:
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    candidates = []

    meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
    if meta and meta.get("content"):
        candidates.append(meta["content"])

    for link in soup.find_all("link", attrs={"rel": True, "type": True}):
        try:
            rels = " ".join(link.get("rel") or []).lower()
        except Exception:
            rels = ""
        typ = (link.get("type") or "").lower()
        href = link.get("href")
        if "alternate" in rels and "application/pdf" in typ and href:
            candidates.append(href)

    for url in candidates:
        try:
            test_url = url
            if test_url.startswith("/"):
                parsed = urlparse(landing_url)
                test_url = f"{parsed.scheme}://{parsed.netloc}{test_url}"

            h = requests.head(
                test_url, headers=HEADERS, allow_redirects=True, timeout=DEFAULT_TIMEOUT
            )
            if (
                200 <= h.status_code < 300
                and "application/pdf" in h.headers.get("Content-Type", "").lower()
            ):
                return test_url

            g = requests.get(
                test_url, headers=HEADERS, stream=True, timeout=DEFAULT_TIMEOUT
            )
            ct = g.headers.get("Content-Type", "").lower()
            g.close()
            if 200 <= g.status_code < 300 and "application/pdf" in ct:
                return test_url
        except Exception:
            continue

    return None


# --------------------------
# Abstract helpers
# --------------------------


def _pubmed_abstract_by_pmid(pmid: str) -> str | None:
    params = {
        "db": "pubmed",
        "retmode": "xml",
        "id": pmid,
        "tool": NCBI_TOOL,
        "email": NCBI_EMAIL,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    text = _safe_get_text(f"{NCBI_BASE}/efetch.fcgi", params=params)
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except Exception:
        return None

    abstract_texts = []
    for abst in root.findall(".//AbstractText"):
        label = abst.attrib.get("Label") or abst.attrib.get("NlmCategory")
        part = (abst.text or "").strip()
        if label and part:
            abstract_texts.append(f"{label}: {part}")
        elif part:
            abstract_texts.append(part)

    return "\n\n".join(abstract_texts).strip() if abstract_texts else None


def _pubmed_pmid_from_doi(doi: str) -> str | None:
    params = {
        "db": "pubmed",
        "retmode": "json",
        "term": f"{doi}[DOI] OR {doi}[AID]",
        "tool": NCBI_TOOL,
        "email": NCBI_EMAIL,
    }
    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY
    data = _safe_get(f"{NCBI_BASE}/esearch.fcgi", params=params)
    ids = (data or {}).get("esearchresult", {}).get("idlist", [])
    return ids[0] if ids else None


def _europe_pmc_abstract_by_doi(doi: str) -> str | None:
    params = {"query": f"DOI:{doi}", "format": "json", "pageSize": 1}
    data = _safe_get(f"{EUROPE_PMC_BASE}/search", params=params)
    results = (data or {}).get("resultList", {}).get("result", [])
    if not results:
        return None
    abstract = results[0].get("abstractText")
    return abstract.strip() if abstract else None


def _europe_pmc_abstract_by_title(
    title: str, year_hint: int | None = None
) -> tuple[str | None, float]:
    """
    Europe PMC title-based abstract search with a conservative confidence score.
    """
    if not title:
        return (None, 0.0)
    query = f'TITLE:"{title}"'
    if year_hint:
        query += f" AND PUB_YEAR:{year_hint}"
    params = {"query": query, "format": "json", "pageSize": 1}
    data = _safe_get(f"{EUROPE_PMC_BASE}/search", params=params)
    results = (data or {}).get("resultList", {}).get("result", [])
    if not results:
        return (None, 0.0)
    cand_title = (results[0].get("title") or "").strip()
    conf = _title_confidence(title, cand_title)
    abstract = results[0].get("abstractText")
    return (abstract.strip() if abstract else None, conf)


def _abstract_by_doi(doi: str) -> str | None:
    if not doi:
        return None
    pmid = _pubmed_pmid_from_doi(doi)
    if pmid:
        abs_txt = _pubmed_abstract_by_pmid(pmid)
        if abs_txt:
            return abs_txt
    return _europe_pmc_abstract_by_doi(doi)


# --------------------------
# Grants & funder info fallback
# --------------------------


def search_europe_pmc_grants(query: str) -> StudyHit | None:
    """
    Search Europe PMC Grants for the query string and return a summary.
    """
    params = {"search": query, "format": "json", "pageSize": 3}
    data = _safe_get(f"{EUROPE_PMC_BASE}/grants", params=params)
    results = (data or {}).get("resultList", {}).get("result", [])
    if not results:
        return None

    # Pick the best by fuzzy title match if possible
    best = None
    best_conf = -1.0
    for r in results:
        title = (r.get("title") or "").strip()
        conf = _title_confidence(query, title) if title else 0.6  # generic baseline
        if conf > best_conf:
            best_conf = conf
            best = r

    if not best:
        return None

    title = (best.get("title") or "").strip() or None
    funder = best.get("funderName") or best.get("fundingAgency") or ""
    grant_id = best.get("grantId") or best.get("projectNumber") or ""
    start = best.get("startDate") or ""
    end = best.get("endDate") or ""
    pi = best.get("pi") or best.get("principalInvestigator") or ""
    abs_txt = (best.get("abstractText") or best.get("laySummary") or "").strip()

    # Build a compact summary mapped to 'abstract'
    parts = []
    if funder:
        parts.append(f"Funder: {funder}")
    if grant_id:
        parts.append(f"Grant ID: {grant_id}")
    if pi:
        parts.append(f"PI: {pi}")
    if start or end:
        parts.append(f"Dates: {start} – {end}")
    if abs_txt:
        parts.append(f"Summary: {abs_txt}")
    summary = "\n".join(parts) if parts else None

    return StudyHit(
        kind="grant",
        source="europe_pmc_grants",
        title=title,
        authors=None,
        year=int(start[:4]) if start[:4].isdigit() else None,
        doi=None,
        landing_url=None,
        pdf_url=None,
        pmid=None,
        pmcid=None,
        confidence=best_conf,
        abstract=summary,
    )


def search_crossref_funder_info(query: str) -> StudyHit | None:
    """
    Probe Crossref Funder registry for a funder/org that matches the query,
    returning a brief summary (name + top-level location if available).
    """
    url = f"{CROSSREF_BASE}/funders"
    params = {"query": query, "rows": 5}
    data = _safe_get(url, params=params)
    if not data:
        return None
    items = (data.get("message") or {}).get("items", []) or []
    if not items:
        return None

    best = None
    best_conf = -1.0
    for it in items:
        name = it.get("name") or ""
        conf = _title_confidence(query, name)
        if conf > best_conf:
            best_conf = conf
            best = it

    if not best:
        return None

    name = best.get("name") or None
    location = (best.get("location") or "").strip()
    alt_names = (
        ", ".join(best.get("alt-names", [])[:3]) if best.get("alt-names") else ""
    )
    summary_parts = []
    if name:
        summary_parts.append(f"Funder: {name}")
    if location:
        summary_parts.append(f"Location: {location}")
    if alt_names:
        summary_parts.append(f"Also known as: {alt_names}")
    summary = "\n".join(summary_parts) if summary_parts else None

    return StudyHit(
        kind="funder",
        source="crossref_funder",
        title=name,
        authors=None,
        year=None,
        doi=None,
        landing_url=None,
        pdf_url=None,
        pmid=None,
        pmcid=None,
        confidence=best_conf,
        abstract=summary,
    )


# --------------------------
# Download utilities
# --------------------------


def download_pdf(pdf_url: str, filename_hint: str) -> str | None:
    if not pdf_url:
        return None
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename_hint)[:200]
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    out_path = os.path.join(DOWNLOAD_DIR, safe_name)

    try:
        with requests.get(
            pdf_url, headers=HEADERS, stream=True, timeout=DEFAULT_TIMEOUT
        ) as r:
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "").lower()
            if "pdf" not in ct and not pdf_url.lower().endswith(".pdf"):
                return None
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return out_path
    except Exception:
        return None


# --------------------------
# Main orchestration
# --------------------------


def _apply_oa_and_abstract(hit: StudyHit) -> StudyHit:
    """Augment an article hit with OA location and abstract if possible."""
    if hit.kind != "article":
        return hit

    if hit.doi:
        oa_landing, oa_pdf = unpaywall_best_oa(hit.doi)
        if not oa_pdf and not oa_landing:
            oa_landing, oa_pdf = openalex_best_oa(hit.doi)
        pdf_from_publisher = None
        if not oa_pdf and (oa_landing or hit.landing_url):
            pdf_from_publisher = discover_publisher_pdf(oa_landing or hit.landing_url)
        hit.landing_url = oa_landing or hit.landing_url
        hit.pdf_url = oa_pdf or pdf_from_publisher or hit.pdf_url

    # Fill abstract if missing
    if not hit.abstract:
        if hit.pmid:
            hit.abstract = _pubmed_abstract_by_pmid(hit.pmid) or hit.abstract
        if not hit.abstract and hit.doi:
            hit.abstract = _abstract_by_doi(hit.doi) or hit.abstract

    return hit


def find_study(
    study_name: str | None, reference: str | None = None, threshold: float | None = None
) -> StudyHit | None:
    """
    Strategy:
    1) If DOI present anywhere (study_name or reference), treat as high-confidence anchor.
    2) Crossref search by title (+year).
    3) PubMed search by title (+year).
    Returns the BEST candidate (may still be low confidence).
    """
    threshold = (
        DEFAULT_HIGH_CONFIDENCE if threshold is None else max(0.0, min(1.0, threshold))
    )

    study_name = _normalize_title(study_name)
    # Look for year/doi in EITHER field by merging them
    merged_text = " ".join([reference or "", study_name or ""]).strip()
    year_hint = _extract_year_from_reference(merged_text)
    doi = _extract_doi(merged_text)

    # 1) DOI present? Treat as quasi-high confidence (with title-based adjustment if possible)
    if doi:
        landing = resolve_doi_landing(doi)
        hit = StudyHit(
            kind="article",
            source="doi",
            title=study_name,  # keep user-provided title if any
            authors=None,
            year=year_hint,
            doi=doi,
            landing_url=landing,
            pdf_url=None,
            pmid=None,
            pmcid=None,
            confidence=0.95,
            abstract=None,
        )
        hit = _apply_oa_and_abstract(hit)
        return hit

    # 2) Crossref
    if study_name:
        crossref_hit = search_crossref(study_name, year_hint=year_hint)
        if crossref_hit:
            crossref_hit = _apply_oa_and_abstract(crossref_hit)
            if crossref_hit.abstract and crossref_hit.confidence >= threshold:
                return crossref_hit

    # 3) PubMed
    if study_name:
        pm_hit = search_pubmed(study_name, year_hint=year_hint)
        if pm_hit:
            pm_hit = _apply_oa_and_abstract(pm_hit)
            if pm_hit.abstract and pm_hit.confidence >= threshold:
                return pm_hit

    # If we reached here, we may still have a candidate, but below threshold.
    return None


def fetch_article(
    study_name: str | None, reference: str | None = None, threshold: float | None = None
) -> dict:
    """
    High-level entry:
      - Returns only high-confidence article (with abstract) OR high-confidence grant/info (with summary).
      - Otherwise returns nothing (ok: False).
    """
    threshold = (
        DEFAULT_HIGH_CONFIDENCE if threshold is None else max(0.0, min(1.0, threshold))
    )

    # Try article flow
    hit = find_study(study_name, reference, threshold=threshold)

    # If article candidate exists BUT below threshold or missing abstract, treat as no result
    if hit and hit.kind == "article":
        if (hit.confidence < threshold) or not hit.abstract:
            hit = None

    if hit:
        # Optionally download OA PDF (only if we already decided it's high-confidence)
        local_pdf = None
        if hit.pdf_url:
            fn_title = hit.title or (study_name or "paper")
            if hit.year:
                fn_title = f"{fn_title}_{hit.year}"
            if hit.doi:
                short_doi = hit.doi.replace("/", "_")
                fn_title = f"{fn_title}_{short_doi}"
            local_pdf = download_pdf(hit.pdf_url, fn_title)
        return {"ok": True, "metadata": asdict(hit), "downloaded_pdf_path": local_pdf}

    # ---------- Grants / info fallback ----------
    merged_text = " ".join([reference or "", study_name or ""]).strip()
    query = _normalize_title(merged_text) or _normalize_title(study_name) or study_name

    grant_hit = search_europe_pmc_grants(query)
    if grant_hit and grant_hit.abstract and grant_hit.confidence >= threshold:
        return {"ok": True, "metadata": asdict(grant_hit), "downloaded_pdf_path": None}

    funder_hit = search_crossref_funder_info(query)
    if funder_hit and funder_hit.abstract and funder_hit.confidence >= threshold:
        return {"ok": True, "metadata": asdict(funder_hit), "downloaded_pdf_path": None}

    # Last-resort: try EPMC title abstract, but only accept if high-confidence
    title_norm = _normalize_title(study_name)
    year_hint = _extract_year_from_reference(merged_text)
    epmc_abs, epmc_conf = _europe_pmc_abstract_by_title(title_norm, year_hint=year_hint)
    if epmc_abs and epmc_conf >= threshold:
        orphan = StudyHit(
            kind="article",
            source="europe_pmc_title_fallback",
            title=title_norm,
            authors=None,
            year=year_hint,
            doi=None,
            landing_url=None,
            pdf_url=None,
            pmid=None,
            pmcid=None,
            confidence=epmc_conf,
            abstract=epmc_abs,
        )
        return {"ok": True, "metadata": asdict(orphan), "downloaded_pdf_path": None}

    # Nothing confident enough
    return {
        "ok": False,
        "error": "No high-confidence article or grant/info summary found.",
    }


# --------------------------
# CLI
# --------------------------


def _cli():
    parser = argparse.ArgumentParser(
        description="Find and download a study article by title/reference (high-confidence only)."
    )
    parser.add_argument(
        "--study", required=True, help="Study name/title (free text is fine)"
    )
    parser.add_argument(
        "--reference",
        default="",
        help="Literature reference text (optional, DOI allowed)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_HIGH_CONFIDENCE,
        help=f"Confidence threshold in [0,1] (default {DEFAULT_HIGH_CONFIDENCE})",
    )
    args = parser.parse_args()

    result = fetch_article(args.study, args.reference, threshold=args.threshold)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not result.get("ok"):
        print("❌ No high-confidence abstract/summary found.")
        sys.exit(1)

    meta = result["metadata"]
    print("✅ High-confidence result")
    print(f"  Kind       : {meta.get('kind')}")
    print(f"  Source     : {meta.get('source')}")
    print(f"  Title      : {meta.get('title')}")
    print(f"  Authors    : {', '.join(meta.get('authors') or [])}")
    print(f"  Year       : {meta.get('year')}")
    print(f"  DOI        : {meta.get('doi')}")
    print(f"  Landing    : {meta.get('landing_url')}")
    print(f"  PDF URL    : {meta.get('pdf_url')}")
    print(f"  PMID/PMCID : {meta.get('pmid')} / {meta.get('pmcid')}")
    print(f"  Confidence : {meta.get('confidence'):.2f}")

    abstract = meta.get("abstract")
    if abstract:
        print("\n--- Abstract / Summary ---\n")
        print(abstract)

    if result.get("downloaded_pdf_path"):
        print(f"\n📄 Downloaded: {result['downloaded_pdf_path']}")
    else:
        if meta.get("kind") == "article":
            print(
                "\nℹ️ No Open-Access PDF was fetched. Try the landing page or your institution's access."
            )


if __name__ == "__main__":
    _cli()
