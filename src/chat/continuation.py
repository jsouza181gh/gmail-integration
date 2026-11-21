"""Releases chat continuations waiting on an actions card once its actions ran."""
from src.chat.store import ChatStore
from src.jobs.store import JobStore
from src.jobs.types import ChatTurnJob


class CardWaiter:
    def __init__(self, store: ChatStore, jobs: JobStore) -> None:
        self.store = store
        self.jobs = jobs

    def settle(self, card_id: int) -> None:
        answer = self.store.settle_card(card_id)
        if answer is not None:
            self.jobs.enqueue(ChatTurnJob(message_id=answer))

    def on_action_settled(self, action_id: int) -> None:
        """An approved action was executed or failed (worker or dead-letter)."""
        for card_id in self.store.cards_of_action(action_id):
            self.settle(card_id)
