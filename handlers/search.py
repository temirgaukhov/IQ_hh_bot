import io
import re
import logging

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import get_user, log_filter_usage, log_download
from sheets import get_unique_values, filter_alumni, get_authed_session

logger = logging.getLogger(__name__)

# Порядок и подписи фильтров
FILTER_FIELDS: list[tuple[str, str]] = [
    ("Специальность", "🎓 Специальность"),
    ("ВУЗ", "🏫 ВУЗ"),
    ("Область", "🌍 Область"),
    ("Район", "🏘 Район"),
    ("Пол", "👤 Пол"),
]
FIELD_LABEL: dict[str, str] = {f: l for f, l in FILTER_FIELDS}

# Специальность выбирается в 2 этапа: сначала Направление, потом Специальность
SPEC_FIELD = "Специальность"
DIR_FIELD = "Направление"

PAGE_SIZE = 15


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_access_key(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    if "access_key" not in context.user_data:
        row = get_user(user_id)
        if row:
            context.user_data["access_key"] = row["access_key"]
    return context.user_data.get("access_key", "unknown")


def _filter_keyboard(filters: dict[str, str], count: int) -> InlineKeyboardMarkup:
    rows = []
    for field, label in FILTER_FIELDS:
        if field == SPEC_FIELD:
            # 2-этапный фильтр: сначала Направление, потом Специальность
            if SPEC_FIELD in filters:
                text = f"❌ Сбросить фильтр: {filters[SPEC_FIELD]}"
                cb = f"fclear:{SPEC_FIELD}"
            elif DIR_FIELD in filters:
                # Выбрано «ВСЕ СОИСКАТЕЛИ» — только направление без специальности
                text = f"❌ Сбросить фильтр: {filters[DIR_FIELD]}"
                cb = f"fclear:{SPEC_FIELD}"
            else:
                text = label
                cb = f"fsel:{SPEC_FIELD}"
        else:
            if field in filters:
                text = f"❌ Сбросить фильтр: {filters[field]}"
                cb = f"fclear:{field}"
            else:
                text = label
                cb = f"fsel:{field}"
        rows.append([InlineKeyboardButton(text, callback_data=cb)])

    rows.append([InlineKeyboardButton(
        f"👥 Показать соискателей — {count} чел.",
        callback_data="show_results",
    )])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(rows)


def _values_keyboard(field: str, values: list[str], page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(values))
    rows = []
    for i in range(start, end):
        rows.append([InlineKeyboardButton(values[i], callback_data=f"fval:{i}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Пред.", callback_data=f"fpage:{field}:{page - 1}"))
    if end < len(values):
        nav.append(InlineKeyboardButton("След. ▶️", callback_data=f"fpage:{field}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("◀️ Назад к фильтрам", callback_data="back_to_filters")])
    return InlineKeyboardMarkup(rows)


def _is_top(person: dict) -> bool:
    return str(person.get("ТОП", "")).strip().lower() == "да"


def _results_keyboard(results: list[dict], page: int) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(results))
    rows = []
    for i in range(start, end):
        name = results[i].get("ФИО", "Без имени")
        label = f"{name} — ТОП кандидат ⭐" if _is_top(results[i]) else name
        rows.append([InlineKeyboardButton(label, callback_data=f"res:{i}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Пред.", callback_data=f"rpage:{page - 1}"))
    if end < len(results):
        nav.append(InlineKeyboardButton("След. ▶️", callback_data=f"rpage:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("📥 Скачать всё", callback_data="dl_all")])
    rows.append([InlineKeyboardButton("◀️ Назад к фильтрам", callback_data="back_to_filters")])
    return InlineKeyboardMarkup(rows)


def _extract_google_id(url: str) -> str | None:
    """Extract Google file/doc ID from any Google URL."""
    for pattern in (
        r"/(?:document|spreadsheets|presentation)/d/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def _guess_ext_from_response(resp: requests.Response, fallback: str) -> str:
    """Detect file extension from Content-Disposition or Content-Type."""
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename[^;=\n]*=["\'`]?([^"\'`;\n]+)', cd, re.IGNORECASE)
    if m:
        name_part = m.group(1).strip()
        _, dot_ext = name_part.rsplit(".", 1) if "." in name_part else ("", "")
        if dot_ext:
            return f".{dot_ext.lower()}"

    ct = resp.headers.get("Content-Type", "").lower()
    if "pdf" in ct:
        return ".pdf"
    if "spreadsheet" in ct or "excel" in ct or "xlsx" in ct:
        return ".xlsx"
    if "word" in ct or "docx" in ct or "document" in ct:
        return ".docx"

    return fallback


def _resolve_download_urls(url: str) -> list[tuple[str, str]]:
    """Return ordered list of (download_url, fallback_ext) to try."""
    url = url.strip()
    file_id = _extract_google_id(url)

    # Google Docs (native or uploaded .docx) — PDF export, fallback to direct
    if "docs.google.com/document" in url and file_id:
        return [
            (f"https://docs.google.com/document/d/{file_id}/export?format=pdf", ".pdf"),
            (f"https://drive.google.com/uc?export=download&id={file_id}", ".docx"),
        ]

    # Google Sheets (native or uploaded .xlsx) — xlsx export, fallback to direct
    if "docs.google.com/spreadsheets" in url and file_id:
        return [
            (f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx", ".xlsx"),
            (f"https://drive.google.com/uc?export=download&id={file_id}", ".xlsx"),
        ]

    # Google Drive direct file link — download as-is
    if "drive.google.com" in url and file_id:
        return [(f"https://drive.google.com/uc?export=download&id={file_id}", "")]

    return [(url, "")]


_GOOGLE_HOSTS = ("docs.google.com", "drive.google.com")


def _fetch(download_url: str) -> requests.Response:
    is_google = any(h in download_url for h in _GOOGLE_HOSTS)
    if is_google:
        # Use service-account session so private/restricted files are accessible
        session = get_authed_session()
        return session.get(download_url, timeout=30, allow_redirects=True)
    return requests.get(
        download_url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
        allow_redirects=True,
    )


async def _download_and_send(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    person: dict,
    access_key: str,
    user_id: int = 0,
) -> bool:
    resume_url = str(person.get("Резюме", "")).strip()
    name = person.get("ФИО", "Без имени")

    if not resume_url:
        await context.bot.send_message(chat_id, f"⚠️ У *{name}* нет ссылки на резюме.", parse_mode="Markdown")
        return False

    candidates = _resolve_download_urls(resume_url)
    resp = None
    ext = ""

    try:
        for url_attempt, ext_attempt in candidates:
            r = _fetch(url_attempt)
            # Google returns 400 for Office files via Docs export — try next candidate
            if r.status_code == 400 and len(candidates) > 1:
                logger.warning("Got 400 for %s (%s), trying fallback", name, url_attempt)
                continue
            r.raise_for_status()
            resp = r
            ext = ext_attempt
            break

        if resp is None:
            raise RuntimeError("All download attempts failed")

        detected_ext = _guess_ext_from_response(resp, ext or ".pdf")
        filename = f"{name}{detected_ext}"

        buf = io.BytesIO(resp.content)
        buf.name = filename

        await context.bot.send_document(
            chat_id=chat_id,
            document=buf,
            filename=filename,
            caption=f"📄 {name}",
        )
        log_download(access_key, user_id, name)
        return True

    except Exception as exc:
        logger.error("Download failed for %s: %s", name, exc)
        await context.bot.send_message(chat_id, f"❌ Ошибка при скачивании резюме *{name}*.", parse_mode="Markdown")
        return False


# ---------------------------------------------------------------------------
# Специальность — Этап 2 (выбор направления → список специальностей)
# ---------------------------------------------------------------------------

async def handle_spec_stage2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь выбрал Направление — показываем ВСЕ СОИСКАТЕЛИ + список Специальностей."""
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split(":", 1)[1])
    directions: list[str] = context.user_data.get("spec_directions", [])
    direction = directions[idx] if idx < len(directions) else ""
    context.user_data["spec_selected_direction"] = direction

    # Считаем с учётом уже активных фильтров + выбранное направление
    temp = {**context.user_data.get("filters", {}), DIR_FIELD: direction}
    temp.pop(SPEC_FIELD, None)
    all_in_dir = filter_alumni(temp)
    all_count = len(all_in_dir)

    # Уникальные специальности внутри этого направления
    specialties = sorted({
        str(r.get(SPEC_FIELD, "")).strip()
        for r in all_in_dir
        if str(r.get(SPEC_FIELD, "")).strip()
    })
    context.user_data["spec_specialties"] = specialties

    rows = [
        [InlineKeyboardButton(
            f"ВСЕ СОИСКАТЕЛИ  {all_count} чел.",
            callback_data="spec_all",
        )]
    ]
    for i, s in enumerate(specialties):
        rows.append([InlineKeyboardButton(s, callback_data=f"spec_val:{i}")])
    rows.append([InlineKeyboardButton("◀️ Назад к направлениям", callback_data=f"fsel:{SPEC_FIELD}")])

    await query.edit_message_text(
        f"Направление: *{direction}*\n\nВыберите специальность или смотрите всех:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def handle_spec_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """«ВСЕ СОИСКАТЕЛИ» — применяем только фильтр по Направлению."""
    query = update.callback_query
    await query.answer()

    direction = context.user_data.get("spec_selected_direction", "")
    filters = context.user_data.setdefault("filters", {})
    filters[DIR_FIELD] = direction
    filters.pop(SPEC_FIELD, None)

    user_id = update.effective_user.id
    access_key = await _get_access_key(context, user_id)
    log_filter_usage(access_key, user_id, DIR_FIELD, direction)

    count = len(filter_alumni(filters))
    await query.edit_message_text(
        "🔍 *Поиск соискателей*\n\nВыберите фильтры:",
        parse_mode="Markdown",
        reply_markup=_filter_keyboard(filters, count),
    )


async def handle_spec_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь выбрал конкретную Специальность."""
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split(":", 1)[1])
    direction = context.user_data.get("spec_selected_direction", "")
    specialties: list[str] = context.user_data.get("spec_specialties", [])
    specialty = specialties[idx] if idx < len(specialties) else ""

    filters = context.user_data.setdefault("filters", {})
    filters[DIR_FIELD] = direction
    filters[SPEC_FIELD] = specialty

    user_id = update.effective_user.id
    access_key = await _get_access_key(context, user_id)
    log_filter_usage(access_key, user_id, SPEC_FIELD, specialty)

    count = len(filter_alumni(filters))
    await query.edit_message_text(
        "🔍 *Поиск соискателей*\n\nВыберите фильтры:",
        parse_mode="Markdown",
        reply_markup=_filter_keyboard(filters, count),
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    filters: dict = context.user_data.setdefault("filters", {})
    count = len(filter_alumni(filters))

    await query.edit_message_text(
        "🔍 *Поиск соискателей*\n\nВыберите фильтры:",
        parse_mode="Markdown",
        reply_markup=_filter_keyboard(filters, count),
    )


async def handle_filter_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    field = query.data.split(":", 1)[1]

    # ── Этап 1 для Специальности: показываем список Направлений ──
    if field == SPEC_FIELD:
        directions = get_unique_values(DIR_FIELD)
        context.user_data["spec_directions"] = directions
        rows = []
        for i, d in enumerate(directions):
            rows.append([InlineKeyboardButton(d, callback_data=f"spec_dir:{i}")])
        rows.append([InlineKeyboardButton("◀️ Назад к фильтрам", callback_data="back_to_filters")])
        await query.edit_message_text(
            "Выберите *Направление*:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    # ── Обычные фильтры ──
    values = get_unique_values(field)
    context.user_data["selecting_field"] = field
    context.user_data["field_options"] = values

    label = FIELD_LABEL.get(field, field)
    await query.edit_message_text(
        f"Выберите значение для *{label}* (всего: {len(values)}):",
        parse_mode="Markdown",
        reply_markup=_values_keyboard(field, values, 0),
    )


async def handle_filter_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split(":", 1)[1])
    field: str = context.user_data.get("selecting_field", "")
    options: list[str] = context.user_data.get("field_options", [])

    if field and 0 <= idx < len(options):
        value = options[idx]
        context.user_data.setdefault("filters", {})[field] = value
        uid = update.effective_user.id
        access_key = await _get_access_key(context, uid)
        log_filter_usage(access_key, uid, field, value)

    filters = context.user_data.get("filters", {})
    count = len(filter_alumni(filters))

    await query.edit_message_text(
        "🔍 *Поиск соискателей*\n\nВыберите фильтры:",
        parse_mode="Markdown",
        reply_markup=_filter_keyboard(filters, count),
    )


async def handle_filter_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, field, page_str = query.data.split(":", 2)
    page = int(page_str)
    values: list[str] = context.user_data.get("field_options", [])
    label = FIELD_LABEL.get(field, field)

    await query.edit_message_text(
        f"Выберите значение для *{label}* (всего: {len(values)}):",
        parse_mode="Markdown",
        reply_markup=_values_keyboard(field, values, page),
    )


async def handle_filter_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    field = query.data.split(":", 1)[1]
    filters = context.user_data.get("filters", {})
    filters.pop(field, None)
    # Сброс Специальности убирает и Направление
    if field == SPEC_FIELD:
        filters.pop(DIR_FIELD, None)

    count = len(filter_alumni(filters))

    await query.edit_message_text(
        "🔍 *Поиск соискателей*\n\nВыберите фильтры:",
        parse_mode="Markdown",
        reply_markup=_filter_keyboard(filters, count),
    )


async def handle_show_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    filters = context.user_data.get("filters", {})
    results = filter_alumni(filters)

    if not results:
        await query.edit_message_text(
            "😔 По выбранным фильтрам соискателей не найдено.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("◀️ Назад к фильтрам", callback_data="back_to_filters")
            ]]),
        )
        return

    results.sort(key=lambda r: (not _is_top(r), str(r.get("ФИО", "")).strip().lower()))
    context.user_data["results"] = results

    await query.edit_message_text(
        f"👥 Найдено: *{len(results)} чел.*\n\nНажмите на имя, чтобы скачать резюме:",
        parse_mode="Markdown",
        reply_markup=_results_keyboard(results, 0),
    )


async def handle_results_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    page = int(query.data.split(":", 1)[1])
    results: list[dict] = context.user_data.get("results", [])

    await query.edit_message_text(
        f"👥 Найдено: *{len(results)} чел.*\n\nНажмите на имя, чтобы скачать резюме:",
        parse_mode="Markdown",
        reply_markup=_results_keyboard(results, page),
    )


async def handle_result_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("⏳ Скачиваю...")

    idx = int(query.data.split(":", 1)[1])
    results: list[dict] = context.user_data.get("results", [])
    if idx >= len(results):
        return

    uid = update.effective_user.id
    access_key = await _get_access_key(context, uid)
    await _download_and_send(context, update.effective_chat.id, results[idx], access_key, uid)


async def handle_download_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("⏳ Начинаю скачивание...")

    results: list[dict] = context.user_data.get("results", [])
    if not results:
        return

    uid = update.effective_user.id
    access_key = await _get_access_key(context, uid)
    chat_id = update.effective_chat.id
    total = len(results)

    progress = await context.bot.send_message(
        chat_id, f"⏳ Скачивание: 0 / {total}"
    )

    success = 0
    failed = 0

    for i, person in enumerate(results):
        name = person.get("ФИО", "Без имени")
        try:
            await progress.edit_text(
                f"⏳ Скачивание: {i + 1} / {total}\n"
                f"📥 {name}\n"
                f"✅ Готово: {success}  ❌ Ошибок: {failed}"
            )
        except Exception:
            pass

        ok = await _download_and_send(context, chat_id, person, access_key, uid)
        if ok:
            success += 1
        else:
            failed += 1

    summary = (
        f"✅ *Скачивание завершено*\n"
        f"Всего: {total} | Готово: {success} | Ошибок: {failed}"
    )
    try:
        await progress.edit_text(summary, parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(chat_id, summary, parse_mode="Markdown")
