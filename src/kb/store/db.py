import re
from importlib import resources

import psycopg
from psycopg.rows import dict_row

from kb.errors import KbError

_MIGRATIONS = resources.files("kb.store") / "migrations"


def connect(database_url: str) -> psycopg.Connection:
    """Conexão em autocommit: blocos atômicos são abertos explicitamente com `conn.transaction()`."""
    try:
        return psycopg.connect(database_url, autocommit=True, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        raise KbError(
            f"Não foi possível conectar ao banco ({_hide_password(database_url)}). "
            "Suba o Postgres com: docker compose up -d postgres"
        ) from exc


def apply_migrations(conn: psycopg.Connection) -> list[str]:
    """Aplica, em ordem, os arquivos .sql de migrations/ que ainda não foram aplicados."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " versao text PRIMARY KEY, aplicada_em timestamptz NOT NULL DEFAULT now())"
        )
        applied = {row["versao"] for row in conn.execute("SELECT versao FROM schema_migrations")}
        new = []
        for migration in sorted(_MIGRATIONS.iterdir(), key=lambda item: item.name):
            if not migration.name.endswith(".sql") or migration.name in applied:
                continue
            conn.execute(migration.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (versao) VALUES (%s)", (migration.name,))
            new.append(migration.name)
    return new


def _hide_password(url: str) -> str:
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)
