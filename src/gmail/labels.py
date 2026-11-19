"""Applies labels by name, creating missing ones (satisfies `ports.Labeler`).

Gmail ids are cached in `gmail_labels`; a modify that fails because a
cached id went stale (label deleted by the user) refreshes the cache once.
"""
from collections.abc import Callable, Sequence

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import GmailLabel
from src.utils.console import log

USER_ID = "me"


class _Label(BaseModel):
    id: str
    name: str


class _Labels(BaseModel):
    labels: list[_Label] = []


class GmailLabeler:
    def __init__(
        self,
        service_factory: Callable[[], Resource],
        session_factory: sessionmaker[Session],
        *,
        dry_run: bool = False,
    ) -> None:
        self._service = service_factory
        self.sf = session_factory
        self.dry_run = dry_run

    def _cached(self, names: Sequence[str]) -> dict[str, str]:
        with self.sf() as s:
            rows = s.execute(
                select(GmailLabel.name, GmailLabel.gmail_id).where(GmailLabel.name.in_(names))
            ).all()
        return {name: gid for name, gid in rows}

    def _resolve(self, service: Resource, names: Sequence[str]) -> list[str]:
        known = self._cached(names)
        missing = [n for n in names if n not in known]
        if missing:
            existing = {
                label.name: label.id
                for label in _Labels.model_validate(
                    service.users().labels().list(userId=USER_ID).execute(num_retries=5)
                ).labels
            }
            for name in missing:
                gid = existing.get(name)
                if gid is None:
                    created = _Label.model_validate(
                        service.users().labels().create(
                            userId=USER_ID,
                            body={
                                "name": name,
                                "labelListVisibility": "labelShow",
                                "messageListVisibility": "show",
                            },
                        ).execute(num_retries=5)
                    )
                    gid = created.id
                known[name] = gid
                with self.sf.begin() as s:
                    s.merge(GmailLabel(name=name, gmail_id=gid))
        return [known[n] for n in names]

    def _modify(self, service: Resource, email_id: str, ids: list[str]) -> None:
        service.users().messages().modify(
            userId=USER_ID, id=email_id, body={"addLabelIds": ids}
        ).execute(num_retries=5)

    def apply(self, email_id: str, names: Sequence[str]) -> tuple[str, ...]:
        if self.dry_run:
            log(f"[dry-run] labels {list(names)} em {email_id}")
            return tuple(names)
        service = self._service()
        try:
            self._modify(service, email_id, self._resolve(service, names))
        except HttpError as exc:
            if exc.resp.status not in (400, 404):
                raise
            with self.sf.begin() as s:
                s.execute(delete(GmailLabel).where(GmailLabel.name.in_(names)))
            self._modify(service, email_id, self._resolve(service, names))
        return tuple(names)
