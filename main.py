import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN
from database import init_db
from handlers.admin import admin_stats
from handlers.auth import AWAITING_KEY, check_key, show_main_menu, start
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
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    init_db()
    logger.info("Database initialised")

    app = Application.builder().token(BOT_TOKEN).build()

    # ------------------------------------------------------------------
    # Auth conversation (only active until user enters a valid key once)
    # ------------------------------------------------------------------
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            AWAITING_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_key)
            ]
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
    )
    app.add_handler(conv)

    # ------------------------------------------------------------------
    # Admin command
    # ------------------------------------------------------------------
    app.add_handler(CommandHandler("admin", admin_stats))

    # ------------------------------------------------------------------
    # Inline keyboard callbacks
    # ------------------------------------------------------------------
    app.add_handler(CallbackQueryHandler(show_main_menu,       pattern=r"^main_menu$"))
    app.add_handler(CallbackQueryHandler(handle_search,        pattern=r"^search$"))
    app.add_handler(CallbackQueryHandler(handle_search,        pattern=r"^back_to_filters$"))

    app.add_handler(CallbackQueryHandler(handle_filter_select, pattern=r"^fsel:"))
    app.add_handler(CallbackQueryHandler(handle_filter_value,  pattern=r"^fval:"))
    app.add_handler(CallbackQueryHandler(handle_filter_page,   pattern=r"^fpage:"))
    app.add_handler(CallbackQueryHandler(handle_filter_clear,  pattern=r"^fclear:"))

    # Специальность — 2-этапный выбор
    app.add_handler(CallbackQueryHandler(handle_spec_stage2,   pattern=r"^spec_dir:"))
    app.add_handler(CallbackQueryHandler(handle_spec_all,      pattern=r"^spec_all$"))
    app.add_handler(CallbackQueryHandler(handle_spec_value,    pattern=r"^spec_val:"))

    app.add_handler(CallbackQueryHandler(handle_show_results,  pattern=r"^show_results$"))
    app.add_handler(CallbackQueryHandler(handle_results_page,  pattern=r"^rpage:"))
    app.add_handler(CallbackQueryHandler(handle_result_download, pattern=r"^res:"))
    app.add_handler(CallbackQueryHandler(handle_download_all,  pattern=r"^dl_all$"))

    logger.info("Bot started — polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
