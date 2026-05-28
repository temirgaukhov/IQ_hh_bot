import time
import logging
import gspread
from google.oauth2.service_account import Credentials
from google.auth.transport.requests import AuthorizedSession
from config import CREDENTIALS_FILE, SPREADSHEET_ID

logger = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",   # read + write
    "https://www.googleapis.com/auth/drive.readonly",
]

_client: gspread.Client | None = None
_authed_session: AuthorizedSession | None = None

_alumni_cache: list[dict] | None = None
_alumni_cache_ts: float = 0
_ALUMNI_TTL = 300  # 5 минут

# Реестр: {БИН: Попечитель}
_registry_cache: dict[str, str] | None = None
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


def get_registry_data() -> dict[str, str]:
    """Возвращает словарь {БИН: Попечитель}."""
    global _registry_cache, _registry_cache_ts
    now = time.time()
    if _registry_cache is None or now - _registry_cache_ts > _REGISTRY_TTL:
        ws = _get_spreadsheet().worksheet("Реестр")
        records = ws.get_all_records()
        _registry_cache = {
            str(r.get("БИН", "")).strip(): str(r.get("Попечитель", "")).strip()
            for r in records
            if str(r.get("БИН", "")).strip()
        }
        _registry_cache_ts = now
        logger.info("Registry cache refreshed: %d entries", len(_registry_cache))
    return _registry_cache


def is_valid_key(key: str) -> bool:
    return key in get_registry_data()


def get_trustee(key: str) -> str:
    """Возвращает имя попечителя по БИН. Если не найден — возвращает сам ключ."""
    return get_registry_data().get(key, key)


def get_alumni_data() -> list[dict]:
    global _alumni_cache, _alumni_cache_ts
    now = time.time()
    if _alumni_cache is None or now - _alumni_cache_ts > _ALUMNI_TTL:
        ws = _get_spreadsheet().worksheet("Аламни")
        _alumni_cache = ws.get_all_records()
        _alumni_cache_ts = now
        logger.info("Alumni cache refreshed: %d rows", len(_alumni_cache))
    return _alumni_cache


def get_unique_values(field: str, filters: dict[str, str] | None = None) -> list[str]:
    """Уникальные значения поля. Если переданы filters — из уже отфильтрованного датасета."""
    data = filter_alumni(filters) if filters else get_alumni_data()
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


def warm_cache() -> None:
    """Принудительно загружает обе таблицы в кэш. Вызывается при старте бота."""
    logger.info("Warming up cache…")
    try:
        get_registry_data()
        logger.info("Registry cache ready")
    except Exception as exc:
        logger.error("Failed to warm registry cache: %s", exc)
    try:
        get_alumni_data()
        logger.info("Alumni cache ready")
    except Exception as exc:
        logger.error("Failed to warm alumni cache: %s", exc)


def append_to_filter_report(rows: list[list]) -> None:
    """Дописывает строки в вкладку 'Отчет по фильтрам'.
    Каждая строка: [Попечитель, User_id, Категория, Фильтр]
    """
    ws = _get_spreadsheet().worksheet("Отчет по фильтрам")
    ws.append_rows(rows, value_input_option="USER_ENTERED")


def append_to_requests(row: list) -> None:
    """Записывает заявку во вкладку 'Заявки': [user_id, Попечитель, Телефон]."""
    ws = _get_spreadsheet().worksheet("Заявки")
    ws.append_rows([row], value_input_option="USER_ENTERED")


def upsert_fio_report(rows: list[dict]) -> None:
    """Вставляет новые строки или обновляет дату в существующих.
    Каждый элемент rows: {'trustee', 'user_id', 'full_name', 'date', 'action'}
    action = 'new' → append; action = 'update' → найти строку и обновить дату (кол. E).
    Структура листа: A=Попечитель B=user_id C=Соискатель D=Статус E=Дата
    """
    ws = _get_spreadsheet().worksheet("Отчет по ФИО")

    # Загружаем существующие данные один раз для поиска строк
    existing = ws.get_all_values()          # [[header], [row], ...]
    # Индекс: (str(user_id), full_name) → номер строки в листе (1-based)
    row_index: dict[tuple, int] = {}
    for i, row in enumerate(existing[1:], start=2):   # пропускаем заголовок
        if len(row) >= 3:
            row_index[(row[1].strip(), row[2].strip())] = i

    to_append: list[list] = []
    cell_updates: list[dict] = []

    for r in rows:
        key = (str(r["user_id"]), r["full_name"])
        if r["action"] == "update" and key in row_index:
            sheet_row = row_index[key]
            cell_updates.append({"range": f"E{sheet_row}", "values": [[r["date"]]]})
        else:
            to_append.append([r["trustee"], r["user_id"], r["full_name"], "Просмотрен", r["date"]])

    if cell_updates:
        ws.batch_update(cell_updates)
    if to_append:
        ws.append_rows(to_append, value_input_option="USER_ENTERED")
