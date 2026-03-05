from dataclasses import dataclass, field, asdict
import logging
from re import escape
from decimal import Decimal

import pandas as pd

from umc_research.datascience.database import (
    get_database_engine,
    upload_report_ids_to_temp_table,
)
from collections import defaultdict
from sqlalchemy import text as sql_text
from pathlib import Path
from sqlalchemy.sql import text, bindparam
from sqlalchemy.engine import Engine
from llm_dup_dev.lib.util import is_missing
import re


@dataclass
class Reaction:
    pt_name: str = ""
    start_date: pd.Timestamp = pd.Timestamp("")
    end_date: pd.Timestamp = pd.Timestamp("")
    outcome: str = ""


@dataclass
class MedicalHistoryEpisode:
    term: str = ""
    start_date: pd.Timestamp = pd.Timestamp("")
    end_date: pd.Timestamp = pd.Timestamp("")
    continuing: str = ""
    comment: str = ""


@dataclass
class Drug_history:
    def __init__(
        self,
        drug_name: str = "",
        indication: str = "",
        reaction: str = "",
        start_date: pd.Timestamp = pd.Timestamp(""),
        end_date: pd.Timestamp = pd.Timestamp(""),
    ):
        self.drug_name = drug_name
        self.indication = indication
        self.reaction = reaction
        self.start_date = start_date
        self.end_date = end_date

    def is_empty(self):
        return (
            self.drug_name == ""
            and self.indication == ""
            and self.reaction == ""
            and self.start_date == ""
            and self.end_date == ""
        )


@dataclass
class TestResults:
    TestName: str = ""
    TestResult: str = ""
    Testunit: str = ""
    LowTestRange: str = ""
    HighTestRange: str = ""
    date: str = ""
    CommentsOnTestandProcedures: str = ""

    def is_empty(self):
        return (
            self.TestName == ""
            and self.TestResult == ""
            and self.Testunit == ""
            and self.LowTestRange == ""
            and self.HighTestRange == ""
            and self.date == ""
            and self.CommentsOnTestandProcedures == ""
        )


@dataclass
class Dosage:
    dosage_number: float = None
    dosage_unit: str = ""
    separate_dosage_number: float = None
    interval_dosage_number: float = None
    time_unit: str = ""
    dosage_text: str = ""
    dosage_form: str = ""

    def _format_number(self, number) -> str:
        # Works for both float and Decimal
        if number == int(number):
            return str(int(number))
        return str(number.normalize() if isinstance(number, Decimal) else number)

    def _normalize_unit(self, unit: str) -> str:
        """
        Keep only the short unit (e.g., 'mg', 'µg', 'ng', 'pg', 'mg/kg', 'µg/kg', 'mg/m2', 'µg/m2'),
        fixing odd spacing like 'mg/m 2' or 'µg/ m 2'.
        """
        if not unit:
            return ""

        u = unit.strip()

        # Lowercase common letters but keep µ; also normalize weird spaces around '/', 'm', and '2'
        # Examples handled: "Mg milligram(s)", "mg/m 2 ...", "µg/ m 2 ..."
        u = (
            u.replace("Mg", "mg").replace("UG", "µg").replace("ug", "µg")
        )  # gentle nudges
        u = re.sub(r"\s+", " ", u)  # collapse multiple spaces
        u = re.sub(r"/\s*", "/", u)  # remove spaces after slash
        u = re.sub(r"\s*/", "/", u)  # remove spaces before slash
        u = re.sub(r"m\s*2", "m2", u, flags=re.I)  # m 2 -> m2

        # Now the first "token" is usually enough (e.g., 'mg', 'µg', 'mg/kg', 'mg/m2')
        # But ensure the token can include '/...' fragments (no spaces)
        first = u.split(" ")[0]

        # Normalize case for the main prefixes; keep µ as-is
        first = first.replace("Milligram", "mg").replace("milligram", "mg")
        first = first.replace("Microgram", "µg").replace("microgram", "µg")
        first = first.replace("Nanogram", "ng").replace("nanogram", "ng")
        first = first.replace("Picogram", "pg").replace("picogram", "pg")

        # Final tidy: allow only expected chars
        first = re.sub(r"[^a-zA-Zµ/0-9]", "", first)  # strip stray punctuation
        # Common uppercase cleanups
        first = (
            first.replace("MG", "mg")
            .replace("UG", "µg")
            .replace("NG", "ng")
            .replace("PG", "pg")
        )

        # Special cases where people type m^2 or m²
        first = first.replace("m^2", "m2").replace("m²", "m2")

        return first

    @property
    def dose(self) -> str:
        parts = []
        if self.dosage_number is not None and self.dosage_unit:
            parts.append(
                f"{self._format_number(self.dosage_number)} {self._normalize_unit(self.dosage_unit)}"
            )
            if self.separate_dosage_number is not None:
                parts.append(self._format_number(self.separate_dosage_number))
            if self.interval_dosage_number is not None and self.time_unit:
                parts.append(
                    f"per {self._format_number(self.interval_dosage_number)} {self.time_unit}"
                )
        if isinstance(self.dosage_text, str) and self.dosage_text.strip():
            parts.append(f"({self.dosage_text})")

        if isinstance(self.dosage_form, str) and self.dosage_form.strip():
            parts.append(f"form: {self.dosage_form}")

        return " ".join(parts) if parts else ""


@dataclass
class Drug:
    drug_name: str = ""
    drug_role: str = ""
    start_date: pd.Timestamp = pd.Timestamp("")
    end_date: pd.Timestamp = pd.Timestamp("")
    indication: str = ""
    action_taken: str = ""
    dosage: str = ""


@dataclass
class Report:
    sex: str = "Unknown"
    age: float = None
    height: float = None
    weight: float = None
    country: str = "Unknown"
    report_date: pd.Timestamp = None
    reactions: list[Reaction] = field(default_factory=list)
    drugs: list[Drug] = field(default_factory=list)
    medical_history_episodes: list[MedicalHistoryEpisode] = field(default_factory=list)
    Test_history: list[TestResults] = field(default_factory=list)
    dosage: list[Dosage] = field(default_factory=list)

    Drughistory: list[Drug_history] = field(default_factory=list)
    narrative: str = ""
    medical_history: str = ""
    literatureeference: str = ""
    studyname: str = ""

    def __post_init__(self):
        if self.weight is not None:
            self.weight = f"{int(float(self.weight))} kg"
        if self.height is not None:
            self.height = f"{int(float(self.height))} cm"


def make_fetch_data_sql(engine: Engine, report_id):
    query = text("""
        select REP.[ReportID] as UMCReportID
        , Sex.[Description] as PatientSex
        , FLOOR(ROUND(( P.UMCCalculated_PatientOnsetAgeAvg + 0.0 ) / 365.242199, 3)) as PatientAge
        , MDH.PT_NAME as PT
        , RE.UMCCalculated_Reaction as ReportedReaction
        , RE.UMCValidated_ReactionStartDateMin as ReactionStartMin
        , RE.UMCValidated_ReactionEndDateMin as ReactionEndMin
        , lxc.CountryOfficialName as Country
        , REP.UMCCalculated_DateDatabase as LatestReportDate
      --  ,REP.UMCValidated_ReceiveDateMin as LatestReportDate
        , lxout.Description as ReactionOutcome
        , DRUG.UMCValidated_BaseCompositionName as BaseCompositionName
        , DRUG.MedicinalProduct as DrugVerbatim
        , DRUG.UMCValidated_DrugStartDateMin as DrugStartMin
        , DRUG.UMCValidated_DrugEndDateMin as DrugStopMin
        , DRUG.UMCValidated_DrugIndicationText as DrugIndication
        , DRUG.UMCValidated_DrugCharacterizationID as DrugRole
        , lxaction.Description DrugActionTaken
        , FT.NarrativeIncludeClinical as CaseNarrative
        , mhe.[UMCValidated_PatientEpisodeNameText] as MedicalHistoryTerm
        , mhe.[UMCValidated_PatientMedicalStartDateMin] as MedicalHistoryStartMin
        , lxcontinue.Description as MedicalHistoryContinue
        , mhe.[UMCValidated_PatientMedicalEndDateMin] as MedicalHistoryEndMin
        , Test.TestName as TestName
        , Test.TestResult
        , Test.Testunit
        , Test.LowTestRange
        , Test.HighTestRange
        , Test.UMCValidated_TestDateMax as TestDate
        , FText.ResultsTestsProcedures as CommentsOnTestandProcedures
        , mhe.MedicalHistoryEpisodeID
        , MHT.PatientMedicalComment
        , llt.LLT_NAME
        , llt.LLT_CODE
        , PDT.PatientDrugName as PastDrugName
        , PDT.UMCValidated_PatientDrugIndicationText as PastDrugIndication
       -- , PDT.PatientDrugReaction 
        , PDT.UMCValidated_PatientDrugStartDateMin as PastDrugStartDate
        , PDT.UMCValidated_PatientDrugEndDateMin as PastDrugEndDate
        , case when llt.LLT_CODE is NULL then TestName else llt.LLT_NAME end as actual_test_name
        , llt2.LLT_NAME as PastDrugReaction
        , DRUG.UMCValidated_DrugStructureDosageNumb
       , DosageUnit.[Description] as DosageUnit
         , DRUG.UMCValidated_DrugSeparateDosageNumb
        , DRUG.UMCValidated_DrugIntervalDosageUnitNumb
         , TimeUnit.[Description] as TimeUnit
         , DRUG.DrugDosageText
         , DRUG.DrugDosageForm
        ,PrimarySource.LiteratureReference as Litref
        ,PrimarySource.StudyName as StudyName
         ,P.UMCValidated_PatientHeight as Height
               , P.UMCValidated_PatientWeight as Weight 
      FROM [UMCReport].[Report] as REP
      JOIN [UMCReport].[Reaction] RE on REP.ReportID = RE.ReportID
      JOIN [UMCReport].[Drug] DRUG on REP.ReportID = DRUG.ReportID
      JOIN [UMCReport].Patient P on P.ReportID = REP.ReportID
      left join [UMCReport].Test Test on Test.ReportID = REP.ReportID
      left join [UMCReport].[FreeText]  FText on FText.ReportID = REP.ReportID
      
      left join MeddraWeekly.Meddra.MEDDRA_LOW_LEVEL_TERM llt on llt.LLT_code = TRY_CAST(TestName as int)
      left join [UMCReport].[PatientPastDrugTherapy] PDT on PDT.ReportID = REP.ReportID

      left join MeddraWeekly.Meddra.MEDDRA_LOW_LEVEL_TERM llt2 on llt2.LLT_code = PDT.PatientDrugReaction 

      left join [Lexicon].Sex on Sex.SexID = P.UMCValidated_PatientSexID
      left join Lexicon.Country lxc on lxc.CountryID = rep.UMCCalculated_PrimarySourceCountryID
      join MeddraWeekly.Meddra.MEDDRA_MD_HIERARCHY MDH on MDH.PT_CODE = RE.UMCValidated_ReactionMeddraPtCode
      left join [UMCReport].[FreeText] FT on FT.ReportID = REP.ReportID
      left join [Lexicon].[ReactionOutcome] lxout on lxout.ReactionOutcomeID = RE.UMCValidated_ReactionOutcomeID
      left join [UMCReport].[MedicalHistoryEpisode] mhe on mhe.ReportID = REP.ReportID
      --left join [UMCReportWeekly].[UMCReport].[PatientPastDrugTherapy] PDT on PDT.ReportID = REP.ReportID
      left join [Lexicon].[YesNoUnknown] lxcontinue on lxcontinue.YesNoUnknownID = mhe.[UMCValidated_PatientMedicalContinueID]
      left join [Lexicon].[ActionDrug] lxaction on lxaction.ActionDrugID = DRUG.UMCValidated_ActionDrugID 
      left join [UMCReport].[MedicalHistoryEpisode_FreeText] as MHT on mhe.MedicalHistoryEpisodeID=MHT.MedicalHistoryEpisodeID 
               
    left join [Lexicon].[DosageUnit] DosageUnit on DosageUnit.DosageUnitID = DRUG.UMCValidated_DrugStructureDosageUnitID
    left join [UMCReport].[PrimarySource] PrimarySource on PrimarySource.ReportID = REP.ReportID

      left join [Lexicon].[TimeUnit] TimeUnit ON Drug.UMCValidated_DrugIntervalDosageDefinitionID = TimeUnit.TimeUnitID

      WHERE 1 = 1
       AND REP.UMCCalculated_ForeignCase = 0 -- Remove foreign cases
   --    AND (REP.UMCCalculated_PreferredICSR_ReportID IS NULL OR REP.UMCCalculated_PreferredICSR_ReportID = REP.ReportID)
       AND REP.ReportID = :report_id
    """).bindparams(bindparam("report_id", value=report_id))
    try:
        with engine.connect() as conn:
            result = conn.execute(query, {"report_id": report_id})
            return pd.DataFrame(result.fetchall(), columns=result.keys())
    except Exception as e:
        logging.error(f"Error fetching report: {e}")
    return 0  # Return 0 in case of an error


# def fetch_report_counts_for_common_reactions(
#     common_reaction_names, Target_DB="UMCReportWeekly"
# ):
#     engine = get_database_engine(Target_DB, driver="{ODBC Driver 18 for SQL Server}")
#     n = len(common_reaction_names)
#     sql = sql_text("""
#     ;WITH pt_map AS (
#         SELECT DISTINCT MDH.PT_CODE, MDH.PT_NAME
#         FROM MeddraWeekly.Meddra.MEDDRA_MD_HIERARCHY AS MDH
#         WHERE MDH.PT_NAME IN :common_reaction_names
#     ),
#     matched_reports AS (
#         SELECT re.ReportID
#         FROM [UMCReport].[Reaction] AS RE
#         JOIN pt_map AS m
#         ON m.PT_CODE = RE.UMCValidated_ReactionMeddraPtCode
#         GROUP BY re.ReportID
#         HAVING COUNT(DISTINCT m.PT_NAME) = :n
#     )
#     Select ReportID from matched_reports;
#     """).bindparams(
#         bindparam(
#             "common_reaction_names", expanding=True
#         ),  # expands to (:drug_names_1, :drug_names_2, ...)
#         bindparam("n"),
#     )
#     params = {"common_reaction_names": list(common_reaction_names), "n": n}
#     return pd.read_sql(sql, engine, params=params)


# def fetch_report_counts_for_common_drugs(drug_names, Target_DB="UMCReportWeekly"):
#     engine = get_database_engine(Target_DB, driver="{ODBC Driver 18 for SQL Server}")
#     n = len(drug_names)
#     print(f"recieved drug names: {drug_names} with length {n}")
#     sql = sql_text("""
#    SELECT ReportID
# FROM [UMCReport].[Drug]
# WHERE DRUG.UMCValidated_BaseCompositionName IN :drug_names
# GROUP BY ReportID
# HAVING COUNT(DISTINCT UMCValidated_BaseCompositionName) = :n

#     """).bindparams(
#         bindparam(
#             "drug_names", expanding=True
#         ),  # expands to (:drug_names_1, :drug_names_2, ...)
#         bindparam("n"),
#     )
#     params = {"drug_names": list(drug_names), "n": n}
#     df=pd.read_sql(sql, engine, params=params)
#     print(df.head())
#     return pd.read_sql(sql, engine, params=params)


# def fetch_demographic_for_give_reports_ids_via_temp_table(report_ids_input, Target_DB):
#     if isinstance(report_ids_input, (pd.DataFrame, pd.Series)):
#         report_ids = report_ids_input["ReportID"].dropna().astype(int).tolist()
#     elif isinstance(report_ids_input, (list, set, tuple)):
#         # ensure ints, in case of strings
#         report_ids = [int(x) for x in report_ids_input]
#     else:
#         raise TypeError(f"Unsupported type: {type(report_ids_input)}")

#     engine = get_database_engine(
#         Target_DB, driver="{ODBC Driver 18 for SQL Server}", fast_executemany=True
#     )
#     QUERY = sql_text("""
#     SELECT DISTINCT
#         rep.ReportID AS ReportID,
#         rep.UMCCalculated_DateDatabase as dbDate,
#      --   rep.UMCValidated_ReceiveDateMin as dbDate,
#         pat.UMCValidated_PatientOnsetAge as age,
#         country.CountryOfficialName as SourceCountry,
#         S.[Description] as sex
#     FROM #ids AS i
#     JOIN [UMCReport].[Report] AS rep ON rep.ReportID = i.ReportID
#     left JOIN [UMCReport].[Patient] AS pat ON pat.ReportID = i.ReportID
#     left JOIN [Lexicon].[Country] as country on country.CountryID=rep.UMCValidated_PrimarySourceCountryID
#     left Join [Lexicon].[Sex] as S on S.SexID=TRY_CONVERT(tinyint, pat.PatientSex)
#     """)

#     with engine.begin() as conn:
#         # 1) create temp table
#         conn.exec_driver_sql("CREATE TABLE #ids (ReportID INT PRIMARY KEY);")
#         # 2) bulk insert ids (fast_executemany makes this ~orders faster)
#         rows = [{"ReportID": int(i)} for i in report_ids]
#         if rows:
#             conn.execute(
#                 sql_text("INSERT INTO #ids (ReportID) VALUES (:ReportID)"), rows
#             )
#         # 3) run the join query
#         result = conn.execute(QUERY)
#         return pd.DataFrame(result.fetchall(), columns=result.keys())


def make_report_from_report_id(report_id, Target_DB="UMCReportWeekly"):
    engine = get_database_engine(Target_DB, driver="{ODBC Driver 18 for SQL Server}")

    report_df = make_fetch_data_sql(engine, report_id)
    if report_df.empty:
        print(f"No data for report {report_id}")
        return None
    if (
        len(report_df.Weight) == 0
        or is_missing(list(report_df.Weight)[0])
        or report_df.Weight is None
    ):
        weight = None
    else:
        weight = list(report_df.Weight)[0]
    if (
        len(report_df.Height) == 0
        or is_missing(list(report_df.Height)[0])
        or report_df.Height is None
    ):
        height = None
    else:
        height = list(report_df.Height)[0]
    if len(report_df.PatientSex) == 0:
        sex = "Unknown"
    else:
        sex = list(report_df.PatientSex)[0]
    if len(report_df.PatientAge) == 0:
        age = "Unknown"
    else:
        age = list(report_df.PatientAge)[0]

    if len(report_df.Country) == 0:
        country = "Unknown"
    else:
        country = list(report_df.Country)[0]
    if (
        "LatestReportDate" not in report_df.columns
        or report_df["LatestReportDate"].isnull().all()
        or len(report_df["LatestReportDate"]) == 0
    ):
        latest_report_date = None
    else:
        # Get the first non-null value
        first_valid = report_df["LatestReportDate"].dropna().iloc[0]
        latest_report_date = first_valid.date() if pd.notnull(first_valid) else None

    reaction_df = report_df[
        [
            "PT",
            "ReportedReaction",
            "ReactionStartMin",
            "ReactionEndMin",
            "ReactionOutcome",
        ]
    ].drop_duplicates()
    reactions = []
    for row_dict in reaction_df.to_dict(orient="records"):
        reactions.append(
            Reaction(
                pt_name=row_dict["PT"],
                start_date=(
                    row_dict["ReactionStartMin"].date()
                    if row_dict.get("ReactionStartMin")
                    else None
                ),
                end_date=(
                    row_dict["ReactionEndMin"].date()
                    if row_dict.get("ReactionEndMin")
                    else None
                ),
                outcome=row_dict["ReactionOutcome"],
            )
        )

    drug_df = report_df[
        [
            "BaseCompositionName",
            "DrugVerbatim",
            "DrugStartMin",
            "DrugStopMin",
            "DrugIndication",
            "DrugActionTaken",
            "DrugRole",
            "UMCValidated_DrugStructureDosageNumb",
            "DosageUnit",
            "UMCValidated_DrugSeparateDosageNumb",
            "UMCValidated_DrugIntervalDosageUnitNumb",
            "TimeUnit",
            "DrugDosageText",
            "DrugDosageForm",
        ]
    ].drop_duplicates()
    drugs = []
    for row_dict in drug_df.to_dict(orient="records"):
        if row_dict["DrugRole"] == 1:
            Drug_role = "Suspected"
        elif row_dict["DrugRole"] == 3:
            Drug_role = "Interacting"
        elif row_dict["DrugRole"] == 2:
            Drug_role = "Concomitant"

        dos = Dosage(
            dosage_number=row_dict.get("UMCValidated_DrugStructureDosageNumb"),
            dosage_unit=(row_dict.get("DosageUnit") or ""),
            separate_dosage_number=row_dict.get("UMCValidated_DrugSeparateDosageNumb"),
            interval_dosage_number=row_dict.get(
                "UMCValidated_DrugIntervalDosageUnitNumb"
            ),
            time_unit=(row_dict.get("TimeUnit") or ""),
            dosage_text=(row_dict.get("DrugDosageText") or ""),
            dosage_form=(row_dict.get("DrugDosageForm") or ""),
        )

        drugs.append(
            Drug(
                drug_name=row_dict["BaseCompositionName"],
                drug_role=Drug_role,
                start_date=(
                    row_dict["DrugStartMin"].date()
                    if row_dict.get("DrugStartMin")
                    else None
                ),
                end_date=(
                    row_dict["DrugStopMin"].date()
                    if row_dict.get("DrugStopMin")
                    else None
                ),
                indication=row_dict["DrugIndication"],
                action_taken=row_dict["DrugActionTaken"],
                dosage=dos.dose,  # or dose=dos.dose
            )
        )

    drug_history_df = report_df[
        [
            "PastDrugName",
            "PastDrugIndication",
            "PastDrugReaction",
            "PastDrugStartDate",
            "PastDrugEndDate",
        ]
    ].drop_duplicates()
    drugs_history = []
    for row_dict in drug_history_df.to_dict(orient="records"):
        drug_episode = Drug_history(
            drug_name=row_dict["PastDrugName"],
            start_date=(
                row_dict["PastDrugStartDate"].date()
                if row_dict.get("PastDrugStartDate")
                else None
            ),
            end_date=(
                row_dict["PastDrugEndDate"].date()
                if row_dict.get("PastDrugEndDate")
                else None
            ),
            indication=row_dict["PastDrugIndication"],
            reaction=row_dict["PastDrugReaction"],
        )
        if drug_episode.is_empty():
            continue
        drugs_history.append(drug_episode)

    history_df = report_df[
        [
            "MedicalHistoryTerm",
            "MedicalHistoryStartMin",
            "MedicalHistoryContinue",
            "MedicalHistoryEndMin",
            "PatientMedicalComment",
        ]
    ].drop_duplicates()
    medical_history_episodes = []
    for row_dict in history_df.to_dict(orient="records"):
        if all(x is None for x in row_dict.values()):
            continue
        episode = MedicalHistoryEpisode(
            term=row_dict["MedicalHistoryTerm"],
            start_date=row_dict["MedicalHistoryStartMin"],
            end_date=row_dict["MedicalHistoryEndMin"],
            continuing=row_dict["MedicalHistoryContinue"],
            comment=row_dict["PatientMedicalComment"],
        )
        if episode.term is None:
            continue

        medical_history_episodes.append(episode)

    test_df = report_df[
        [
            "actual_test_name",
            "TestResult",
            "Testunit",
            "LowTestRange",
            "HighTestRange",
            "TestDate",
            "CommentsOnTestandProcedures",
        ]
    ].drop_duplicates()
    # Then, remove duplicate comments in 'CommentsOnTestandProcedures' after the first occurrence
    # Remove duplicate comments after the first occurrence
    test_df["CommentsOnTestandProcedures"] = test_df[
        "CommentsOnTestandProcedures"
    ].where(~test_df.duplicated(subset="CommentsOnTestandProcedures"), "")

    Test_results = []
    for row_dict in test_df.to_dict(orient="records"):
        if all(x is None for x in row_dict.values()):
            continue
        episode = TestResults(
            TestName=row_dict["actual_test_name"],
            TestResult=row_dict["TestResult"],
            Testunit=row_dict["Testunit"],
            LowTestRange=row_dict["LowTestRange"],
            HighTestRange=row_dict["HighTestRange"],
            date=row_dict["TestDate"],
            CommentsOnTestandProcedures=row_dict["CommentsOnTestandProcedures"],
        )
        if episode.is_empty():
            continue

        Test_results.append(episode)

    narrative = list(report_df.CaseNarrative)[0]
    literatureeference = list(report_df.Litref)[0]
    studyname = list(report_df.StudyName)[0]

    return Report(
        sex=sex,
        age=age,
        weight=weight,
        height=height,
        country=country,
        report_date=latest_report_date,
        reactions=reactions,
        drugs=drugs,
        medical_history_episodes=medical_history_episodes,
        Test_history=Test_results,
        Drughistory=drugs_history,
        narrative=narrative,
        literatureeference=literatureeference,
        studyname=studyname,
    )


def make_reaction_text(reactions):
    if not all(r.start_date is None for r in reactions):
        reactions = sorted(reactions, key=lambda r: r.start_date)
    prompt = ""
    for reaction in reactions:
        prompt += reaction.pt_name
        if reaction.start_date is not None and reaction.start_date is not pd.NaT:
            prompt += f" starting on {reaction.start_date}"
        if reaction.end_date is not None and reaction.end_date is not pd.NaT:
            prompt += f", ending on {reaction.end_date}"
        if (
            reaction.outcome is not None
            and reaction.outcome != "Unknown"
            and reaction.outcome != "unknown"
        ):
            prompt += f" and the outcome was: {reaction.outcome}"
        prompt += "\n"

    return prompt


def make_drug_text(drugs):
    if not all(d.start_date is None for d in drugs):
        drugs = sorted(drugs, key=lambda d: d.start_date)
    prompt = ""
    for drug in drugs:
        prompt += drug.drug_name
        prompt += f" is reported to be {drug.drug_role} drug"
        if drug.start_date is not None and drug.start_date is not pd.NaT:
            prompt += f" starting on {drug.start_date}"
        if drug.end_date is not None and drug.end_date is not pd.NaT:
            prompt += f", ending on {drug.end_date}"
        if drug.indication is not None:
            prompt += f", with indication: {drug.indication}"
        if drug.action_taken is not None:
            prompt += f". The action taken with the drug was {drug.action_taken}"
        if drug.dosage is not None and drug.dosage != "":
            prompt += f", with dosage: {drug.dosage}"
        prompt += "\n"

    return prompt


def make_medical_history_text(medical_history_episodes):
    # sorted_medical_history_episodes = sorted(medical_history_episodes, key=lambda mh: mh.start_date)
    if len(medical_history_episodes) == 0:
        return ""

    prompt = "The patient had some reported medical history or concurrent conditions;"
    for mhe in medical_history_episodes:
        prompt += mhe.term
        if mhe.start_date is not None and not pd.isnull(mhe.start_date):
            prompt += f" starting on {mhe.start_date}"
        if mhe.end_date is not None and not pd.isnull(mhe.end_date):
            prompt += f", ending on {mhe.end_date}"
        if mhe.continuing == "Yes":
            prompt += f". This was reported as continuing."
        elif mhe.continuing == "No":
            prompt += f". This was reported as not continuing."
        if mhe.comment is not None:
            prompt += f". Comments: {mhe.comment}"
        prompt += "\n"

    return prompt


def make_Test_Results_text(Test_history):
    # sorted_medical_history_episodes = sorted(medical_history_episodes, key=lambda mh: mh.start_date)
    if len(Test_history) == 0:
        return ""

    prompt = "The patient had some reported Test results :"
    for mhe in Test_history:
        if mhe.TestName is not None:
            prompt += mhe.TestName
        if mhe.TestResult is not None:
            prompt += f" Value {mhe.TestResult}"
        if mhe.Testunit is not None:
            prompt += f" {mhe.Testunit}"
        if mhe.date is not None and mhe.date is not pd.NaT:
            prompt += f" on date: {mhe.date.date()}"
        if mhe.LowTestRange is not None:
            prompt += f" with normal low range {mhe.LowTestRange}"
        if mhe.HighTestRange is not None:
            prompt += f" and normal high range {mhe.HighTestRange}"
        prompt += "\n"

    for mhe in Test_history:
        if (
            mhe.CommentsOnTestandProcedures is not None
            and mhe.CommentsOnTestandProcedures != ""
        ):
            prompt += f"Comments: {mhe.CommentsOnTestandProcedures}"

    return prompt


def make_drug_history_text(Drug_history):
    if len(Drug_history) == 0:
        return ""

    prompt = "The patient had some reported Drug history: "
    for dh in Drug_history:
        if dh.is_empty():
            continue
        if dh.drug_name is not None:
            prompt += dh.drug_name
        if dh.indication is not None:
            prompt += f", for indication: {dh.indication}"
        if dh.start_date is not None and dh.start_date is not pd.NaT:
            prompt += f" starting on {dh.start_date}"
        if dh.end_date is not None and dh.end_date is not pd.NaT:
            prompt += f", ending on {dh.end_date}"
        if dh.reaction is not None:
            prompt += f", with reaction: {dh.reaction}"
        prompt += "\n"

    return prompt


def make_report_details(report: Report):
    if report.sex is None:
        report.sex = "Unknown"
    if report.age is None:
        report.age = "Unknown"
    prompt = f"""
<Individual Case Safety Report>

<patient details>
Sex: {report.sex} 
Age: {report.age} years 
Weight: {report.weight if report.weight is not None else "Unknown"} kg
Height: {report.height if report.height is not None else "Unknown"} cm
</patient details>

<adverse events>
{make_reaction_text(report.reactions)}
</adverse events>

<drugs>
{make_drug_text(report.drugs)}
</drugs>

<medical history>
{make_medical_history_text(report.medical_history_episodes)}
</medical history>

<Drug history>
{make_drug_history_text(report.Drughistory)}
</Drug history>

<Test Results>
{make_Test_Results_text(report.Test_history)}
</Test Results>

<free text narrative>
{report.narrative}
</free text narrative>

<literature reference>
{report.literatureeference}
</literature reference>
<study name>
{report.studyname}
</study name>
</Individual Case Safety Report>
"""
    return prompt
