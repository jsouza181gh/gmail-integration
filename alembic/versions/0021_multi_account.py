"""multi-account: gmail_accounts owns every content row

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

# Tables whose rows belong to one Gmail account (src.db.scope.AccountOwned).
# gmail_labels and account_settings carry the column in their primary key and
# are handled on their own.
OWNED = (
    "runs", "emails", "run_emails", "category_sets", "category_flags", "categories",
    "category_examples", "kb_entries", "llm_calls", "llm_call_messages", "agent_configs",
    "agent_runs", "agent_steps", "tool_policies", "automation_rules", "suggested_actions",
    "feedback", "notifications", "jobs", "email_embeddings", "attachments", "llm_call_emails",
    "config_audit", "chat_conversations", "chat_messages", "chat_message_refs", "chat_audio",
    "chat_cards", "chat_card_items",
)
# Names Postgres gave the unnamed constraints; SQLite batch mode reflects
# unnamed constraints under the same names through this convention.
NAMING = {"uq": "%(table_name)s_%(column_0_N_name)s_key"}
TEMPLATE_EMAIL = "template@invalid"
LIVE = sa.text("status IN ('pending', 'approved')")
# (table, old unique columns, new constraint name)
UNIQUES = (
    ("category_sets", ("version",), "uq_category_sets_account_version"),
    ("agent_configs", ("version",), "uq_agent_configs_account_version"),
    ("tool_policies", ("target", "trigger"), "uq_tool_policies_account_target_trigger"),
    ("jobs", ("idempotency_key",), "uq_jobs_account_idempotency_key"),
)


def upgrade() -> None:
    op.create_table(
        "gmail_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("email_address", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("history_id", sa.String(32)),
        sa.Column("watch_expiration", sa.BigInteger()),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("email", sa.Text()))
        batch.add_column(
            sa.Column("role", sa.String(16), nullable=False, server_default="member")
        )
        batch.create_unique_constraint("users_email_key", ["email"])

    _create_first_account()
    owner = sa.text("(SELECT min(id) FROM gmail_accounts)")
    for table in OWNED:
        op.add_column(table, sa.Column("gmail_account_id", sa.Integer()))
        op.execute(sa.text(f"UPDATE {table} SET gmail_account_id = {owner.text}"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("gmail_account_id", existing_type=sa.Integer(), nullable=False)
            batch.create_foreign_key(
                f"fk_{table}_gmail_account", "gmail_accounts", ["gmail_account_id"], ["id"]
            )
            batch.create_index(f"ix_{table}_gmail_account_id", ["gmail_account_id"])

    for table, cols, name in UNIQUES:
        with op.batch_alter_table(table, naming_convention=NAMING) as batch:
            batch.drop_constraint(f"{table}_{'_'.join(cols)}_key", type_="unique")
            batch.create_unique_constraint(name, ["gmail_account_id", *cols])

    op.drop_index("uq_suggested_actions_live", table_name="suggested_actions")
    op.create_index(
        "uq_suggested_actions_live", "suggested_actions", ["gmail_account_id", "email_id", "kind"],
        unique=True, postgresql_where=LIVE, sqlite_where=LIVE,
    )

    # gmail_labels: the account joins the primary key; copy through a new table
    op.create_table(
        "gmail_labels_new",
        sa.Column("gmail_account_id", sa.Integer(), sa.ForeignKey("gmail_accounts.id"),
                  primary_key=True),
        sa.Column("name", sa.String(255), primary_key=True),
        sa.Column("gmail_id", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(sa.text(
        f"INSERT INTO gmail_labels_new (gmail_account_id, name, gmail_id, updated_at) "
        f"SELECT {owner.text}, name, gmail_id, updated_at FROM gmail_labels"
    ))
    op.drop_table("gmail_labels")
    op.rename_table("gmail_labels_new", "gmail_labels")

    op.create_table(
        "account_settings",
        sa.Column("gmail_account_id", sa.Integer(), sa.ForeignKey("gmail_accounts.id"),
                  primary_key=True),
        sa.Column("llm_mode", sa.String(8)),
        sa.Column("backfill_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("backfill_turbo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def _create_first_account() -> None:
    """The single-user install becomes account 1 (its user turns admin).

    A fresh database (no watch_state) still holds what earlier migrations
    seeded (taxonomy, policies, agent config, KB): a `template` account owns
    it. It is never connected and has no user (user_id 0). A database with
    mail but no watch_state is unexpected: stop rather than guess an owner.
    """
    conn = op.get_bind()
    state = conn.execute(
        sa.text("SELECT email_address, history_id, expiration FROM watch_state")
    ).first()
    user = conn.execute(sa.text("SELECT min(id) FROM users")).scalar()
    insert = sa.text(
        "INSERT INTO gmail_accounts (id, user_id, email_address, status, history_id,"
        " watch_expiration, connected_at, updated_at)"
        " VALUES (1, :user, :email, :status, :history, :expiration,"
        " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    if state is None or user is None:
        if conn.execute(sa.text("SELECT count(*) FROM emails")).scalar():
            raise RuntimeError("0021: há e-mails mas não há watch_state/usuário para criar a conta")
        conn.execute(insert, {"user": 0, "email": TEMPLATE_EMAIL, "status": "template",
                              "history": None, "expiration": None})
        return
    email = str(state.email_address).strip().lower()
    conn.execute(insert, {"user": user, "email": email, "status": "connected",
                          "history": state.history_id, "expiration": state.expiration})
    conn.execute(
        sa.text("UPDATE users SET role = 'admin', email = :email WHERE id = :user"),
        {"email": email, "user": user},
    )


def downgrade() -> None:
    op.drop_table("account_settings")

    op.create_table(
        "gmail_labels_old",
        sa.Column("name", sa.String(255), primary_key=True),
        sa.Column("gmail_id", sa.String(64), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(sa.text(
        "INSERT INTO gmail_labels_old (name, gmail_id, updated_at) "
        "SELECT name, gmail_id, updated_at FROM gmail_labels"
    ))
    op.drop_table("gmail_labels")
    op.rename_table("gmail_labels_old", "gmail_labels")

    op.drop_index("uq_suggested_actions_live", table_name="suggested_actions")
    op.create_index(
        "uq_suggested_actions_live", "suggested_actions", ["email_id", "kind"],
        unique=True, postgresql_where=LIVE, sqlite_where=LIVE,
    )
    for table, cols, name in UNIQUES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_="unique")
            batch.create_unique_constraint(f"{table}_{'_'.join(cols)}_key", list(cols))

    for table in OWNED:
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_gmail_account_id")
            batch.drop_constraint(f"fk_{table}_gmail_account", type_="foreignkey")
            batch.drop_column("gmail_account_id")

    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("users_email_key", type_="unique")
        batch.drop_column("role")
        batch.drop_column("email")
    op.drop_table("gmail_accounts")
