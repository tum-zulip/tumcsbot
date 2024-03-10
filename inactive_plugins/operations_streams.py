#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

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
    ):
       
        sender_name = await sender.name

        if args.stream_names is None or None in args.stream_names:
            return DMResponse(f"Sorry {sender_name}, no streams found.")
        
        if opts.r:
            streams = await sender.client.get_streams_from_regex(args.stream_names)
        else:
            streams = args.stream_names
        
        failed: list[str] = []
        for stream in streams:
            result: dict[str, Any] = await sender.client.get_stream_id(stream)
            if result["result"] != "success":
                failed.append(stream)
                raise PartialError()
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
                raise PartialError()
            
            result = await sender.client.delete_stream(stream_id)
            if result["result"] != "success":
                failed.append(stream)
                raise PartialError()

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
                failed.append("empty stream name")
                raise PartialError("empty stream name")
            result: dict[str, Any] = sender.client.add_subscriptions(
                streams=[{"name": stream, "description": desc}]
            )
            if result["result"] != "success":
                failed.append(f"stream: {stream}, description: {desc}")
                raise PartialError()

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
                old_id: int = await sender.client.get_stream_id(old)["stream_id"]
            except Exception as e:
                self.logger.exception(e)
                failed.append(line)
                continue

            # todo: update_stream async?
            result: dict[str, Any] = await sender.client.update_stream(
                {"stream_id": old_id, "new_name": f"'{new}'"}
            )
            if result["result"] != "success":
                failed.append(line)
                raise PartialError()

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
                    failed.append("empty stream name")
                    raise PartialError("empty stream name")
                
                result_id: dict[str, Any] = await sender.client.get_stream_id(stream)
                if result_id["result"] != "success":
                   failed.append(stream)
                   raise PartialError()
                stream_id: int = result_id["stream_id"]

                result = await sender.client.mark_stream_as_read(stream_id)
                if result["result"] != "success":
                    failed.append(f"stream: {stream}")
                    raise PartialError()

            if not failed:
                return Response.ok(message)

            yield DMResponse(
                "Failed to create the following streams:\n" + "\n".join(failed)
            ) 