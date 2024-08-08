#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import logging
from tumcsbot.lib.client import AsyncClient
from tumcsbot.lib.conf import Conf
from tumcsbot.lib.response import Response
from tumcsbot.plugin import PluginCommandMixin, Plugin


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
    Send log messages to a Zulip stream.
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
