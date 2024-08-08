#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

# TODO: replacement for zulip usergroups. Replace as soon as api allows bot requests for usergroups

import logging
from typing import Any, AsyncGenerator
from sqlalchemy import Column, Integer, String, ForeignKey
import sqlalchemy
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.ext.hybrid import hybrid_property
import yaml
from zulip import ZulipStream

from tumcsbot.lib.client import AsyncClient
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.conf import Conf
from tumcsbot.lib.db import Session, TableBase, serialize_model
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import DMMessage, DMResponse
from tumcsbot.plugin import PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import command
from tumcsbot.plugins.userinput import UserInput


class ZulipLogHandler(logging.Handler):
    """
    A handler class which sends
    log records to a Zulip stream.
    """

    def __init__(self, stream_id: int, client: AsyncClient, log_level: int = logging.INFO) -> None:
        logging.Handler.__init__(self)
        self.stream_id = stream_id
        self.client = client
        self.setLevel(log_level)

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a record.
        """
        msg = self.format(record)

        response = self.client.as_sync().get_messages({"anchor": "newest", "num_before": 100, "num_after": 0, "narrow": [
            {"operator": "sender", "operand": self.client.id},
            {"operator": "channel", "operand": self.stream_id}
        ]})
        if response["result"] == "success":
            # delete old messages sent from this bot
            msgs = [m for m in response["messages"] if m["sender_id"] == self.client.id]
            msgs = msgs[:len(msgs) - 10]
            for m in msgs:
                self.client.as_sync().delete_message(m["id"])



        response = self.client.as_sync().send_message(Response.build_message(None, content=msg, to=self.stream_id, subject="Log", msg_type="stream").response)
        if response["result"] != "success":
            return


class LogStream(PluginCommandMixin, Plugin):
    """
    Manage user groups.
    Alternative to Zulip user groups, as the bot does not have access to the api.
    """

    def _init_plugin(self) -> None:
        logstram = Conf.get("logstream")

        if logstram is None:
            return

        response = self.client.as_sync().get_streams()

        if response["result"] != "success":
            logging.warning("Could not get streams")
            return
        
        for stream in response["streams"]:
            if stream["name"] == logstram:
                # add handler
                logging.getLogger().addHandler(ZulipLogHandler(stream["stream_id"], self.client))
                return
        
        logging.warning(f"Stream {logstram} not found")



    @command
    async def test(
        self,
        sender,
        _session,
        args: CommandParser.Args,
        _opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[Response, None]:
        """
        Set the stream to log to.
        """
        text = await UserInput.short_text(self.client, sender, "What is your first name?")
        if text is None:
            yield Response.none()
            return
        
        yield DMResponse(f"Hello {text}!")