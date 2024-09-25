#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any, Iterable

from tumcsbot.lib.response import Response
from tumcsbot.plugin import PluginCommand, Plugin
from tumcsbot.lib.client import Event
from tumcsbot.lib.conf import Conf


class Restart(PluginCommand, Plugin):
    """
    Restart the bot[bot owner only].
    """
    syntax = "restart"

    async def handle_message(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        if not Conf.is_bot_owner(message["sender_id"]):
            return Response.privilege_err(message)

        await self.plugin_context.push_loopback(Event.restart_event(sender='restart'))
        await self.client.stop_typing_direct(message["sender_id"]) # trigger some event for eventloop to process

        return Response.none()
