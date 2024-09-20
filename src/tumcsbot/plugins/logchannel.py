#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import logging

from tumcsbot.lib.types import ZulipChannel
from tumcsbot.lib.client import AsyncClient
from tumcsbot.lib.conf import Conf
from tumcsbot.lib.db import DB
from tumcsbot.lib.response import Response
from tumcsbot.plugin import PluginCommand, Plugin
from tumcsbot.plugins.garbage_collector import GarbageCollectorIgnoreChannelsTable


class ZulipLogHandler(logging.Handler):
    """
    A handler class which sends
    log records to a Zulip channel.
    """

    def __init__(
        self, channel_id: int, client: AsyncClient, log_level: int = logging.INFO
    ) -> None:
        logging.Handler.__init__(self)
        self.channel_id = channel_id
        self.client = client
        self.setLevel(log_level)

    def emit(self, record: logging.LogRecord) -> None:
        """
        Emit a record.
        """
        msg = self.format(record)

        response = self.client.as_sync().get_messages(
            {
                "anchor": "newest",
                "num_before": 100,
                "num_after": 0,
                "narrow": [
                    {"operator": "sender", "operand": self.client.id},
                    {"operator": "channel", "operand": self.channel_id},
                ],
            }
        )
        if response["result"] == "success":
            # delete old messages sent from this bot
            msgs = [m for m in response["messages"] if m["sender_id"] == self.client.id]
            msgs = msgs[: len(msgs) - 100]
            for m in msgs:
                self.client.as_sync().delete_message(m["id"])

        if "Traceback" in msg:
            msg = "```python\n" + msg + "\n```\n"

        response = self.client.as_sync().send_message(
            Response.build_message(
                None, content=msg, to=self.channel_id, subject="Log", msg_type="channel"
            ).response
        )
        if response["result"] != "success":
            return


class LogChannel(PluginCommand, Plugin):
    """
    Send log messages to a Zulip channel.
    """

    def _init_plugin(self) -> None:
        logstram = Conf.get("logchannel")

        if logstram is None:
            return

        principals = [self.client.id]
        owner = Conf.get("bot_owner")
        if owner is not None:
            principals.append(int(owner))
        else:
            logging.warning("Bot owner not set")

        response = self.client.as_sync().add_subscriptions(
            streams=[
                {
                    "name": logstram,
                    "description": "Log channel of TUM CS Bot",
                }
            ],
            principals=principals,
            invite_only=True,
        )

        if response["result"] != "success":
            logging.warning("Could not add subscribers to %s", logstram)
            return

        response = self.client.as_sync().get_streams()

        if response["result"] != "success":
            logging.error("Could not get channels")
            return

        channel_filter = filter(lambda x: x["name"] == logstram, response["streams"])

        if channel_filter:
            channel = next(channel_filter)

            with DB.session() as session:
                channel = ZulipChannel(channel["stream_id"])
                if (
                    session.query(GarbageCollectorIgnoreChannelsTable)
                    .filter_by(Channel=channel)
                    .first()
                    is None
                ):
                    session.add(GarbageCollectorIgnoreChannelsTable(Channel=channel))
                session.commit()

            logging.getLogger().addHandler(ZulipLogHandler(channel.id, self.client))
        else:
            logging.error("Channel %s not found", logstram)
