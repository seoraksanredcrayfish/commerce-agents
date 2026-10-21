# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The write port: the store's operations workbench CLI, which calls the same function as
the workbench screen and so keeps its stock checks, idempotency, and audit log. This
package never writes a ledger table itself.

A call here is the platform write. It passes ``--yes`` because the approval has already
happened: :meth:`LedgerMerchantBackend.apply_change` runs only for a change the host
marked approved (``MerchantAgentConfig.require_host_approval``)."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence

from pydantic import BaseModel

Runner = Callable[[Sequence[str], Mapping[str, str], str], Awaitable[tuple[int, str]]]


class WorkbenchError(Exception):
    """The workbench refused or failed a command; the message is its reason."""


class StockFix(BaseModel):
    before: int
    after: int


async def run_subprocess(argv: Sequence[str], env: Mapping[str, str], cwd: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=dict(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    return process.returncode or 0, stdout.decode("utf-8", "replace")


def _last_json(stdout: str) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


class WorkbenchCli:
    """``root`` is the workbench checkout and ``package`` its app's pnpm package, whose
    ``cli`` script takes the commands; the tenant reaches the CLI as
    ``SUPABASE_TENANT_ID``, the variable the workbench reads it from."""

    def __init__(self, root: str, package: str, tenant_id: str, *, runner: Runner = run_subprocess):
        self._root = root
        self._package = package
        self._tenant_id = tenant_id
        self._runner = runner

    async def _run(self, command: str, *args: str) -> dict:
        argv = ["pnpm", "--filter", self._package, "cli", "--", command, *args, "--yes", "--json"]
        env = {**os.environ, "SUPABASE_TENANT_ID": self._tenant_id}
        code, stdout = await self._runner(argv, env, self._root)
        result = _last_json(stdout)
        if result is None:
            raise WorkbenchError(f"{command} returned no result: {stdout.strip()[-300:]}")
        if code != 0 or result.get("status") == "failed" or result.get("ok") is False:
            raise WorkbenchError(str(result.get("reason") or result.get("error") or result))
        return result

    async def set_stock(self, barcode: str, count: int) -> StockFix:
        """Set one barcode's sellable stock to ``count`` (``stock.set``)."""
        if count < 0 or int(count) != count:
            raise WorkbenchError(f"stock must be a whole number of zero or more, got {count}")
        result = await self._run("stock.set", "--barcode", barcode, "--count", str(int(count)))
        before = int(result["before"])
        return StockFix(before=before, after=int(result.get("after", before)))
