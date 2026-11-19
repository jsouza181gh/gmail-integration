"""Builds the real indexing pieces (one shared gateway per process)."""
from src.alerts.service import build_alert_service
from src.classification.classifier import Classifier
from src.db.store import Store
from src.gmail.client import get_gmail_service
from src.indexing.backfill import Backfill
from src.indexing.documents import DocumentIndexer
from src.indexing.indexer import Indexer
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.llm.gateway import LLMGateway


class Indexing:
    def __init__(self, store: Store, jobs: JobStore, gateway: LLMGateway) -> None:
        self.index_store = IndexStore(store.sf)
        self.indexer = Indexer(self.index_store, jobs, gateway, get_gmail_service)
        self.documents = DocumentIndexer(
            self.index_store, gateway, get_gmail_service, max_attempts=jobs.max_attempts
        )
        self.backfill = Backfill(
            store,
            self.index_store,
            self.indexer,
            Classifier(store, gateway),
            jobs,
            build_alert_service(store),
            get_gmail_service,
        )
