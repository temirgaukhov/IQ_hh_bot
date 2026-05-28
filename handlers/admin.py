import logging
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from database import get_filter_stats, get_download_stats
from sheets import get_trustee

logger = logging.getLogger(__name__)

_MAX_MSG = 4000


def _split(text: str, limit: int = _MAX_MSG) -> list[str]:
    parts, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur:
        parts.append(cur)
    return parts or [""]


def _pluralize(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "раз"
    rem = n % 10
    if rem == 1:
        return "раз"
    if 2 <= rem <= 4:
        return "раза"
    return "раз"


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ У вас нет доступа к этой команде.")
        return

    filter_rows = get_filter_stats()
    download_rows = get_download_stats()

    lines: list[str] = ["📊 *Статистика использования*\n"]

    # ── Фильтры: группируем по (попечитель, user_id) ──
    if filter_rows:
        lines.append("*🔍 Фильтры:*")

        # Собираем: {(trustee, user_id): [(field, value, cnt), ...]}
        grouped: dict[tuple, list] = defaultdict(list)
        for row in filter_rows:
            trustee = get_trustee(row["access_key"])
            key = (trustee, row["user_id"])
            grouped[key].append((row["filter_field"], row["filter_value"], row["cnt"]))

        for (trustee, uid), entries in sorted(grouped.items()):
            lines.append(f"\n👤 *{trustee}* (user\_id: `{uid}`):")
            for field, value, cnt in entries:
                lines.append(f"  • {field} = {value}: {cnt} {_pluralize(cnt)}")
    else:
        lines.append("_Фильтры ещё не применялись._")

    lines.append("")

    # ── Скачивания ──
    if download_rows:
        lines.append("*📥 Скачанные резюме:*")
        for row in download_rows:
            users_note = f" ({row['unique_users']} польз.)" if row["unique_users"] > 1 else ""
            lines.append(f"  • {row['full_name']}: {row['cnt']} {_pluralize(row['cnt'])}{users_note}")
    else:
        lines.append("_Резюме ещё не скачивались._")

    full_text = "\n".join(lines)
    for chunk in _split(full_text):
        await update.message.reply_text(chunk, parse_mode="Markdown")
