import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from src.classification.seed import V1, V1_PREFERENCES
from src.classification.taxonomy import PreferenceSeed, TaxonomySpec
from src.db.store import Store

V1_NAMES = [
    "Pessoas", "Carreira", "Vagas", "Estudos", "Compras", "Finanças",
    "Contas & Segurança", "Infra/Dev", "Conteúdo", "Promoções", "Social",
]


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> Store:
    return Store(session_factory)


def test_no_taxonomy_returns_none(store: Store) -> None:
    assert store.active_taxonomy() is None


def test_create_and_read_active_set(store: Store) -> None:
    sid = store.create_category_set(V1)
    tax = store.active_taxonomy()
    assert tax is not None and tax.set_id == sid and tax.version == 1
    assert tax.names == V1_NAMES
    assert tax.spec == V1  # full round-trip: definitions, examples, flags


def test_new_version_deactivates_previous(store: Store) -> None:
    store.create_category_set(V1)
    store.create_category_set(V1.model_copy(update={"categories": V1.categories[:2]}))
    tax = store.active_taxonomy()
    assert tax is not None and tax.version == 2 and tax.names == ["Pessoas", "Carreira"]


def test_inactive_set_is_not_returned(store: Store) -> None:
    store.create_category_set(V1, activate=False)
    assert store.active_taxonomy() is None


def test_spec_rejects_duplicate_category() -> None:
    with pytest.raises(ValidationError):
        TaxonomySpec(categories=(V1.categories[0], V1.categories[0]), flags=V1.flags)


def test_spec_rejects_missing_flag() -> None:
    with pytest.raises(ValidationError):
        TaxonomySpec(categories=V1.categories, flags=V1.flags[:3])


def test_preferences_filtered_by_scope_and_active(store: Store) -> None:
    store.add_preference(PreferenceSeed(scope="classification", text="a"), "seed")
    store.add_preference(PreferenceSeed(scope="chat", text="b"), "seed")
    assert store.active_preferences("classification") == ["a"]


def test_seed_has_job_preference() -> None:
    assert [p.scope for p in V1_PREFERENCES] == ["classification"]
    assert "backend" in V1_PREFERENCES[0].text
