import time
import logging
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import AuthorizedSession
from config import CREDENTIALS_FILE, SPREADSHEET_ID

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

_client: gspread.Client | None = None
_authed_session: AuthorizedSession | None = None

_alumni_cache: list[dict] | None = None
_alumni_cache_ts: float = 0
_ALUMNI_TTL = 300  # 5 минут

_registry_cache: list[str] | None = None
_registry_cache_ts: float = 0
_REGISTRY_TTL = 60  # 1 минута


def _get_creds() -> Credentials:
    return Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=_SCOPES)


def _get_client() -> gspread.Client:
    global _client
    if _client is None:
        _client = gspread.authorize(_get_creds())
    return _client


def get_authed_session() -> AuthorizedSession:
    """Authorized requests session using the service account — for Drive file downloads."""
    global _authed_session
    if _authed_session is None:
        _authed_session = AuthorizedSession(_get_creds())
    return _authed_session


def _get_spreadsheet() -> gspread.Spreadsheet:
    return _get_client().open_by_key(SPREADSHEET_ID)


def get_registry_keys() -> list[str]:
    global _registry_cache, _registry_cache_ts
    now = time.time()
    if _registry_cache is None or now - _registry_cache_ts > _REGISTRY_TTL:
        ws = _get_spreadsheet().worksheet("Реестр")
        values = ws.col_values(1)
        _registry_cache = [v.strip() for v in values if v.strip()]
        _registry_cache_ts = now
        logger.info("Registry cache refreshed: %d keys", len(_registry_cache))
    return _registry_cache


def is_valid_key(key: str) -> bool:
    return key in get_registry_keys()


def get_alumni_data() -> list[dict]:
    global _alumni_cache, _alumni_cache_ts
    now = time.time()
    if _alumni_cache is None or now - _alumni_cache_ts > _ALUMNI_TTL:
        ws = _get_spreadsheet().worksheet("Аламни")
        _alumni_cache = ws.get_all_records()
        _alumni_cache_ts = now
        logger.info("Alumni cache refreshed: %d rows", len(_alumni_cache))
    return _alumni_cache


def get_unique_values(field: str) -> list[str]:
    data = get_alumni_data()
    seen: set[str] = set()
    result: list[str] = []
    for row in data:
        val = str(row.get(field, "")).strip()
        if val and val not in seen:
            seen.add(val)
            result.append(val)
    return sorted(result)


def filter_alumni(filters: dict[str, str]) -> list[dict]:
    data = get_alumni_data()
    if not filters:
        return list(data)
    result = []
    for row in data:
        if all(str(row.get(f, "")).strip() == v for f, v in filters.items()):
            result.append(row)
    return result


def invalidate_alumni_cache() -> None:
    global _alumni_cache
    _alumni_cache = None
