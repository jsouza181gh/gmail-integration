import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from src.classification.classifier import Classifier, classify_pending
from src.classification.seed import V1
from src.classification.taxonomy import Classification
from src.db.models import Email
from src.db.store import Store
from src.gmail.messages import MessageSummary
from tests.test_classifier import FakeGateway, always, batch, item


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> Store:
    s = Store(session_factory)
    s.create_category_set(V1)
    return s


def active_id(store: Store) -> int:
    tax = store.active_taxonomy()
    assert tax is not None
    return tax.set_id


def seed_email(store: Store, eid: str, category: str, category_set_id: int | None) -> None:
    run_id = store.get_or_create_run(f"seed:{eid}").id
    store.ingest(run_id, [eid], [MessageSummary(id=eid, sender="x", subject="s", date="d", snippet="sn")])
    store.save_classifications({eid: Classification(
        category=category, reason="r", is_relevant=True, deadline=None,
        has_document=True, ephemeral=False, category_set_id=category_set_id or 0,
    )})
    if category_set_id is None:  # rows classified before taxonomies existed
        with store.sf.begin() as s:
            s.execute(update(Email).where(Email.id == eid).values(category_set_id=None))


def test_reclassify_targets_only_outdated(store: Store) -> None:
    seed_email(store, "old", "Urgente", None)
    seed_email(store, "new", "Social", active_id(store))
    run_id = store.start_reclassify_run()
    assert {r.id for r in store.pending(run_id, 10)} == {"old"}


def test_reclassify_resets_classification_fields(store: Store) -> None:
    seed_email(store, "old", "Urgente", None)
    store.bump_attempts(["old"], "boom")
    run_id = store.start_reclassify_run()
    row = store.pending(run_id, 10)[0]
    assert (row.category, row.reason, row.is_relevant, row.has_document) == (None, None, None, None)
    assert (row.attempts, row.last_error, row.category_set_id) == (0, None, None)


def test_reclassify_all_resets_everything(store: Store) -> None:
    seed_email(store, "old", "Urgente", None)
    seed_email(store, "new", "Social", active_id(store))
    run_id = store.start_reclassify_run(only_outdated=False)
    assert {r.id for r in store.pending(run_id, 10)} == {"old", "new"}


def test_rerun_resumes_same_run(store: Store) -> None:
    seed_email(store, "old", "Urgente", None)
    first = store.start_reclassify_run()
    assert store.start_reclassify_run() == first


def test_reclassify_end_to_end_with_fake_gateway(store: Store) -> None:
    seed_email(store, "old", "Urgente", None)
    run_id = store.start_reclassify_run()
    gw = FakeGateway(always(batch(item("old", "Estudos", is_relevant=True))))
    classify_pending(store, Classifier(store, gw), run_id)
    row = store.report_rows(run_id)[0]
    assert (row.category, row.is_relevant, row.category_set_id) == ("Estudos", True, active_id(store))
