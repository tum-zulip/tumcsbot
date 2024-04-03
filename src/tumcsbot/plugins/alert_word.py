#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

from random import randint
import re
from typing import Any, Iterable
from sqlalchemy import Column, String

from tumcsbot.lib.response import Response
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import DB, TableBase
from tumcsbot.plugin import Event, PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import *

class Alert(TableBase):
    __tablename__ = 'Alerts'

    Phrase = Column(String, primary_key=True)
    Emoji = Column(String, nullable=False)

class AlertWord(PluginCommandMixin, Plugin):
    """Manage reactions on certain words or phrases with emojis."""

    def _init_plugin(self) -> None:
        # Initialize the plugin's daemon part.
        # Get pattern and the alert_phrase - emoji bindings.
        self._bindings: list[tuple[re.Pattern[str], str]] = self._get_bindings()
        # Replace markdown links by their textual representation.
        self._markdown_links: re.Pattern[str] = re.compile(r"\[([^\]]*)\]\([^\)]+\)")

        self._received_command: bool = False
    
    def is_responsible(self, event: Event) -> bool:
        # First check whether the command mixin part is responsible.
        if super().is_responsible(event):
            self._received_command = True
            return True

        # Do not react on own messages or on private messages where we
        # are not the only recipient.
        return (
            event.data["type"] == "message"
            and event.data["message"]["sender_id"] != self.client.id
            and (
                event.data["message"]["type"] == "stream"
                or self.client.is_only_pm_recipient(event.data["message"])
            )
        )

    def _get_bindings(self) -> list[tuple[re.Pattern[str], str]]:
        """Compile the regexes and bind them to their emojis."""
        bindings: list[tuple[re.Pattern[str], str]] = []

        # Verify every regex and only use the valid ones.
        with DB.session() as session:
            for alert in session.query(Alert).all():
                try:
                    pattern: re.Pattern[str] = re.compile(alert.Phrase)
                except re.error:
                    continue
                bindings.append((pattern, alert.Emoji))

        return bindings
    
    @command(name="list")
    @privilege(Privilege.ADMIN)
    async def _list(self,
        _sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List all alert phrases and the corresponding emojis.
        """
        response: str = "Alert word or phrase | Emoji\n---- | ----"
        for alert in session.query(Alert).all():
            response += f"\n`{alert.Phrase}` | {alert.Emoji} :{alert.Emoji}:"
        yield Response.build_message(message, response)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("alert_phrase", str, description="The alert phrase regex to add.")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the reaction.")
    async def add(self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add an alert word / phrase together with the emoji the bot \
        should use to react on messages containing the corresponding \
        alert phrase.
        For the new alert phrases to take effect, please restart the \
        bot.
        Note that an alert phrase may be any regular expression.
        Hint: `\\b` represents word boundaries.
        """
        session.merge(Alert(Phrase=args.alert_phrase.lower(), Emoji=args.emoji))
        session.commit()
        yield Response.ok(message)


    @command
    @privilege(Privilege.ADMIN)
    @arg("alert_phrase", str, description="The alert phrase regex to remove.")
    async def remove(self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Remove an alert word / phrase.
        """
        alert_phrase: str = args.alert_phrase.lower()
        session.query(Alert).filter(Alert.Phrase == alert_phrase).delete()
        session.commit()
        yield Response.ok(message)

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        if self._received_command:
            self._received_command = False
            return await self.handle_message(event.data["message"])

        if not self._bindings:
            return Response.none()

        # Get message content.
        # Replace markdown links by their textual representation.
        # Convert to lowercase.
        content: str = self._markdown_links.sub(
            r"\1", event.data["message"]["content"]
        ).lower()

        return [
            Response.build_reaction(message=event.data["message"], emoji=emoji)
            for pattern, emoji in self._bindings
            if randint(1, 6) == 3 and pattern.search(content) is not None
        ]


