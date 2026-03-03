
from pathlib import Path
from sqlalchemy.sql import text
import umc_research.datascience.database
import pandas as pd
SCRIPT_DIR = Path(__file__).resolve().parent       # .../lib
PROJECT_ROOT = SCRIPT_DIR.parent                   # .../
DATA_DIR = PROJECT_ROOT / "data"
OUT_PATH = DATA_DIR / "evalset.csv"
query = text("""
DECLARE @seed NVARCHAR(50) = N'12345';
WITH masters AS (
    SELECT rep.ReportID AS MasterReportID
    FROM [UMCReport20260128].[UMCReport].[Report] AS rep
    WHERE rep.UMCCalculated_PreferredICSR_ReportID = rep.ReportID
),
sampled_masters AS (
    SELECT TOP 100 MasterReportID
    FROM masters
    ORDER BY HASHBYTES('SHA2_256', CONCAT(@seed, CONVERT(NVARCHAR(50), MasterReportID)))
)
SELECT 
    sm.MasterReportID,
    rep.ReportID AS SlaveReportID
FROM sampled_masters sm
JOIN [UMCReport20260128].[UMCReport].[Report] AS rep
    ON rep.UMCCalculated_PreferredICSR_ReportID = sm.MasterReportID
    AND rep.ReportID != sm.MasterReportID
ORDER BY sm.MasterReportID, rep.ReportID;
""")
engine = umc_research.datascience.database.get_database_engine("UMCReport20260128")
data = pd.read_sql(query, engine)
data = data.sort_values(by=["MasterReportID"])
# 2. Keep only first occurrence of each unique ID
data = data.drop_duplicates(subset=["MasterReportID"], keep='first')
data.to_csv(OUT_PATH, index=False)
