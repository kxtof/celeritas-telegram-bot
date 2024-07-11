import asyncio
from typing import Any
from typing import Awaitable
from typing import Dict

from telegram.ext import BaseUpdateProcessor


class UserSequentialUpdateProcessor(BaseUpdateProcessor):
    def __init__(self, max_concurrent_updates: int):
        super().__init__(max_concurrent_updates)
        self._user_locks: Dict[int, asyncio.Lock] = {}

    async def do_process_update(
        self,
        update: object,
        coroutine: Awaitable[Any],
    ) -> None:
        user_id = self._get_user_id(update)

        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()

        async with self._user_locks[user_id]:
            await coroutine

    def _get_user_id(self, update: object) -> int:
        if hasattr(update, "effective_user"):
            return update.effective_user.id
        else:
            return 0

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass
