"""Fill every table of an account (and of its user) from the ORM metadata.

Generic on purpose: a table added later gets a row too, so the "nothing of A
is left" checks cover it without anyone remembering to extend a fixture.
"""
from datetime import UTC, datetime

from sqlalchemy import BigInteger, Boolean, LargeBinary, Connection, DateTime, Float, Integer, func, select
from sqlalchemy.orm import Session, sessionmaker

from src.accounts.exit import account_tables, user_tables
from src.db.models import Base
from src.db.scope import system_scope
from src.db.vector import EMBED_DIM, Vector

# columns a generic value would break (CHECK constraints): table -> column -> value
OVERRIDES: dict[str, dict[str, str | None]] = {
    "automation_rules": {"sender_domain": "example.com"},  # ck_automation_rules_has_condition
    # ck_chat_card_items_one_target: exactly one target (the email)
    "chat_card_items": {"action_id": None, "attachment_id": None},
}


def _value(ctype: object, label: str) -> object:
    if isinstance(ctype, Vector):
        return (0.0,) * EMBED_DIM
    if isinstance(ctype, LargeBinary):
        return b"x"
    if isinstance(ctype, Boolean):
        return False
    if isinstance(ctype, DateTime):
        return datetime.now(UTC)
    if isinstance(ctype, Integer | BigInteger):
        return 1
    if isinstance(ctype, Float):
        return 0.0
    return label[: getattr(ctype, "length", None) or 200]


def seed_rows(conn: Connection, name: str, fixed: dict[str, int], tag: str,
              keys: dict[tuple[str, str, str], object]) -> None:
    """One row in `table`; `fixed` pins gmail_account_id / user_id; FK columns take
    the parent row's value recorded in `keys` for the same `tag`."""
    table = Base.metadata.tables[name]
    values: dict[str, object] = {}
    for col in table.c:
        if col.name in fixed:
            values[col.name] = fixed[col.name]
        elif col.name in OVERRIDES.get(table.name, {}):
            values[col.name] = OVERRIDES[table.name][col.name]
        elif col.foreign_keys:
            fk = next(iter(col.foreign_keys))
            parent = (fk.column.table.name, fk.column.name, tag)
            if parent in keys:
                values[col.name] = keys[parent]
            elif not col.nullable:
                raise AssertionError(f"{table.name}.{col.name}: parent {parent} not seeded")
        elif (col.primary_key and len(table.primary_key.columns) == 1
              and col.autoincrement is not False and isinstance(col.type, Integer)):
            continue
        elif col.nullable or col.default is not None or col.server_default is not None:
            continue
        else:
            values[col.name] = _value(col.type, f"{tag}-{table.name}-{col.name}")
    result = conn.execute(table.insert().values(**values))
    assert result.inserted_primary_key is not None
    for col, pk in zip(table.primary_key.columns, result.inserted_primary_key, strict=True):
        keys[(table.name, col.name, tag)] = pk


def seed_account(sf: sessionmaker[Session], account_id: int, user_id: int) -> None:
    """Seed users, gmail_accounts (must exist: tests create them), then the account's and user's tables."""
    tag = str(account_id)
    keys: dict[tuple[str, str, str], object] = {
        ("gmail_accounts", "id", tag): account_id,
        ("users", "id", tag): user_id,
    }
    with system_scope(), sf.begin() as s:
        conn = s.connection()
        for table in Base.metadata.sorted_tables:
            if table.name in user_tables():
                seed_rows(conn, table.name, {"user_id": user_id}, tag, keys)
            elif table.name in account_tables():
                seed_rows(conn, table.name, {"gmail_account_id": account_id}, tag, keys)


def rows_of(sf: sessionmaker[Session], account_id: int, user_id: int) -> dict[str, int]:
    """Row counts per table that still belong to the account or its user."""
    counts: dict[str, int] = {}
    with system_scope(), sf() as s:
        for table in Base.metadata.sorted_tables:
            if table.name in account_tables():
                where = table.c.gmail_account_id == account_id
            elif table.name in user_tables():
                where = table.c.user_id == user_id
            elif table.name == "gmail_accounts":
                where = table.c.id == account_id
            elif table.name == "users":
                where = table.c.id == user_id
            else:
                continue
            n = s.scalar(select(func.count()).select_from(table).where(where)) or 0
            if n:
                counts[table.name] = n
    return counts
