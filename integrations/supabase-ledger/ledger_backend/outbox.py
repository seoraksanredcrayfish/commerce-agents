# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The outbox for customer messages: an inquiry reply on its channel thread, or a text to
an order's customer. The assistant stages a message; it leaves only when a person approves
it by name, and then through the workbench (:class:`~ledger_backend.workbench.WorkbenchCli`),
which posts or sends it. A refused send stays staged with the reason. The outbox is one
JSON file, so a message staged by one process is approved from another."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, TypeAdapter

from .workbench import WorkbenchError

MAX_BODY_CHARS = 2000


class OutboxError(ValueError):
    """The message is unknown, not staged, or not fit to stage or approve."""


class MessageKind(StrEnum):
    INQUIRY_REPLY = "inquiry_reply"
    ORDER_SMS = "order_sms"


class MessageStatus(StrEnum):
    STAGED = "staged"
    SENT = "sent"
    DISCARDED = "discarded"


class OutboundMessage(BaseModel):
    message_id: str
    kind: MessageKind
    target: str
    """The inquiry thread id for a reply, the order id for a text."""
    body: str
    sender_no: str | None = None
    status: MessageStatus = MessageStatus.STAGED
    staged_at: datetime
    approved_by: str | None = None
    sent_at: datetime | None = None
    last_error: str | None = None


class MessageWorkbench(Protocol):
    async def reply_to_inquiry(self, thread_id: str, body: str) -> None: ...

    async def send_order_sms(
        self, order_id: str, sender_no: str, content: str, *, sent_by: str
    ) -> None: ...


_Messages = TypeAdapter(list[OutboundMessage])


class Outbox:
    def __init__(self, path: str | os.PathLike[str], workbench: MessageWorkbench):
        self._path = Path(path)
        self._workbench = workbench

    def _load(self) -> list[OutboundMessage]:
        if not self._path.exists():
            return []
        return _Messages.validate_json(self._path.read_bytes())

    def _save(self, messages: list[OutboundMessage]) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_bytes(_Messages.dump_json(messages, indent=1))
        os.chmod(temporary, 0o600)
        temporary.replace(self._path)

    def _stage(self, kind: MessageKind, target: str, body: str, sender_no: str | None):
        if not target.strip():
            raise OutboxError("a message needs its thread or order")
        if not body.strip():
            raise OutboxError("a message needs a body")
        if len(body) > MAX_BODY_CHARS:
            raise OutboxError(f"a message body is at most {MAX_BODY_CHARS} characters")
        messages = self._load()
        message = OutboundMessage(
            message_id=f"msg-{len(messages) + 1:04d}",
            kind=kind,
            target=target,
            body=body,
            sender_no=sender_no,
            staged_at=datetime.now(UTC),
        )
        self._save([*messages, message])
        return message

    def stage_reply(self, thread_id: str, body: str) -> OutboundMessage:
        return self._stage(MessageKind.INQUIRY_REPLY, thread_id, body, None)

    def stage_sms(self, order_id: str, sender_no: str, body: str) -> OutboundMessage:
        if not sender_no.strip():
            raise OutboxError("a text needs a registered sender number")
        return self._stage(MessageKind.ORDER_SMS, order_id, body, sender_no)

    def pending(self) -> list[OutboundMessage]:
        return [m for m in self._load() if m.status is MessageStatus.STAGED]

    def _replace(self, updated: OutboundMessage) -> OutboundMessage:
        self._save([updated if m.message_id == updated.message_id else m for m in self._load()])
        return updated

    def _staged(self, message_id: str) -> OutboundMessage:
        for message in self._load():
            if message.message_id == message_id:
                if message.status is not MessageStatus.STAGED:
                    raise OutboxError(f"{message_id} is {message.status}, not staged")
                return message
        raise OutboxError(f"no message {message_id}")

    async def approve(self, message_id: str, *, approver: str) -> OutboundMessage:
        """Send one staged message; ``approver`` is the person who read and approved it."""
        approver = approver.strip()
        if not approver:
            raise OutboxError("approval needs the approver's name")
        message = self._staged(message_id)
        try:
            if message.kind is MessageKind.INQUIRY_REPLY:
                await self._workbench.reply_to_inquiry(message.target, message.body)
            else:
                await self._workbench.send_order_sms(
                    message.target, message.sender_no or "", message.body, sent_by=approver
                )
        except WorkbenchError as error:
            self._replace(message.model_copy(update={"last_error": str(error)}))
            raise
        return self._replace(
            message.model_copy(
                update={
                    "status": MessageStatus.SENT,
                    "approved_by": approver,
                    "sent_at": datetime.now(UTC),
                    "last_error": None,
                }
            )
        )

    def discard(self, message_id: str) -> OutboundMessage:
        message = self._staged(message_id)
        return self._replace(message.model_copy(update={"status": MessageStatus.DISCARDED}))


def _workbench_from_env(env: Mapping[str, str]) -> MessageWorkbench:
    from .tenant import tenant_from_env
    from .workbench import WorkbenchCli

    return WorkbenchCli(
        env["WORKBENCH_ROOT"], env["WORKBENCH_PACKAGE"], tenant_from_env(env).tenant_id
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ledger_backend.outbox",
        description="Stage customer messages; send one only when a person approves it.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="staged messages")
    reply = commands.add_parser("stage-reply", help="stage an inquiry reply")
    reply.add_argument("thread")
    reply.add_argument("body")
    sms = commands.add_parser("stage-sms", help="stage a text to an order's customer")
    sms.add_argument("order")
    sms.add_argument("sender")
    sms.add_argument("body")
    approve = commands.add_parser("approve", help="send one staged message")
    approve.add_argument("message_id")
    approve.add_argument("--by", required=True, help="the person who approved it")
    discard = commands.add_parser("discard", help="drop one staged message")
    discard.add_argument("message_id")
    return parser


async def main(
    argv: Sequence[str],
    env: Mapping[str, str] = os.environ,
    workbench: MessageWorkbench | None = None,
) -> int:
    """``LEDGER_OUTBOX`` names the outbox file; sending reads ``WORKBENCH_ROOT``,
    ``WORKBENCH_PACKAGE``, and the tenant variables. Exit codes: 0 done, 1 refused, 2
    usage."""
    try:
        args = _parser().parse_args(list(argv))
    except SystemExit as exit_:
        return int(exit_.code or 0)
    box = Outbox(env.get("LEDGER_OUTBOX", "outbox.json"), workbench or _LazyWorkbench(env))
    try:
        if args.command == "list":
            for message in box.pending():
                print(json.dumps(message.model_dump(mode="json"), ensure_ascii=False))
            return 0
        if args.command == "stage-reply":
            message = box.stage_reply(args.thread, args.body)
        elif args.command == "stage-sms":
            message = box.stage_sms(args.order, args.sender, args.body)
        elif args.command == "approve":
            message = await box.approve(args.message_id, approver=args.by)
        else:
            message = box.discard(args.message_id)
    except (OutboxError, WorkbenchError) as error:
        print(f"outbox: {error}", file=sys.stderr)
        return 1
    print(json.dumps(message.model_dump(mode="json"), ensure_ascii=False))
    return 0


class _LazyWorkbench:
    """Builds the workbench only for a send, so staging and listing need no checkout."""

    def __init__(self, env: Mapping[str, str]):
        self._env = env

    async def reply_to_inquiry(self, thread_id: str, body: str) -> None:
        await _workbench_from_env(self._env).reply_to_inquiry(thread_id, body)

    async def send_order_sms(
        self, order_id: str, sender_no: str, content: str, *, sent_by: str
    ) -> None:
        await _workbench_from_env(self._env).send_order_sms(
            order_id, sender_no, content, sent_by=sent_by
        )


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
