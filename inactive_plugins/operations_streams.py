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
    @opt(
        "r",
        description="select the streams according to the given regular expressions",
        privilege=Privilege.ADMIN,
        )
    @arg(
        "stream_names",
        type=str,
        description="Streams that should be archived", 
        greedy=True
        )
    async def archive_stream(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
       
        sender_name = await sender.name

        if args.stream_names is None or None in args.stream_names:
            return DMResponse(f"Sorry {sender_name}, no streams found.")
        
        if opts.r:
            # Get the list of streams we would delete, build a new command
            # without regexes that would accomplish this, and ask the user
            # whether they would like to execute it like that.
            streams = await sender.client.get_streams_from_regex(args.stream_names)
            return Response.build_request_msg(
                message, f"{self.plugin_name()} {' '.join(map(quote, streams))}"
            )
        else:
            streams = []
            for stream in args.stream_names:
                stream_s: str | None = Regex.get_stream_name(stream)
                if stream_s is None:
                    return Response.build_message(
                        message, f"error: {stream} cannot be parsed"
                    )
                streams.append(stream_s)
            return self._archive_streams(message, sender, args.stream_names)
        

    async def _archive_streams(
            self, sender:ZulipUser, message: dict[str, Any], streams: list[str]
        ):
            failed: list[str] = []

            for stream in streams:
                result: dict[str, Any] = await sender.client.get_stream_id(stream)
                if result["result"] != "success":
                    failed.append(stream)
                    continue
                stream_id: int = result["stream_id"]

                # Check if stream is empty.
                result = await sender.client.get_messages(
                    {
                        "anchor": "oldest",
                        "num_before": 0,
                        "num_after": 1,
                        "narrow": [{"operator": "stream", "operand": stream_id}],
                    }
                )
                if result["result"] != "success" or result["messages"]:
                    failed.append(stream)
                    continue

                # Archive the stream: https://zulip.com/help/archive-a-stream
                result = await sender.client.delete_stream(stream_id)
                if result["result"] != "success":
                    failed.append(stream)

            if not failed:
                return Response.ok(message)
            
            yield DMResponse(
               f"Failed to remove the following stream(s): {failed}"
            )




  
    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "stream_tuples", 
        type=lambda t: split(t, sep=",", exact_split=2, discard_empty=False),
        description="List of (stream,description)-tuples",
        greedy=True
        )
    async def create_stream(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ):
        failed: list[str] = []

        sender_name = await sender.name

        if args.stream_tuples is None or None in args.stream_tuples:
            yield DMResponse(f"Sorry {sender_name}, no streams found.")

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

        yield DMResponse(
            "Failed to create the following streams:\n" + "\n".join(failed)
        )

    
    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "stream_tuples",
        lambda t: split(t, sep=",", exact_split=2),
        description="list of (`stream_name_old`,`stream_name_new`)-tuples", 
        greedy=True
        )
    async def rename_stream(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ):
        failed: list[str] = []

        sender_name = await sender.name

        if args.stream_tuples is None or None in args.stream_tuples:
            yield DMResponse(f"Sorry {sender_name}, no streams found.")

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

        @command
        @privilege(Privilege.USER)
        @arg(
             "stream_names",
             str,
             description="Streams that should be marked as read", 
             greedy=True
        )
        async def mark_as_read( 
            self,
            sender: ZulipUser,
            session,
            args: CommandParser.Args,
            opts: CommandParser.Opts,
            message: dict[str, Any],
        ):
            failed: list[str] = []

            sender_name = await sender.name

            if args.stream_names is None or None in args.stream_names:
                yield DMResponse(f"Sorry {sender_name}, no streams found.")

            for stream in args.stream_names:
                if not stream:
                    raise PartialError("empty stream name")
                stream_id = await sender.client.get_stream_id(stream)
                result = await sender.client.mark_stream_as_read(stream_id)
                if result["result"] != "success":
                    failed.append(f"stream: {stream}")

            if not failed:
                return Response.ok(message)

            yield DMResponse(
                "Failed to create the following streams:\n" + "\n".join(failed)
            ) 