"""A new account starts with a snapshot of the generic configuration.

Copied: the active taxonomy (set, flags, categories, examples), the active
agent configuration and the tool policies. Never copied: rules, KB, labels,
history (personal data). Later changes of the source do not propagate.
System scope, ids only: it writes rows, it never returns content.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.accounts import admin_account_id
from src.db.models import (
    AgentConfig,
    Category,
    CategoryExample,
    CategoryFlag,
    CategorySet,
    GmailAccount,
    ToolPolicy,
)
from src.db.scope import system_scope


def generic_source_account(sf: sessionmaker[Session]) -> int | None:
    """The `template` account of a fresh install, else the first admin's account."""
    with system_scope(), sf() as s:
        template = s.scalar(
            select(GmailAccount.id).where(GmailAccount.status == "template").order_by(GmailAccount.id)
        )
    return template if template is not None else admin_account_id(sf)


def seed_generic_config(sf: sessionmaker[Session], *, source: int, target: int) -> None:
    with system_scope(), sf.begin() as s:
        _copy_taxonomy(s, source, target)
        for config in s.scalars(
            select(AgentConfig).where(AgentConfig.gmail_account_id == source, AgentConfig.active)
        ).all():
            s.add(AgentConfig(
                gmail_account_id=target, version=config.version, active=True,
                decide_prompt=config.decide_prompt, chat_prompt=config.chat_prompt,
                max_turns=config.max_turns,
            ))
        for policy in s.scalars(select(ToolPolicy).where(ToolPolicy.gmail_account_id == source)).all():
            s.add(ToolPolicy(gmail_account_id=target, target=policy.target,
                             trigger=policy.trigger, policy=policy.policy))


def _copy_taxonomy(s: Session, source: int, target: int) -> None:
    live = s.scalars(
        select(CategorySet).where(CategorySet.gmail_account_id == source, CategorySet.active)
    ).first()
    if live is None:
        return
    new_set = CategorySet(gmail_account_id=target, version=live.version, active=True)
    s.add(new_set)
    s.flush()
    for flag in s.scalars(select(CategoryFlag).where(CategoryFlag.set_id == live.id)):
        s.add(CategoryFlag(gmail_account_id=target, set_id=new_set.id, name=flag.name,
                           definition=flag.definition))
    for cat in s.scalars(select(Category).where(Category.set_id == live.id)).all():
        copy = Category(gmail_account_id=target, set_id=new_set.id, name=cat.name,
                        definition=cat.definition, color=cat.color, position=cat.position)
        s.add(copy)
        s.flush()
        for example in s.scalars(
            select(CategoryExample).where(CategoryExample.category_id == cat.id)
        ).all():
            s.add(CategoryExample(gmail_account_id=target, category_id=copy.id,
                                  position=example.position, text=example.text))
