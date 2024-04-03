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
from typing import Any, Iterable

from tumcsbot.lib.client import AsyncClient
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import ZulipUser
from tumcsbot.plugin import Event,Plugin


class UserInput(Plugin):

    pending_inputs: dict[int, asyncio.Queue] = {}
    client: AsyncClient

    def _init_plugin(self) -> None:
        UserInput.client = self.client

    def is_responsible(self, event: Event) -> bool:
        return super().is_responsible(event) or (
            event.data["type"] == "reaction"
            and event.data["op"] == "add"
            and event.data["user_id"] != self.client.id
        )

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        uid: int = event.data["user_id"]
        mid: int = event.data["message_id"]
        self.logger.debug(event.data)
        self.logger.debug(UserInput.pending_inputs)
        if event.data["type"] == "reaction":
            if mid in UserInput.pending_inputs:
                self.logger.debug("putting reaction")
                await UserInput.pending_inputs[mid].put(event.data)
                self.logger.debug("queue id: " + str(id(UserInput.pending_inputs[mid])))
        return Response.none()

    @classmethod
    async def confirm(cls, user: ZulipUser, message: str, timeout: int = 10) -> bool:
        """Ask the user for confirmation."""
        message = await cls.client.send_response(Response.build_message(message=None, content=message, to=[user.id], msg_type="private"))
        m_id = message["id"]

        message_nak = await cls.client.send_response(Response.build_reaction_from_id(m_id, "cross_mark"))
        message_ack = await cls.client.send_response(Response.build_reaction_from_id(m_id, "check"))
        # todo: handle if message_nak or message_ack was not successful

        import logging
        cls.pending_inputs[m_id] = asyncio.Queue()
        logging.debug("queue id: " + str(id(cls.pending_inputs[m_id])))
        try:
            reaction = await cls.pending_inputs[m_id].get()
            logging.error(reaction)
            return reaction == "check"
        except asyncio.TimeoutError:
            return False
        finally:
            del cls.pending_inputs[m_id]
            await cls.client.remove_reaction({"message_id": m_id, "emoji_name": "cross_mark"})
            await cls.client.remove_reaction({"message_id": m_id, "emoji_name": "check"})
