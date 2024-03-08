#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

from typing import Any, Iterable, Callable
from inspect import cleandoc

from tumcsbot.lib import Response
from tumcsbot.command_parser import CommandParser
from tumcsbot.db import DB
from tumcsbot.plugin import PluginCommandMixin, PluginThread


class Rest(PluginCommandMixin, PluginThread):
    syntax = cleandoc(
        """
        rest method url [json_payload]
        """
    )
    description = cleandoc(
        """
        make rest requests. designed for inter-plugin usage.
        """
    )

    def parseMethod(s: str):
        method = s.strip().upper()
        if method in ["POST", "GET", "PUT", "PATCH", "DELETE"]:
            return method
        raise ValueError(f"'{method}' is not a rest method")

    def _init_plugin(self) -> None:
        pass

    def handle_message(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        result: tuple[str, CommandParser.Opts, CommandParser.Args] | None

        # Get command and parameters.
        result = self.command_parser.parse(message["command"])
        self.logger.debug(result)
        if result is None:
            return Response.command_not_found(message)
        command, opts, args = result

        if command in self.command_parser.commands:
            func: Callable[
                [dict[str, Any], CommandParser.Args, CommandParser.Opts],
                Response | Iterable[Response],
            ] = getattr(self, "_" + command)
            return func(message, args, opts)
        else:
            return Response.command_not_found(message)
