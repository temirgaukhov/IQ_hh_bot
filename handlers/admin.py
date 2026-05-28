import logging
from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from database import get_filter_stats, get_download_stats

logger = logging.getLogger(__name__)

_MAX_MSG = 4000  # немного меньше лимита Telegram 4096


def _split(text: str, limit: int = _MAX_MSG) -> list[str]:
    """Split long text into chunks that fit Telegram message limit."""
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts or [""]


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id

    if user_id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return

    filter_rows = get_filter_stats()
    download_rows = get_download_stats()

    lines: list[str] = ["📊 *Статистика*\n"]

    # ---- Filter stats grouped by access_key ----
    if filter_rows:
        lines.append("*Фильтры по ключам доступа:*")
        current_key: str | None = None
        for row in filter_rows:
            if row["access_key"] != current_key:
                current_key = row["access_key"]
                lines.append(f"\n🔑 `{current_key}`:")
            plural = _pluralize(row["cnt"])
            lines.append(f"  • {row['filter_field']} = {row['filter_value']}: {row['cnt']} {plural}")
    else:
        lines.append("_Фильтры ещё не применялись._")

    lines.append("")

    # ---- Download stats ----
    if download_rows:
        lines.append("*Скачанные резюме:*")
        for row in download_rows:
            plural = _pluralize(row["cnt"])
            lines.append(f"  • {row['full_name']}: {row['cnt']} {plural}")
    else:
        lines.append("_Резюме ещё не скачивались._")

    full_text = "\n".join(lines)

    for chunk in _split(full_text):
        await update.message.reply_text(chunk, parse_mode="Markdown")


def _pluralize(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "раз"
    rem = n % 10
    if rem == 1:
        return "раз"
    if 2 <= rem <= 4:
        return "раза"
    return "раз"
