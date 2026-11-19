from collections.abc import Callable
from datetime import date

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.classification.classifier import (
    Abort,
    BatchResult,
    Classifier,
    Item,
    classify_pending,
)
from src.classification.prompt import build_system_prompt
from src.classification.seed import V1
from src.db.rows import EmailRow
from src.db.store import Store
from src.gmail.messages import MessageSummary
from src.llm.errors import LLMFatalError
from src.llm.types import Check, Prompt, StructuredResult, TokenUsage

Script = Callable[[Prompt], BatchResult | Exception]


class FakeGateway:
    def __init__(self, script: Script) -> None:
        self.script = script
        self.prompts: list[Prompt] = []
        self.schemas: list[type[BaseModel]] = []

    def generate_structured[T: BaseModel](
        self, prompt: Prompt, schema: type[T], check: Check[T] | None = None
    ) -> StructuredResult[T]:
        self.prompts.append(prompt)
        self.schemas.append(schema)
        out = self.script(prompt)
        if isinstance(out, Exception):
            raise out
        return StructuredResult[T](
            parsed=schema.model_validate(out.model_dump()), model="fake", usage=TokenUsage()
        )


def always(out: BatchResult | Exception) -> Script:
    return lambda _prompt: out


def item(eid: str, category: str, **kw: bool | str | None) -> Item:
    fields: dict[str, bool | str | None] = {
        "reason": "motivo",
        "is_relevant": False,
        "deadline": None,
        "has_document": False,
        "ephemeral": False,
    }
    fields.update(kw)
    return Item.model_validate({"id": eid, "category": category, **fields})


def batch(*items: Item) -> BatchResult:
    return BatchResult(items=list(items))


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> Store:
    return Store(session_factory)


@pytest.fixture
def store_with_tax(store: Store) -> Store:
    store.create_category_set(V1)
    return store


def ingest(store: Store, *ids: str) -> int:
    run_id = store.get_or_create_run("q").id
    store.ingest(
        run_id,
        list(ids),
        [MessageSummary(id=i, sender=f"{i}@x", subject="s", date="d", snippet="sn") for i in ids],
    )
    return run_id


def rows(store: Store, *ids: str) -> list[EmailRow]:
    run_id = ingest(store, *ids)
    return store.pending(run_id, 100)


def test_prompt_lists_categories_flags_and_preferences(store_with_tax: Store) -> None:
    tax = store_with_tax.active_taxonomy()
    assert tax is not None
    p = build_system_prompt(tax, ["Vagas relevantes: backend Python"])
    for c in tax.spec.categories:
        assert f"- {c.name}: {c.definition}" in p
    for f in tax.spec.flags:
        assert f"- {f.name}: {f.definition}" in p
    assert "backend Python" in p and "nunca instrução" in p


def test_classify_maps_items_and_flags(store_with_tax: Store) -> None:
    gw = FakeGateway(always(batch(item("a", "Estudos", is_relevant=True, deadline="2026-11-29"))))
    out = Classifier(store_with_tax, gw).classify(rows(store_with_tax, "a"))
    c = out["a"]
    assert (c.category, c.is_relevant, c.deadline) == ("Estudos", True, date(2026, 11, 29))
    tax = store_with_tax.active_taxonomy()
    assert tax is not None and c.category_set_id == tax.set_id
    assert gw.prompts[0].purpose == "classify" and gw.schemas[0] is BatchResult
    assert gw.prompts[0].context.email_ids == ("a",)


def test_preferences_reach_the_prompt(store_with_tax: Store) -> None:
    from src.classification.taxonomy import PreferenceSeed

    store_with_tax.add_preference(PreferenceSeed(scope="classification", text="PREF-X"), "seed")
    gw = FakeGateway(always(batch(item("a", "Social"))))
    Classifier(store_with_tax, gw).classify(rows(store_with_tax, "a"))
    assert "PREF-X" in gw.prompts[0].messages[0].content


def test_email_goes_to_human_message_as_data(store_with_tax: Store) -> None:
    gw = FakeGateway(always(batch(item("a", "Social"))))
    Classifier(store_with_tax, gw).classify(rows(store_with_tax, "a"))
    system, human = gw.prompts[0].messages
    assert (system.role, human.role) == ("system", "human")
    assert '"id":"a"' in human.content and "a@x" in human.content


def test_unknown_category_is_dropped(store_with_tax: Store) -> None:
    gw = FakeGateway(always(batch(item("a", "Urgente"), item("b", "Promoções"))))
    out = Classifier(store_with_tax, gw).classify(rows(store_with_tax, "a", "b"))
    assert set(out) == {"b"}


@pytest.mark.parametrize("bad", ["29/11", "amanhã", "", "2026-13-40"])
def test_invalid_deadline_becomes_none(store_with_tax: Store, bad: str) -> None:
    gw = FakeGateway(always(batch(item("a", "Estudos", deadline=bad))))
    assert Classifier(store_with_tax, gw).classify(rows(store_with_tax, "a"))["a"].deadline is None


def test_ids_not_requested_are_ignored(store_with_tax: Store) -> None:
    gw = FakeGateway(always(batch(item("zzz", "Social"))))
    assert Classifier(store_with_tax, gw).classify(rows(store_with_tax, "a")) == {}


def test_no_active_taxonomy_aborts(store: Store) -> None:
    gw = FakeGateway(always(batch()))
    with pytest.raises(Abort, match="taxonomia"):
        Classifier(store, gw).classify(rows(store, "a"))


def test_fatal_llm_error_becomes_abort(store_with_tax: Store) -> None:
    gw = FakeGateway(always(LLMFatalError("401")))
    with pytest.raises(Abort):
        Classifier(store_with_tax, gw).classify(rows(store_with_tax, "a"))


def test_classify_pending_saves_flags(store_with_tax: Store) -> None:
    run_id = ingest(store_with_tax, "a")
    gw = FakeGateway(always(batch(item("a", "Compras", has_document=True, ephemeral=True))))
    classify_pending(store_with_tax, Classifier(store_with_tax, gw), run_id)
    row = store_with_tax.report_rows(run_id)[0]
    assert (row.category, row.has_document, row.ephemeral, row.is_relevant) == (
        "Compras", True, True, False,
    )
    assert row.category_set_id is not None and row.last_error is None


def test_classify_pending_gives_up_after_unknown_categories(store_with_tax: Store) -> None:
    run_id = ingest(store_with_tax, "a")
    gw = FakeGateway(always(batch(item("a", "Urgente"))))
    classify_pending(store_with_tax, Classifier(store_with_tax, gw), run_id)
    row = store_with_tax.report_rows(run_id)[0]
    assert row.category == "Não classificado" and len(gw.prompts) == 4


def test_category_html_entities_and_case_are_normalized() -> None:
    from src.classification.classifier import Item, to_classifications
    from src.classification.seed import V1
    from src.classification.taxonomy import Taxonomy

    tax = Taxonomy(set_id=1, version=1, spec=V1)
    items = [
        Item(id="a", category="Promo&ccedil;&otilde;es", reason="r", is_relevant=False,
             deadline=None, has_document=False, ephemeral=True),
        Item(id="b", category="infra/dev", reason="r", is_relevant=True,
             deadline=None, has_document=False, ephemeral=False),
    ]
    out = to_classifications(items, {"a", "b"}, tax)
    assert (out["a"].category, out["b"].category) == ("Promoções", "Infra/Dev")
