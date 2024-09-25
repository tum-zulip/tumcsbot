#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import asyncio
import logging
from typing import Any, Iterable, Literal, cast

from tumcsbot.lib.client import AsyncClient, Event
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import DMError
from tumcsbot.plugin import Plugin


class UserInput(Plugin):

    pending_inputs: dict[int, asyncio.Queue[dict[str, Any]]] = {}

    zulip_events = ["reaction", "message"]

    async def _get_previous_message(self, message: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.get_messages(
            {
                "anchor": message["id"],
                "num_before": 1,
                "num_after": 0,
                "narrow": [{"operator": "sender", "operand": self.client.id}],
            }
        )
        if response["result"] != "success":
            logging.error("Could not get previous message: %s", response)
            return {}

        msg = cast(dict[str, Any], response["messages"][0])
        if msg["display_recipient"] != message["display_recipient"]:
            return {}

        return msg

    async def is_responsible_reaction(self, event: Event) -> bool:
        return (
            event.data["type"] == "reaction"
            and event.data["op"] == "add"
            and event.data["user_id"] != self.client.id
            and len(list(UserInput.pending_inputs.keys())) > 0
            and event.data["message_id"] in UserInput.pending_inputs
        )

    async def is_responsible_message(self, event: Event) -> bool:
        return (
            event.data["type"] == "message"
            and "message" in event.data
            and len(list(UserInput.pending_inputs.keys())) > 0
            and (await self._get_previous_message(event.data["message"])).get("id", -1)
            in UserInput.pending_inputs
        )

    async def is_responsible(self, event: Event) -> bool:
        return await self.is_responsible_reaction(
            event
        ) or await self.is_responsible_message(event)

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        q: asyncio.Queue[dict[str, Any]]
        if event.data["type"] == "reaction":
            mid: int = event.data["message_id"]
            q = UserInput.pending_inputs[mid]

        elif event.data["type"] == "message":
            prior = await self._get_previous_message(event.data["message"])
            prior_id = prior.get("id", -1)
            q = UserInput.pending_inputs[prior_id]

        await q.put(event.data)
        self.client.trigger_dummy_event()
        await q.join()

        return Response.none()

    @staticmethod
    async def _wait_for_queue(q: asyncio.Queue[dict[str, Any]], timeout: int) -> Any:
        for _ in range(timeout):
            try:
                return await asyncio.wait_for(q.get(), 1)
            except asyncio.TimeoutError:
                pass
        raise asyncio.TimeoutError()

    @classmethod
    async def confirm(
        cls, client: AsyncClient, message_id: int, timeout: int = 10
    ) -> tuple[bool, dict[str, Any]]:
        emote, msg = await cls.choose(
            client, message_id, ["check", "cross_mark"], timeout
        )
        return emote == "check", msg

    @classmethod
    async def i8n_german_or_english(
        cls, client: AsyncClient, message_id: int, timeout: int = 10
    ) -> tuple[Literal["de", "en"], dict[str, Any]]:
        emote, msg = await cls.choose(
            client, message_id, ["flag_germany", "flag_united_kingdom"], timeout
        )
        return "de" if emote == "flag_germany" else "en", msg

    @classmethod
    async def choose(
        cls,
        client: AsyncClient,
        message_id: int,
        emotes_to_choose: list[str],
        timeout: int = 10,
    ) -> tuple[str | None, dict[str, Any]]:
        """Ask the user for confirmation."""

        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(1)
        cls.pending_inputs[message_id] = q

        # wait for UI to be ready, if we send instantly, the reaction might not be registered
        await asyncio.sleep(0.5)

        for emote in emotes_to_choose:
            result = await client.send_response(
                Response.build_reaction({"id": message_id}, emote)
            )
            if result["result"] != "success":
                logging.error(result)
                raise Exception(f"Could not send reaction to user: {emote}")

        try:
            reaction = await cls._wait_for_queue(q, timeout)
            q.task_done()
            if "emoji_name" not in reaction:
                return None, reaction
            return reaction["emoji_name"], reaction
        except asyncio.TimeoutError:
            return None, {}
        finally:
            del cls.pending_inputs[message_id]
            for emote in emotes_to_choose:
                await client.remove_reaction(
                    {"message_id": message_id, "emoji_name": emote}
                )

    @classmethod
    async def reaction(
        cls, message_id: int, timeout: int = 10
    ) -> tuple[str | None, dict[str, Any]]:
        """Ask the user for a reaction."""

        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(1)
        cls.pending_inputs[message_id] = q

        try:
            response = await cls._wait_for_queue(q, timeout)
            q.task_done()
            if "emoji_name" not in response:
                return None, response
            return response["emoji_name"], response
        except asyncio.TimeoutError:
            return None, {}

    @classmethod
    async def specific_reaction(
        cls, message_id: int, emote: str, timeout: int = 10
    ) -> tuple[bool, dict[str, Any]]:
        result, response = await cls.reaction(message_id, timeout)
        return result == emote, response

    @classmethod
    async def short_text_response(
        cls,
        client: AsyncClient,
        message_id: int,
        timeout: int = 10,
        max_length: int | None = 32,
        min_length: int | None = 1,
        allow_spaces: bool = False,
    ) -> tuple[str | None, dict[str, Any]]:
        """Ask the user for a short text."""

        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(1)
        cls.pending_inputs[message_id] = q

        try:
            response = await cls._wait_for_queue(q, timeout)
            q.task_done()

            if "message" not in response:
                return None, response

            if response["message"]["sender_id"] == client.id:
                return None, response

            content: str = response["message"]["content"]
            content = content.strip(" \n\t\r\n")
            if max_length is not None and len(content) > max_length:
                raise DMError(f"Text too long. Max length is {max_length}.")

            if min_length is not None and len(content) < min_length:
                raise DMError(f"Text too short. Min length is {min_length}.")

            if not allow_spaces and " " in content:
                raise DMError("Spaces are not allowed.")

            return content, response
        except asyncio.TimeoutError:
            return None, {}

        finally:
            del cls.pending_inputs[message_id]
