"""Run packaged Alembic migrations."""

from importlib.resources import as_file, files

from alembic import command
from alembic.config import Config


def main() -> None:
    migration_files = files("lingo.migrations")
    with as_file(migration_files) as migration_path:
        config = Config()
        config.set_main_option("script_location", str(migration_path).replace("%", "%%"))
        command.upgrade(config, "head")
