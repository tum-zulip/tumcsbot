#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Keep the bot subscribed to all public streams.

Reason:
As the 'all_public_streams' parameter of the event API [1] does not
seem to work properly, we need a work-around in order to be able to
receive events for all public streams.

[1] https://zulip.com/api/register-queue#parameter-all_public_streams
"""

import asyncio
import logging
from typing import Any, Iterable, Literal

from tumcsbot.lib.client import AsyncClient
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import DMError, ZulipUser
from tumcsbot.plugin import Event,Plugin


class UserInput(Plugin):

    pending_inputs: dict[int, asyncio.Queue] = {}

    async def _get_previous_message(self, message: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.get_messages({"anchor": ["message_id"], "num_before": 1, "num_after": 0, "narrow": [{"operator": "sender", "operand": self.client.id}]})
        if response["result"] != "success":
            return {}

        msg = response["messages"][0]

        if msg["display_recipient"] != message["display_recipient"]:
            return {}
        
        return msg

    async def is_responsible_reaction(self, event: Event) -> bool:
        return (event.data["type"] == "reaction"
                and event.data["op"] == "add"
                and event.data["user_id"] != self.client.id
                and len(list(UserInput.pending_inputs.keys())) > 0
                and event.data["message_id"] in UserInput.pending_inputs)
                
    async def is_responsible_message(self, event: Event) -> bool:
        return (event.data["type"] == "message"
                and "message" in event.data
                and len(list(UserInput.pending_inputs.keys())) > 0
                and (await self._get_previous_message(event.data["message"]["id"])).get("id", -1) in UserInput.pending_inputs)

    async def is_responsible(self, event: Event) -> bool:
        return (
            await self.is_responsible_reaction(event) or
            await self.is_responsible_message(event)
        )

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        q: asyncio.Queue
        if event.data["type"] == "reaction":
            mid: int = event.data["message_id"]
            q = UserInput.pending_inputs[mid]

        elif event.data["type"] == "message":
            prior = await self._get_previous_message(event.data["message"]["id"])
            prior_id = prior.get("id", -1)
            q = UserInput.pending_inputs[prior_id]

        await q.put(event.data)
        self.client.trigger_dummy_event()
        await q.join()
                
        return Response.none()
    
    @staticmethod
    async def _wait_for_queue(q: asyncio.Queue, timeout: int) -> Any:
        for _ in range(timeout):
            try:
                return await asyncio.wait_for(q.get(), 1)
            except asyncio.TimeoutError:
                pass
        raise asyncio.TimeoutError()

    @classmethod
    async def confirm(cls, client: AsyncClient, message_id: int, timeout: int = 10) -> tuple[bool, dict[str, Any]]:
        emote, msg = await cls.choose(client, message_id, ["check", "cross_mark"], timeout)
        return emote == "check", msg
    

    @classmethod
    async def i8n_german_or_english(cls, client: AsyncClient, message_id: int, timeout: int = 10) -> tuple[Literal["de", "en"], dict[str, Any]]:
        emote, msg = await cls.choose(client, message_id, ["flag_germany", "flag_united_kingdom"], timeout)
        return "de" if emote == "flag_germany" else "en", msg

    @classmethod
    async def choose(cls, client: AsyncClient, message_id: int, emotes_to_choose: list[str], timeout: int = 10) -> tuple[str | None, dict[str, Any]]:
        """Ask the user for confirmation."""

        q = asyncio.Queue(1)
        cls.pending_inputs[message_id] = q

        # wait for UI to be ready, if we send instantly, the reaction might not be registered
        await asyncio.sleep(.5)

        for emote in emotes_to_choose:
            result = await client.send_response(Response.build_reaction_from_id(message_id, emote))
            if result["result"] != "success":
                raise Exception(f"Could not send reaction to user: {emote}")

        # todo: handle if message_nak or message_ack was not successful
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
                await client.remove_reaction({"message_id": message_id, "emoji_name": emote})


    @classmethod
    async def short_text_response(cls, message_id: int, timeout: int = 10, max_length=32, min_length=1, allow_spaces=False) -> tuple[str | None, dict[str, Any]]:
        """Ask the user for a short text."""

        q = asyncio.Queue(1)
        cls.pending_inputs[message_id] = q

        try:
            response = await cls._wait_for_queue(q, timeout)
            q.task_done()
            
            if "message" not in response:
                return None, response
            
            content: str = response["message"]["content"]
            content = content.strip(" \n\t\r\n")
            if len(content) > max_length:
                raise DMError(f"Text too long. Max length is {max_length}.")

            if len(content) < min_length:
                raise DMError(f"Text too short. Min length is {min_length}.")
            
            if not allow_spaces and " " in content:
                raise DMError("Spaces are not allowed.")
            
            return content, response
        except asyncio.TimeoutError:
            return None, {}
        
        finally:
            del cls.pending_inputs[message_id]
        
            





        
