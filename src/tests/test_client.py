#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import asyncio
from functools import wraps
import unittest

from typing import Any, Callable, ClassVar

from zulip import Client as ZulipClient
from tumcsbot.lib.client import AsyncClient as TUMCSBotClient

def asSync(func: Callable[[Any], Any]) -> Callable[[Any], Any]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(func(*args, **kwargs))
        loop.close()
        return result
    return wrapper


class Client(TUMCSBotClient):
    def __init__(self) -> None:
        pass # Do not call the super constructor as it is a Mock.

    async def get_users(self, _: dict[str, Any] | None = None) -> dict[str, Any]:
        return await get_users()


class ClientGetUserIdsFromAttributeTest(unittest.TestCase):

    _client: ClassVar[Client]

    @classmethod
    def setUpClass(cls) -> None:
        cls._client = Client()

    @asSync
    async def test_get_user_ids_from_attribute(self) -> None:
        self.assertEqual(
            await self._client.get_user_ids_from_attribute(
                "not_existing_attribute", [1, 2, 3]
            ),
            [],
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute(
                "delivery_email", ["abc@zulip.org"]
            ),
            [1],
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute(
                "delivery_email", ["abc@zulip.org", "ghi@zulip.org"]
            ),
            [1, 3],
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute(
                "delivery_email", ["abc@zulip.org", "gHi@zulip.org"]
            ),
            [1],
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute(
                "delivery_email",
                ["abc@zulip.org", "gHi@zulip.org"],
                case_sensitive=False,
            ),
            [1, 3],
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute("user_id", [1, 3]), [1, 3]
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute(
                "user_id", [2, 3, 4], case_sensitive=False
            ),
            [2, 3],
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute("full_name", ["abc"]), [1, 2]
        )

    @asSync
    async def test_get_user_ids_from_display_names(self) -> None:
        self.assertEqual(
            await self._client.get_user_ids_from_attribute("full_name", ["abc"]),
            await self._client.get_user_ids_from_display_names(["abc"]),
        )
        self.assertEqual(
            await self._client.get_user_ids_from_attribute("full_name", ["aBc"]),
            await self._client.get_user_ids_from_display_names(["aBc"]),
        )


    @asSync
    async def test_get_user_ids_from_emails(self) -> None:
        self.assertEqual(
            await self._client.get_user_ids_from_attribute(
                "delivery_email",
                ["abc@zulip.org", "gHi@zulip.org"],
                case_sensitive=False,
            ),
            await self._client.get_user_ids_from_emails(["abc@zulip.org", "gHi@zulip.org"]),
        )


async def get_users() -> dict[str, Any]:
    await asyncio.sleep(0.1)
    return {
        "result": "success",
        "members": [
            {
                "delivery_email": "abc@zulip.org",
                "full_name": "abc",
                "user_id": 1,
            },
            {
                "delivery_email": "def@zulip.org",
                "full_name": "abc",
                "user_id": 2,
            },
            {
                "delivery_email": "ghi@zulip.org",
                "full_name": "ghi",
                "user_id": 3,
            },
        ],
    }
