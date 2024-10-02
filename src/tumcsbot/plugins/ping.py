#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Iterable

from tumcsbot.lib.response import Response
from tumcsbot.plugin import Plugin
from tumcsbot.lib.client import Event


class Ping(Plugin):
    """The user pinged us. Still be nice! :)

    Do not react on pings in private messages that do not contain a
    command! Otherwise, we'll reach the API rate limit when we
    subscribe a lot of users to a channel, the Notification Bot
    notifies them of the subscription (with ping) and we react on the
    messages of the Notification Bot to the users.
    """
    zulip_events = ["message"]

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        return Response.build_reaction(event.data["message"], "wave")

    async def is_responsible(self, event: Event) -> bool:
        return event.data["type"] == "message" and (
            (
                # Only handle command messages if the command is empty.
                "command_name" in event.data["message"]
                and not event.data["message"]["command_name"]
            )
            or (
                "command_name" not in event.data["message"]
                and event.data["message"]["sender_id"] != self.client.id
                and "mentioned" in event.data["flags"]
                and (
                    not event.data["message"]["type"] == "private"
                    or self.client.is_only_pm_recipient(event.data["message"])
                )
            )
        )
