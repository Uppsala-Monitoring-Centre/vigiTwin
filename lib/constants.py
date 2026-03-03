from logging import getLogger
from pathlib import Path

LOGGER = getLogger(__file__)


def _find_vigi_match_data_directory():
    """Returns valid VigiMatch data directory path."""
    search_paths = [
        "~/WHO Collaborating Centre for International Drug Monitoring/Research and Education - Documents/VigiMatch",
        "~/WHO Collaborating Centre for Int Drug Monitoring/Research - VigiMatch",
        "~/WHO Collaborating Centre for International Drug Monitoring/Research - Documents/VigiMatch",
        "~/WHO Collaborating Centre for Int Drug Monitoring/Research - Documents/VigiMatch",
    ]

    try:
        for path_str in search_paths:
            data_path = Path(path_str).expanduser()
            if data_path.exists():
                return data_path
        raise FileNotFoundError(
            f"failed to find a valid VigiMatch path. The following paths were tried: {search_paths}"
        )
    except (RuntimeError, FileNotFoundError):
        # only warn to support importing this module in CI/CD
        LOGGER.warning(
            "Can't find the onedrive directory. If you're running locally, have you synced it?",
            exc_info=True,
        )
        return Path(".")


TARGET_DB = "UMCReport20230102"
MEDDRA_DB = "Meddra20230102"
VIGIMATCH_ONEDRIVE_PATH = _find_vigi_match_data_directory()
REPO_SQL_ROOT = Path(__file__).parent.parent / "sql"
