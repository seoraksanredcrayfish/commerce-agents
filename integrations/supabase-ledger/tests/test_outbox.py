# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The outbox: customer messages the assistant drafts are staged, and only a named approver
sends one, through the workbench."""

from __future__ import annotations

import pytest
from ledger_backend.outbox import MessageStatus, Outbox, OutboxError
from ledger_backend.workbench import WorkbenchError


class FakeWorkbench:
    def __init__(self, error: str | None = None):
        self.error = error
        self.replies: list[tuple[str, str]] = []
        self.texts: list[tuple[str, str, str, str]] = []

    async def reply_to_inquiry(self, thread_id, body):
        if self.error:
            raise WorkbenchError(self.error)
        self.replies.append((thread_id, body))

    async def send_order_sms(self, order_id, sender_no, content, *, sent_by):
        if self.error:
            raise WorkbenchError(self.error)
        self.texts.append((order_id, sender_no, content, sent_by))


def outbox(tmp_path, workbench=None) -> Outbox:
    return Outbox(tmp_path / "outbox.json", workbench or FakeWorkbench())


async def test_staging_a_reply_sends_nothing(tmp_path):
    workbench = FakeWorkbench()
    box = outbox(tmp_path, workbench)

    message = box.stage_reply("thread-1", "Hello:) It ships next week. Thank you")

    assert message.status is MessageStatus.STAGED
    assert [m.message_id for m in box.pending()] == [message.message_id]
    assert workbench.replies == []


async def test_an_approved_reply_posts_once_and_records_the_approver(tmp_path):
    workbench = FakeWorkbench()
    box = outbox(tmp_path, workbench)
    message = box.stage_reply("thread-1", "Hello")

    sent = await box.approve(message.message_id, approver="owner")

    assert workbench.replies == [("thread-1", "Hello")]
    assert (sent.status, sent.approved_by) == (MessageStatus.SENT, "owner")
    assert sent.sent_at is not None
    with pytest.raises(OutboxError):
        await box.approve(message.message_id, approver="owner")
    assert len(workbench.replies) == 1


async def test_an_approved_text_carries_the_approver_to_the_workbench(tmp_path):
    workbench = FakeWorkbench()
    box = outbox(tmp_path, workbench)
    message = box.stage_sms("20260101-0000001", "02-000-0000", "Hello")

    await box.approve(message.message_id, approver="owner")

    assert workbench.texts == [("20260101-0000001", "02-000-0000", "Hello", "owner")]


async def test_approval_needs_a_named_approver(tmp_path):
    workbench = FakeWorkbench()
    box = outbox(tmp_path, workbench)
    message = box.stage_reply("thread-1", "Hello")

    with pytest.raises(OutboxError):
        await box.approve(message.message_id, approver="  ")
    assert workbench.replies == []


async def test_a_refused_send_stays_staged_with_the_reason(tmp_path):
    box = outbox(tmp_path, FakeWorkbench(error="channel key missing"))
    message = box.stage_reply("thread-1", "Hello")

    with pytest.raises(WorkbenchError):
        await box.approve(message.message_id, approver="owner")

    [pending] = box.pending()
    assert pending.status is MessageStatus.STAGED
    assert pending.last_error == "channel key missing"


async def test_a_discarded_message_can_no_longer_be_approved(tmp_path):
    workbench = FakeWorkbench()
    box = outbox(tmp_path, workbench)
    message = box.stage_reply("thread-1", "Hello")

    box.discard(message.message_id)

    assert box.pending() == []
    with pytest.raises(OutboxError):
        await box.approve(message.message_id, approver="owner")
    assert workbench.replies == []


async def test_the_outbox_survives_a_restart(tmp_path):
    first = outbox(tmp_path)
    message = first.stage_reply("thread-1", "Hello")

    second = outbox(tmp_path)

    assert [m.message_id for m in second.pending()] == [message.message_id]
    assert second.stage_reply("thread-2", "Hi").message_id != message.message_id


def test_a_blank_or_overlong_body_is_refused_when_staged(tmp_path):
    box = outbox(tmp_path)

    with pytest.raises(OutboxError):
        box.stage_reply("thread-1", " ")
    with pytest.raises(OutboxError):
        box.stage_sms("o-1", "02-000-0000", "x" * 2001)
    assert box.pending() == []


async def test_the_command_line_stages_lists_and_approves(tmp_path, capsys):
    from ledger_backend.outbox import main

    workbench = FakeWorkbench()
    env = {"LEDGER_OUTBOX": str(tmp_path / "outbox.json")}

    assert await main(["stage-reply", "thread-1", "Hello"], env, workbench) == 0
    assert await main(["list"], env, workbench) == 0
    listed = capsys.readouterr().out
    assert "msg-0001" in listed and "thread-1" in listed and "Hello" in listed

    assert await main(["approve", "msg-0001"], env, workbench) == 2
    assert workbench.replies == []
    assert await main(["approve", "msg-0001", "--by", "owner"], env, workbench) == 0
    assert workbench.replies == [("thread-1", "Hello")]


async def test_the_command_line_reports_a_refused_send(tmp_path, capsys):
    from ledger_backend.outbox import main

    env = {"LEDGER_OUTBOX": str(tmp_path / "outbox.json")}
    workbench = FakeWorkbench(error="channel key missing")
    await main(["stage-sms", "o-1", "02-000-0000", "Hello"], env, workbench)

    assert await main(["approve", "msg-0001", "--by", "owner"], env, workbench) == 1
    assert "channel key missing" in capsys.readouterr().err
