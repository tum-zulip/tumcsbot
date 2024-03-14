#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
from typing import Any, Final, Iterable, cast

from tumcsbot.lib import DB, Response, get_classes_from_path
from tumcsbot.plugin import Event,Plugin, Plugin

from openai import OpenAI

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
        self._command_names: Iterable[str] = list(
            map(lambda t: cast(str, t[0]), DB(read_only=True).execute(self._select_sql))
        )

    def handle_event(self, event: Event) -> Response | Iterable[Response]:
        message = event.data["message"]

        request: dict[str, Any] = {
            "anchor": "newest",
            "num_before": 5,
            "num_after": 0,
            "narrow": [
                {"operator": "dm", "operand": message["sender_email"]},
            ],
        }

        result = self.client.get_messages(request)

        client = OpenAI(base_url="http://10.10.10.12:1234/v1", api_key="not-needed")
        completion = client.chat.completions.create(
            model="local-model", # this field is currently unused
            messages=[
              {"role": "system", "content": cleandoc(
                  f"""
                    You are TUMCSBot, a helpful bot for managing the zulip chat at TUM.
                    You may use the smilies :), :D, :P, :O, :|, :/, :S, :* and :(.
                    You are chatting with the user {message['sender_full_name']}.
                  """
                  )
                },
            ] + [
              {"role": "user" if message['sender_full_name'] != "TUMCSBot" else "system", "content": message["content"]}
              for message in result["messages"]
            ],
            temperature=0.7,
        )
        return Response.build_reaction(event.data["message"], "question"), Response.build_message(event.data["message"], completion.choices[0].message.content)

    def is_responsible(self, event: Event) -> bool:
        return event.data["type"] == "message" and (
            "command_name" in event.data["message"]
            and event.data["message"]["command_name"]
            and event.data["message"]["command_name"] not in self._command_names
        )
