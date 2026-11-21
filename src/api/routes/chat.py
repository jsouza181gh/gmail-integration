"""/api/chat: conversations, turns (text or recorded WAV), the turn stream
(SSE) and decisions on cards.

The API only stores turns and enqueues `chat_turn`; the worker answers.
Card decisions are the one place where chat changes rules, KB, policies or
approvals, and they happen only on the user's click.
"""
import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from src.agent.types import Verdict
from src.api.deps import ApiDeps, get_deps, require_user
from src.chat.audio import MAX_AUDIO_BYTES, parse_wav
from src.chat.continuation import CardWaiter
from src.chat.events import card_event
from src.llm.config import load_chat_config
from src.chat.store import (
    CardConflict,
    CardView,
    ChatConflict,
    ConversationRow,
    MessageView,
    TurnView,
)
from src.jobs.types import ChatTurnJob
from src.llm.types import Schema

router = APIRouter(prefix="/api/chat", dependencies=[Depends(require_user)], tags=["chat"])
WAV_TYPES = frozenset({"audio/wav", "audio/x-wav", "audio/wave"})
FINAL = frozenset({"done", "failed"})


class TextIn(Schema):
    text: str
    model: str | None = None  # pin one model of the chat chain; None = the chain


class ModelsOut(Schema):
    models: list[str]


def _model(model: str | None) -> str | None:
    if model is not None and model not in load_chat_config().models:
        raise ValueError(f"modelo desconhecido: {model}")
    return model


class ConfirmIn(Schema):
    apply_existing: bool = False
    verdict: Verdict | None = None


def _enqueue(deps: ApiDeps, turn: TurnView) -> TurnView:
    deps.jobs.enqueue(ChatTurnJob(message_id=turn.assistant.id))
    return turn


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ChatConflict):
        return HTTPException(409, str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc).strip("'\""))
    return HTTPException(422, str(exc))


@router.get("/conversations")
def conversations(request: Request, limit: int = Query(30, ge=1, le=100)) -> list[ConversationRow]:
    return get_deps(request).chat.conversations(limit)


@router.get("/models")
def models() -> ModelsOut:
    """The chat's model chain, best first; the panel offers them to pin."""
    return ModelsOut(models=list(load_chat_config().models))


@router.post("/conversations")
def new_conversation(request: Request) -> ConversationRow:
    return get_deps(request).chat.create_conversation()


@router.get("/conversations/{conversation_id}/messages")
def messages(conversation_id: int, request: Request) -> list[MessageView]:
    try:
        return get_deps(request).chat.messages(conversation_id)
    except LookupError as exc:
        raise _http(exc) from exc


@router.post("/conversations/{conversation_id}/messages")
def send_text(conversation_id: int, body: TextIn, request: Request) -> TurnView:
    deps = get_deps(request)
    try:
        return _enqueue(
            deps, deps.chat.start_turn(conversation_id, text=body.text, model=_model(body.model))
        )
    except (ChatConflict, LookupError, ValueError) as exc:
        raise _http(exc) from exc


@router.post("/conversations/{conversation_id}/audio")
async def send_audio(
    conversation_id: int, request: Request, model: str | None = Query(None)
) -> TurnView:
    deps = get_deps(request)
    mime = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mime not in WAV_TYPES:
        raise HTTPException(415, "envie o áudio como WAV")
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "áudio grande demais")
    data = await request.body()
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "áudio grande demais")
    try:
        info = parse_wav(data)
        turn = await asyncio.to_thread(
            deps.chat.start_turn, conversation_id, audio=(data, info), model=_model(model)
        )
    except (ChatConflict, LookupError, ValueError) as exc:
        raise _http(exc) from exc
    return _enqueue(deps, turn)


@router.get("/messages/{message_id}/audio")
def audio(message_id: int, request: Request) -> Response:
    found = get_deps(request).chat.audio(message_id)
    if found is None:
        raise HTTPException(404, "áudio não encontrado")
    mime, data = found
    return Response(content=data, media_type=mime, headers={"Content-Disposition": "inline"})


@router.post("/messages/{message_id}/retry")
def retry(message_id: int, request: Request) -> TurnView:
    deps = get_deps(request)
    try:
        return _enqueue(deps, deps.chat.retry(message_id))
    except (ChatConflict, LookupError) as exc:
        raise _http(exc) from exc


@router.get("/messages/{message_id}/stream")
def stream(message_id: int, request: Request) -> StreamingResponse:
    deps = get_deps(request)
    try:
        deps.chat.turn(message_id)
    except LookupError as exc:
        raise _http(exc) from exc
    timing = deps.stream

    async def frames() -> AsyncIterator[str]:
        started = last_ping = time.monotonic()
        last: str | None = None
        while True:
            turn = await asyncio.to_thread(deps.chat.turn, message_id)
            body = turn.model_dump_json()
            if body != last:
                last = body
                yield f"event: turn\ndata: {body}\n\n"
            if turn.assistant.status in FINAL:
                return
            now = time.monotonic()
            if now - started >= timing.max_s:
                return
            if now - last_ping >= timing.ping_s:
                last_ping = now
                yield ": ping\n\n"
            await asyncio.sleep(timing.poll_s)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/cards/{card_id}/confirm")
def confirm(card_id: int, body: ConfirmIn, request: Request) -> CardView:
    deps = get_deps(request)
    try:
        view = deps.cards.confirm(card_id, apply_existing=body.apply_existing, verdict=body.verdict)
    except (CardConflict, LookupError, ValueError) as exc:
        raise _http(exc) from exc
    # the agent picks the conversation back up (finishes what was asked, or says so)
    conversation = deps.chat.card_conversation(card_id)
    if conversation is not None:
        text = card_event(view, deps.chat.card_action_kinds(card_id))
        runs = view.kind == "actions" and view.status == "confirmed" and view.verdict == "approve"
        if runs:  # the answer waits for the approved actions to run (progress meanwhile)
            if deps.chat.start_event_turn(conversation, text, wait_card_id=card_id) is not None:
                CardWaiter(deps.chat, deps.jobs).settle(card_id)  # all done already?
        elif (turn := deps.chat.start_event_turn(conversation, text)) is not None:
            _enqueue(deps, turn)
    return view


@router.post("/cards/{card_id}/cancel")
def cancel(card_id: int, request: Request) -> CardView:
    try:
        return get_deps(request).cards.cancel(card_id)
    except (CardConflict, LookupError) as exc:
        raise _http(exc) from exc
