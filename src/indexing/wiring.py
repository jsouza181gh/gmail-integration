"""Builds the real indexing pieces; one instance per API key (app, backfill)."""
from typing import Protocol

from src.alerts.service import build_alert_service
from src.classification.classifier import Classifier
from src.db.store import Store
from src.gmail.clients import GmailClients
from src.indexing.backfill import Backfill
from src.indexing.documents import DocumentIndexer
from src.indexing.indexer import Indexer
from src.indexing.store import IndexStore
from src.jobs.store import JobStore
from src.llm.types import Embedder, StructuredGenerator


class IndexingLLM(StructuredGenerator, Embedder, Protocol):
    """What indexing needs: the app gateway, or the backfill's router."""


class Indexing:
    def __init__(
        self, store: Store, jobs: JobStore, gateway: IndexingLLM,
        clients: GmailClients | None = None,
    ) -> None:
        clients = clients or GmailClients(store.sf)
        gmail = clients.getter()
        self.index_store = IndexStore(store.sf)
        self.indexer = Indexer(self.index_store, jobs, gateway, gmail)
        self.documents = DocumentIndexer(
            self.index_store, gateway, gmail, max_attempts=jobs.max_attempts
        )
        self.backfill = Backfill(
            store,
            self.index_store,
            self.indexer,
            Classifier(store, gateway),
            jobs,
            build_alert_service(store, clients),
            gmail,
        )
