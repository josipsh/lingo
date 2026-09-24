"""Container startup with mode-aware schema migration."""

import os


def main() -> None:
    bot_mode = os.getenv("BOT_MODE", "conversation").strip().lower()
    if bot_mode == "conversation":
        from lingo.migrate import main as migrate

        migrate()

    from lingo.server import main as run_server

    run_server()
