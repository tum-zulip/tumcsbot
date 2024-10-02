#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import logging
from typing import Any, Iterable

from tumcsbot.lib.response import Response
from tumcsbot.lib.conf import Conf
from tumcsbot.plugin import PluginCommand,Plugin


class Logfile(PluginCommand, Plugin):
    syntax = "logfile"
    description = "Get the bot's own logfile.\n[bot owner only]"

    async def handle_message(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        if not Conf.is_bot_owner(message["sender_id"]):
            return Response.privilege_err(message)

        handlers: list[logging.Handler] = logging.getLogger().handlers
        if not handlers or len(handlers) > 1:
            return Response.build_message(message, "Cannot determine the logfile.")

        if not isinstance(handlers[0], logging.FileHandler):
            return Response.build_message(message, "No logfile in use.")

        # Upload the logfile. (see https://zulip.com/api/upload-file)
        with open(handlers[0].baseFilename, "rb") as lf:
            result: dict[str, Any] = await self.client.call_endpoint(
                "user_uploads", method="POST", files=[lf]
            )

        if result["result"] != "success":
            return Response.build_message(message, "Could not upload the logfile.")

        return Response.build_message(message, f"[logfile]({result['uri']})")
