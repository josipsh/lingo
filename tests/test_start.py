import sys
from types import SimpleNamespace
from unittest.mock import Mock

from lingo.start import main


def test_echo_start_skips_migrations_with_normalized_mode(monkeypatch):
    migrate = Mock()
    server = Mock()
    monkeypatch.setenv("BOT_MODE", " ECHO_LIVE ")
    monkeypatch.setitem(sys.modules, "lingo.migrate", SimpleNamespace(main=migrate))
    monkeypatch.setitem(sys.modules, "lingo.server", SimpleNamespace(main=server))
    main()
    migrate.assert_not_called()
    server.assert_called_once()


def test_conversation_start_runs_migrations(monkeypatch):
    migrate = Mock()
    server = Mock()
    monkeypatch.setenv("BOT_MODE", "conversation")
    monkeypatch.setitem(sys.modules, "lingo.migrate", SimpleNamespace(main=migrate))
    monkeypatch.setitem(sys.modules, "lingo.server", SimpleNamespace(main=server))
    main()
    migrate.assert_called_once()
    server.assert_called_once()
