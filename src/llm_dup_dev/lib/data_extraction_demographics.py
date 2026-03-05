from sqlalchemy import text as text, bindparam
import pandas as pd
from umc_research.datascience.database import get_database_engine
#from joblib import Memory

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
#demographics_cache = Memory(location="./.testdemographics_cache", verbose=1)

_SETUP_DRUGS_ONLY = """
SELECT ReportID
INTO #matched
FROM [UMCReport].[Drug]
WHERE UMCValidated_BaseCompositionName IN ({drug_placeholders})
GROUP BY ReportID
HAVING COUNT(DISTINCT UMCValidated_BaseCompositionName) = :n_drugs;
-- ;; --
CREATE INDEX ix_matched_rid ON #matched(ReportID);
-- ;; --
SELECT
    rep.ReportID,
    TRY_CONVERT(FLOAT, pat.UMCValidated_PatientOnsetAge) AS age,
    country.CountryOfficialName                          AS SourceCountry,
    S.[Description]                                      AS sex,
    rep.UMCCalculated_DateDatabase                       AS report_date
INTO #base
FROM #matched mr
JOIN  [UMCReport].[Report]   rep     ON rep.ReportID  = mr.ReportID
LEFT JOIN [UMCReport].[Patient]  pat ON pat.ReportID  = mr.ReportID
LEFT JOIN [Lexicon].[Country] country
       ON country.CountryID = rep.UMCValidated_PrimarySourceCountryID
LEFT JOIN [Lexicon].[Sex] S
       ON S.SexID = TRY_CONVERT(TINYINT, pat.PatientSex);
"""

_SETUP_REACTIONS_ONLY = """
SELECT DISTINCT PT_CODE, PT_NAME
INTO #pt_map
FROM MeddraWeekly.Meddra.MEDDRA_MD_HIERARCHY
WHERE PT_NAME IN ({reaction_placeholders});

SELECT re.ReportID
INTO #matched
FROM [UMCReport].[Reaction] AS re
JOIN #pt_map m ON m.PT_CODE = re.UMCValidated_ReactionMeddraPtCode
GROUP BY re.ReportID
HAVING COUNT(DISTINCT m.PT_NAME) = :n_reactions;
-- ;; --
CREATE INDEX ix_matched_rid ON #matched(ReportID);
-- ;; --
SELECT
    rep.ReportID,
    TRY_CONVERT(FLOAT, pat.UMCValidated_PatientOnsetAge) AS age,
    country.CountryOfficialName                          AS SourceCountry,
    S.[Description]                                      AS sex,
    rep.UMCCalculated_DateDatabase                       AS report_date
INTO #base
FROM #matched mr
JOIN  [UMCReport].[Report]   rep     ON rep.ReportID  = mr.ReportID
LEFT JOIN [UMCReport].[Patient]  pat ON pat.ReportID  = mr.ReportID
LEFT JOIN [Lexicon].[Country] country
       ON country.CountryID = rep.UMCValidated_PrimarySourceCountryID
LEFT JOIN [Lexicon].[Sex] S
       ON S.SexID = TRY_CONVERT(TINYINT, pat.PatientSex);
"""

_SETUP_DRUGS_AND_REACTIONS = """
SELECT DISTINCT PT_CODE, PT_NAME
INTO #pt_map
FROM MeddraWeekly.Meddra.MEDDRA_MD_HIERARCHY
WHERE PT_NAME IN ({reaction_placeholders});

SELECT ReportID
INTO #matched_drugs
FROM [UMCReport].[Drug]
WHERE UMCValidated_BaseCompositionName IN ({drug_placeholders})
GROUP BY ReportID
HAVING COUNT(DISTINCT UMCValidated_BaseCompositionName) = :n_drugs;
-- ;; --
SELECT re.ReportID
INTO #matched_reactions
FROM [UMCReport].[Reaction] AS re
JOIN #pt_map m ON m.PT_CODE = re.UMCValidated_ReactionMeddraPtCode
GROUP BY re.ReportID
HAVING COUNT(DISTINCT m.PT_NAME) = :n_reactions;
-- ;; --
SELECT ReportID
INTO #matched
FROM #matched_drugs
INTERSECT
SELECT ReportID FROM #matched_reactions;
-- ;; --
CREATE INDEX ix_matched_rid ON #matched(ReportID);
-- ;; --
SELECT
    rep.ReportID,
    TRY_CONVERT(FLOAT, pat.UMCValidated_PatientOnsetAge) AS age,
    country.CountryOfficialName                          AS SourceCountry,
    S.[Description]                                      AS sex,
    rep.UMCCalculated_DateDatabase                       AS report_date
INTO #base
FROM #matched mr
JOIN  [UMCReport].[Report]   rep     ON rep.ReportID  = mr.ReportID
LEFT JOIN [UMCReport].[Patient]  pat ON pat.ReportID  = mr.ReportID
LEFT JOIN [Lexicon].[Country] country
       ON country.CountryID = rep.UMCValidated_PrimarySourceCountryID
LEFT JOIN [Lexicon].[Sex] S
       ON S.SexID = TRY_CONVERT(TINYINT, pat.PatientSex);
"""


# ======================================================================
# AGGREGATION QUERY
# ======================================================================
# Runs AFTER the setup SQL has materialised #base in the same connection.
# Uses DECLARE scalars so COUNT(*) is computed once, not per-row.
# PERCENTILE_CONT now operates on the small temp table, not the raw joins.
# ======================================================================
_AGG_QUERY = """
DECLARE @n             INT = (SELECT COUNT(*)                FROM #base);
DECLARE @total_reports INT = (SELECT COUNT(DISTINCT ReportID) FROM #base);

WITH age_avg AS (
    SELECT ROUND(AVG(age), 0) AS avg_age
    FROM #base
    WHERE age IS NOT NULL AND age >= 0
),
age_percentiles AS (
    SELECT TOP 1
        ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY age) OVER (), 0) AS q1_age,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY age) OVER (), 0) AS q3_age
    FROM #base
    WHERE age IS NOT NULL AND age >= 0
),
sex_counts AS (
    SELECT sex, COUNT(*) AS cnt
    FROM #base
    GROUP BY sex
),
country_ranked AS (
    SELECT
        SourceCountry,
        COUNT(*) AS cnt,
        ROUND(100.0 * COUNT(*) / @n, 0)            AS pct,
        ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS rn
    FROM #base
    GROUP BY SourceCountry
),
year_ranked AS (
    SELECT
        YEAR(report_date)                          AS yr,
        COUNT(*) AS cnt,
        ROUND(100.0 * COUNT(*) / @n, 0)            AS pct,
        ROW_NUMBER() OVER (ORDER BY COUNT(*) DESC) AS rn
    FROM #base
    WHERE report_date IS NOT NULL
    GROUP BY YEAR(report_date)
)
SELECT 'overview' AS section, 'total_reports' AS metric, NULL AS label,
       CAST(@total_reports AS FLOAT) AS value, NULL AS pct
UNION ALL SELECT 'overview', 'avg_age', NULL, CAST(avg_age AS FLOAT), NULL FROM age_avg
UNION ALL SELECT 'overview', 'q1_age',  NULL, CAST(q1_age  AS FLOAT), NULL FROM age_percentiles
UNION ALL SELECT 'overview', 'q3_age',  NULL, CAST(q3_age  AS FLOAT), NULL FROM age_percentiles
UNION ALL
SELECT 'sex', sex, NULL, CAST(cnt AS FLOAT), ROUND(100.0 * cnt / @n, 0)
FROM sex_counts
UNION ALL
SELECT 'country', CAST(rn AS VARCHAR), SourceCountry, CAST(cnt AS FLOAT), pct
FROM country_ranked
UNION ALL
SELECT 'year', CAST(rn AS VARCHAR), CAST(yr AS VARCHAR), CAST(cnt AS FLOAT), pct
FROM year_ranked WHERE rn <= 3;
"""

# ======================================================================
# PARAMETER BUILDERS
# ======================================================================
# IN-list placeholders must be built dynamically because SQLAlchemy's
# expanding bindparam is not compatible with multi-statement execution
# (which we need so temp tables persist across statements).
# ======================================================================

def _drug_params(drug_names: list[str]) -> tuple[str, dict]:
    """Returns (placeholder_string, params_dict) for drug IN-list."""
    placeholders = ", ".join(f":drug_{i}" for i in range(len(drug_names)))
    params = {f"drug_{i}": name for i, name in enumerate(drug_names)}
    params["n_drugs"] = len(drug_names)
    return placeholders, params


def _reaction_params(reaction_names: list[str]) -> tuple[str, dict]:
    """Returns (placeholder_string, params_dict) for reaction IN-list."""
    placeholders = ", ".join(f":rxn_{i}" for i in range(len(reaction_names)))
    params = {f"rxn_{i}": name for i, name in enumerate(reaction_names)}
    params["n_reactions"] = len(reaction_names)
    return placeholders, params


# ======================================================================
# CORE RUNNER
# ======================================================================
# Executes setup SQL (creates temp tables) and aggregation SQL in the
# SAME connection so SQL Server temp tables remain visible.
# Each statement is executed individually because most ODBC drivers
# do not support multi-statement batches via execute().
# ======================================================================

def _run_with_temps(setup_sql: str, params: dict, target_db: str) -> pd.DataFrame:
    import re

    def _bind(sql: str, params: dict) -> str:
        """Replace :name placeholders with safely-escaped literal values."""
        def replacer(m):
            val = params.get(m.group(1))
            if isinstance(val, str):
                return f"'{val.replace(chr(39), chr(39)*2)}'"
            return str(val)
        return re.sub(r':([a-zA-Z_][a-zA-Z0-9_]*)', replacer, sql)

    engine = get_database_engine(target_db, driver="{ODBC Driver 18 for SQL Server}")
    raw_conn = engine.raw_connection()
    try:
        cursor = raw_conn.cursor()
        for stmt in [s.strip() for s in _bind(setup_sql, params).split("-- ;; --") if s.strip()]:
            cursor.execute(stmt)
        cursor.execute(_AGG_QUERY)
        df = pd.DataFrame.from_records(cursor.fetchall(),
                                       columns=[col[0] for col in cursor.description])
        raw_conn.commit()
        cursor.close()
        return df
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()

# ======================================================================
# FETCH HELPERS  (joblib-cached)
# ======================================================================

#@demographics_cache.cache
def fetch_drug_demographics_summary(
    drug_names: tuple[str], target_db: str
) -> pd.DataFrame:
    drug_ph, params = _drug_params(list(drug_names))
    setup = _SETUP_DRUGS_ONLY.format(drug_placeholders=drug_ph)
    return _run_with_temps(setup, params, target_db)


#@demographics_cache.cache
def fetch_reaction_demographics_summary(
    reaction_names: tuple[str], target_db: str
) -> pd.DataFrame:
    rxn_ph, params = _reaction_params(list(reaction_names))
    setup = _SETUP_REACTIONS_ONLY.format(reaction_placeholders=rxn_ph)
    return _run_with_temps(setup, params, target_db)


#@demographics_cache.cache
def fetch_drug_reaction_demographics_summary(
    drug_names: tuple[str],
    reaction_names: tuple[str],
    target_db: str,
) -> pd.DataFrame:
    drug_ph, drug_params = _drug_params(list(drug_names))
    rxn_ph,  rxn_params  = _reaction_params(list(reaction_names))
    params = {**drug_params, **rxn_params}  # no key collisions: drug_N vs rxn_N
    setup = _SETUP_DRUGS_AND_REACTIONS.format(
        drug_placeholders=drug_ph,
        reaction_placeholders=rxn_ph,
    )
    return _run_with_temps(setup, params, target_db)
