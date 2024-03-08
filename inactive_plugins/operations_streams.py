#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
from shlex import quote
from typing import Any, Iterable
from tumcsbot.lib import split, Response
from tumcsbot.lib import (
    Regex,
    Response,
)
from tumcsbot.command_parser import CommandParser
from tumcsbot.db import DB
from tumcsbot.lib import split, Response
from tumcsbot.plugin import PluginCommandMixin, PluginThread
from tumcsbot.plugin_decorators import *

class OperationStreams(PluginCommandMixin, PluginThread):

    @command
    @privilege(Privilege.ADMIN)
    @arg()
    @opt()
    @arg()
    def archive_stream(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
          pass
  
    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "stream_tuples", 
        type=lambda t: split(t, sep=",", exact_split=2, discard_empty=False),
        description="List of (stream,description)-tuples",
        greedy=True)
    def create_stream(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
        failed: list[str] = []

        if args.stream_tuples is None or None in args.stream_tuples:
            return Response.error(message)

        for stream, desc in args.stream_tuples:
            if not stream:
                failed.append("one empty stream name")
                continue
            result: dict[str, Any] = self.client.add_subscriptions(
                streams=[{"name": stream, "description": desc}]
            )
            if result["result"] != "success":
                failed.append(f"stream: {stream}, description: {desc}")

        if not failed:
            return Response.ok(message)

        response: str = "Failed to create the following streams:\n" + "\n".join(failed)

        return Response.build_message(message, response, msg_type="private")

    
    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "stream_tuples",
        lambda t: split(t, sep=",", exact_split=2),
        description="list of (`stream_name_old`,`stream_name_new`)-tuples", 
        greedy=True
        )
    def rename_stream(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
        failed: list[str] = []

        if args.stream_tuples is None or None in args.stream_tuples:
            return Response.error(message)

        for old, new in args.stream_tuples:
            # Used for error messages.
            line: str = f"{old} -> {new}"

            try:
                old_id: int = self.client.get_stream_id(old)["stream_id"]
            except Exception as e:
                self.logger.exception(e)
                failed.append(line)
                continue

            result: dict[str, Any] = self.client.update_stream(
                {"stream_id": old_id, "new_name": f"'{new}'"}
            )
            if result["result"] != "success":
                failed.append(line)

        if not failed:
            yield Response.ok(message)

        yield DMResponse(
            "Failed to perform the following renamings:\n" + "\n".join(failed)
        )