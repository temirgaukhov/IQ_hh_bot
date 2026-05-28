import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN
from datetime import datetime

from database import (
    init_db,
    get_unsynced_filter_stats, mark_filter_stats_synced,
    get_fio_to_sync, upsert_fio_report_log,
)
from handlers.admin import admin_stats, admin_reply
from handlers.auth import (
    AWAITING_KEY, AWAITING_NAME, AWAITING_PHONE,
    check_key, show_main_menu, start,
    handle_apply_access, handle_retry_key, handle_name_input, handle_phone_input,
)
from handlers.search import (
    handle_download_all,
    handle_filter_clear,
    handle_filter_page,
    handle_filter_select,
    handle_filter_value,
    handle_result_download,
    handle_results_page,
    handle_search,
    handle_show_results,
    handle_spec_stage2,
    handle_spec_all,
    handle_spec_value,
    DIR_FIELD,
    SPEC_FIELD,
)
from sheets import append_to_filter_report, upsert_fio_report, get_trustee, warm_cache

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Как поля БД называются в отчёте
_FIELD_TO_CATEGORY = {
    DIR_FIELD:    "Специальность",   # Направление = первый этап
    "ВУЗ":        "ВУЗ",
    "Область":    "Область",
    "Район":      "Район",
    "Пол":        "Пол",
}


async def _sync_reports(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Каждые 10 минут синхронизирует данные в Google Sheets."""

    # ── Отчет по фильтрам ──────────────────────────────────────────────
    rows_data = get_unsynced_filter_stats()
    if rows_data:
        sheet_rows: list[list] = []
        all_ids: list[int] = []

        for row in rows_data:
            all_ids.append(row["id"])
            field = row["filter_field"]
            if field == SPEC_FIELD:          # второй этап — пропускаем
                continue
            category = _FIELD_TO_CATEGORY.get(field, field)
            trustee = get_trustee(row["access_key"])
            sheet_rows.append([trustee, row["user_id"], category, row["filter_value"]])

        try:
            if sheet_rows:
                append_to_filter_report(sheet_rows)
                logger.info("Отчет по фильтрам: добавлено %d строк", len(sheet_rows))
            mark_filter_stats_synced(all_ids)
        except Exception as exc:
            logger.error("Ошибка синхронизации фильтров: %s", exc)

    # ── Отчет по ФИО ───────────────────────────────────────────────────
    fio_to_sync = get_fio_to_sync()
    if fio_to_sync:
        fio_rows: list[dict] = []
        log_entries: list[tuple[int, str, str]] = []

        for row in fio_to_sync:
            date_str = datetime.fromisoformat(row["latest_date"]).strftime("%d.%m.%Y")
            trustee = get_trustee(row["access_key"])
            fio_rows.append({
                "trustee":   trustee,
                "user_id":   row["user_id"],
                "full_name": row["full_name"],
                "date":      date_str,
                "action":    row["action"],
            })
            log_entries.append((row["user_id"], row["full_name"], row["latest_date"]))

        try:
            upsert_fio_report(fio_rows)
            upsert_fio_report_log(log_entries)
            logger.info("Отчет по ФИО: обработано %d записей", len(fio_rows))
        except Exception as exc:
            logger.error("Ошибка синхронизации ФИО: %s", exc)


async def _post_init(app: Application) -> None:
    """Вызывается после инициализации бота, до начала поллинга."""
    warm_cache()


def main() -> None:
    init_db()
    logger.info("Database initialised")

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    # ------------------------------------------------------------------
    # Auth conversation (only active until user enters a valid key once)
    # ------------------------------------------------------------------
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AWAITING_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_key),
                CallbackQueryHandler(handle_retry_key,    pattern=r"^retry_key$"),
                CallbackQueryHandler(handle_apply_access, pattern=r"^apply_access$"),
            ],
            AWAITING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name_input),
            ],
            AWAITING_PHONE: [
                MessageHandler(filters.CONTACT, handle_phone_input),
            ],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
    )
    app.add_handler(conv)

    # ------------------------------------------------------------------
    # Admin commands
    # ------------------------------------------------------------------
    app.add_handler(CommandHandler("admin", admin_stats))
    app.add_handler(CommandHandler("reply", admin_reply))

    # ------------------------------------------------------------------
    # Inline keyboard callbacks
    # ------------------------------------------------------------------
    app.add_handler(CallbackQueryHandler(show_main_menu,         pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(handle_search,          pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(handle_search,          pattern=r"^back_to_filters$"))

    app.add_handler(CallbackQueryHandler(handle_filter_select,   pattern=r"^fsel:"))
    app.add_handler(CallbackQueryHandler(handle_filter_value,    pattern=r"^fval:"))
    app.add_handler(CallbackQueryHandler(handle_filter_page,     pattern=r"^fpage:"))
    app.add_handler(CallbackQueryHandler(handle_filter_clear,    pattern=r"^fclear:"))

    # Специальность — 2-этапный выбор
    app.add_handler(CallbackQueryHandler(handle_spec_stage2,     pattern=r"^spec_dir:"))
    app.add_handler(CallbackQueryHandler(handle_spec_all,        pattern=r"^spec_all$"))
    app.add_handler(CallbackQueryHandler(handle_spec_value,      pattern=r"^spec_val:"))

    app.add_handler(CallbackQueryHandler(handle_show_results,    pattern=r"^show_results$"))
    app.add_handler(CallbackQueryHandler(handle_results_page,    pattern=r"^rpage:"))
    app.add_handler(CallbackQueryHandler(handle_result_download, pattern=r"^res:"))
    app.add_handler(CallbackQueryHandler(handle_download_all,    pattern=r"^dl_all$"))

    # ------------------------------------------------------------------
    # Фоновый джоб: синхронизация фильтров в Google Sheets каждые 10 мин
    # ------------------------------------------------------------------
    app.job_queue.run_repeating(
        _sync_reports,
        interval=600,   # 10 минут
        first=60,       # первый запуск через 60 сек после старта
    )

    logger.info("Bot started — polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
