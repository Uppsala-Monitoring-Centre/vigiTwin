from sqlalchemy import text as sql_text, bindparam
import pandas as pd
from umc_research.datascience.database import get_database_engine
from joblib import Memory

# ======================================================================
# CACHE SETUP
# ======================================================================
# Caches query results to disk so repeated calls with the same
# drug/reaction names skip the database entirely.
#
# To clear (e.g. after weekly DB refresh):
#   demographics_cache.clear()
# Or just delete the .demographics_cache folder manually.
# ======================================================================
demographics_cache = Memory(location="./.demographics_cache", verbose=1)

# ======================================================================
# SHARED AGGREGATION TAIL
# ======================================================================
# This block is appended after any CTE chain that exposes a `base` CTE
# with columns: ReportID, age, SourceCountry, sex.
# It produces the compact ~15-row summary consumed by
# summary_of_demographics_html_from_summary().
# ======================================================================

_AGG_TAIL = """
report_counts AS (
    SELECT COUNT(*) AS total_rows, COUNT(DISTINCT ReportID) AS total_reports
    FROM base
),
age_agg AS (
    -- exclude NULL and negative ages (data quality filter)
    SELECT ROUND(AVG(age), 0) AS avg_age
    FROM base
    WHERE age IS NOT NULL AND age >= 0
),
age_percentiles AS (
    SELECT
        ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY age) OVER (), 0) AS q1_age,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY age) OVER (), 0) AS q3_age
    FROM base
    WHERE age IS NOT NULL AND age >= 0
),
age_summary AS (
    SELECT TOP 1
        r.total_rows, r.total_reports, a.avg_age, p.q1_age, p.q3_age
    FROM report_counts r
    CROSS JOIN age_agg a
    CROSS JOIN age_percentiles p
),
total_count AS (SELECT COUNT(*) AS n FROM base),
sex_counts AS (
    SELECT sex, COUNT(*) AS cnt
    FROM base
    GROUP BY sex
),
country_ranked AS (
    SELECT
        SourceCountry,
        COUNT(*)                                                          AS cnt,
        ROUND(100.0 * COUNT(*) / (SELECT n FROM total_count), 0)         AS pct,
        ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC)                        AS rn
    FROM base
    GROUP BY SourceCountry
),
year_ranked AS (
    SELECT
        YEAR(rep.UMCCalculated_DateDatabase)                              AS yr,
        COUNT(*)                                                          AS cnt,
        ROUND(100.0 * COUNT(*) / (SELECT n FROM total_count), 0)         AS pct,
        ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC)                        AS rn
    FROM matched_both mr
    JOIN [UMCReport].[Report] rep ON rep.ReportID = mr.ReportID
    WHERE rep.UMCCalculated_DateDatabase IS NOT NULL
    GROUP BY YEAR(rep.UMCCalculated_DateDatabase)
)
SELECT 'overview' AS section, 'total_reports' AS metric, NULL AS label,
       CAST(a.total_reports AS FLOAT) AS value, NULL AS pct FROM age_summary a
UNION ALL SELECT 'overview','avg_age', NULL, a.avg_age,  NULL FROM age_summary a
UNION ALL SELECT 'overview','q1_age',  NULL, a.q1_age,   NULL FROM age_summary a
UNION ALL SELECT 'overview','q3_age',  NULL, a.q3_age,   NULL FROM age_summary a
UNION ALL
SELECT 'sex', sex, NULL, CAST(cnt AS FLOAT),
       ROUND(100.0 * cnt / (SELECT n FROM total_count), 0)
FROM sex_counts
UNION ALL
SELECT 'country', CAST(rn AS VARCHAR), SourceCountry, CAST(cnt AS FLOAT), pct
FROM country_ranked  -- no limit: reporter country may rank anywhere
UNION ALL
SELECT 'year', CAST(rn AS VARCHAR), CAST(yr AS VARCHAR), CAST(cnt AS FLOAT), pct
FROM year_ranked WHERE rn <= 3;
"""



# ======================================================================
# BASE JOIN SNIPPET  (shared by all three queries)
# ======================================================================
# Joins whatever is in `matched_both` to the demographics tables.
# ======================================================================

_BASE_JOIN = """
base AS (
    SELECT
        rep.ReportID,
        TRY_CONVERT(FLOAT, pat.UMCValidated_PatientOnsetAge) AS age,
        country.CountryOfficialName                          AS SourceCountry,
        S.[Description]                                      AS sex
    FROM matched_both mr
    JOIN  [UMCReport].[Report]   rep ON rep.ReportID  = mr.ReportID
    LEFT JOIN [UMCReport].[Patient]  pat ON pat.ReportID  = mr.ReportID
    LEFT JOIN [Lexicon].[Country] country
           ON country.CountryID = rep.UMCValidated_PrimarySourceCountryID
    LEFT JOIN [Lexicon].[Sex] S
           ON S.SexID = TRY_CONVERT(TINYINT, pat.PatientSex)
),
"""


# ======================================================================
# QUERY 1 — drugs only
# matched_both is just an alias so the shared tail works unchanged.
# ======================================================================

_SQL_DRUGS_ONLY = sql_text("""
WITH matched_both AS (
    SELECT ReportID
    FROM [UMCReport].[Drug]
    WHERE UMCValidated_BaseCompositionName IN :drug_names
    GROUP BY ReportID
    HAVING COUNT(DISTINCT UMCValidated_BaseCompositionName) = :n_drugs
),
""" + _BASE_JOIN + _AGG_TAIL).bindparams(
    bindparam("drug_names", expanding=True),
    bindparam("n_drugs"),
)


# ======================================================================
# QUERY 2 — reactions only
# ======================================================================

_SQL_REACTIONS_ONLY = sql_text("""
WITH pt_map AS (
    SELECT DISTINCT PT_CODE, PT_NAME
    FROM MeddraWeekly.Meddra.MEDDRA_MD_HIERARCHY
    WHERE PT_NAME IN :reaction_names
),
matched_both AS (
    SELECT re.ReportID
    FROM [UMCReport].[Reaction] AS re
    JOIN pt_map m ON m.PT_CODE = re.UMCValidated_ReactionMeddraPtCode
    GROUP BY re.ReportID
    HAVING COUNT(DISTINCT m.PT_NAME) = :n_reactions
),
""" + _BASE_JOIN + _AGG_TAIL).bindparams(
    bindparam("reaction_names", expanding=True),
    bindparam("n_reactions"),
)


# ======================================================================
# QUERY 3 — intersection: reports that have ALL drugs AND ALL reactions
# ======================================================================

_SQL_DRUGS_AND_REACTIONS = sql_text("""
WITH pt_map AS (
    SELECT DISTINCT PT_CODE, PT_NAME
    FROM MeddraWeekly.Meddra.MEDDRA_MD_HIERARCHY
    WHERE PT_NAME IN :reaction_names
),
matched_drugs AS (
    SELECT ReportID
    FROM [UMCReport].[Drug]
    WHERE UMCValidated_BaseCompositionName IN :drug_names
    GROUP BY ReportID
    HAVING COUNT(DISTINCT UMCValidated_BaseCompositionName) = :n_drugs
),
matched_reactions AS (
    SELECT re.ReportID
    FROM [UMCReport].[Reaction] AS re
    JOIN pt_map m ON m.PT_CODE = re.UMCValidated_ReactionMeddraPtCode
    GROUP BY re.ReportID
    HAVING COUNT(DISTINCT m.PT_NAME) = :n_reactions
),
matched_both AS (
    -- pure SQL intersection — no Python set operations needed
    SELECT ReportID FROM matched_drugs
    INTERSECT
    SELECT ReportID FROM matched_reactions
),
""" + _BASE_JOIN + _AGG_TAIL).bindparams(
    bindparam("drug_names",     expanding=True),
    bindparam("reaction_names", expanding=True),
    bindparam("n_drugs"),
    bindparam("n_reactions"),
)


# ======================================================================
# FETCH HELPERS
# Each returns the compact ~15-row summary DataFrame.
# ======================================================================

def _run(sql, params, target_db):
    engine = get_database_engine(target_db, driver="{ODBC Driver 18 for SQL Server}")
    return pd.read_sql(sql, engine, params=params)


@demographics_cache.cache
def fetch_drug_demographics_summary(
    drug_names: tuple[str], target_db: str
) -> pd.DataFrame:
    # NOTE: drug_names is a tuple (not list) so joblib can hash it as a cache key
    return _run(
        _SQL_DRUGS_ONLY,
        {"drug_names": list(drug_names), "n_drugs": len(drug_names)},
        target_db,
    )


@demographics_cache.cache
def fetch_reaction_demographics_summary(
    reaction_names: tuple[str], target_db: str
) -> pd.DataFrame:
    return _run(
        _SQL_REACTIONS_ONLY,
        {"reaction_names": list(reaction_names), "n_reactions": len(reaction_names)},
        target_db,
    )


@demographics_cache.cache
def fetch_drug_reaction_demographics_summary(
    drug_names: tuple[str],
    reaction_names: tuple[str],
    target_db: str,
) -> pd.DataFrame:
    return _run(
        _SQL_DRUGS_AND_REACTIONS,
        {
            "drug_names":     list(drug_names),
            "reaction_names": list(reaction_names),
            "n_drugs":        len(drug_names),
            "n_reactions":    len(reaction_names),
        },
        target_db,
    )
