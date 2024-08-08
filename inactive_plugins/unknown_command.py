#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import difflib
from typing import Any, Final, Iterable

from tumcsbot.lib.db import DB
from tumcsbot.lib.response import Response
from tumcsbot.plugin import Event,Plugin, Plugin, PluginTable
from tumcsbot.lib.utils import get_classes_from_path

class UnknownCommand(Plugin):
    """Handle unknown commands."""

    # This plugin depends on all the others because it needs their db entries.
    dependencies = [
        plugin_class.plugin_name()
        for plugin_class in get_classes_from_path("tumcsbot.plugins", Plugin)  # type: ignore
        if plugin_class.plugin_name() != "help"
    ]

    zulip_events = ["message"]
    _select_sql: Final[str] = "select name from Plugins"

    def _init_plugin(self) -> None:
        with DB.session() as session:
            self._command_names = [str(cmd.name) for cmd in session.query(PluginTable).all()]

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        message = event.data["message"]
        cmd = message["command_name"]

        matches = difflib.get_close_matches(cmd, self._command_names, n=2)

        if matches:
            matches = [f"`{match}`" for match in matches]
            return Response.build_message(
                message,
                f"Command not found. Did you mean {' or '.join(matches)}?",
            )
        
        return Response.build_reaction(event.data["message"], "question")

    async def is_responsible(self, event: Event) -> bool:
        return event.data["type"] == "message" and (
            "command_name" in event.data["message"]
            and event.data["message"]["command_name"]
            and event.data["message"]["command_name"] not in self._command_names
        )
