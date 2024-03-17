#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any
from tumcsbot.command_parser import CommandParser
from tumcsbot.lib import split
from tumcsbot.plugin import PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import *

class Streams(PluginCommandMixin, Plugin):

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
    async def archive(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ):
        
        if opts.r:
            streams = await sender.client.get_streams_from_regex(args.stream_names)
        else:
            streams = args.stream_names
        

        for stream in streams:
            result: dict[str, Any] = await sender.client.get_stream_id(stream)

            if result["result"] != "success":
                yield PartialError(result["msg"])
            
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
            if result["result"] != "success":
                yield PartialError(result["msg"])
                continue

            if len(result["messages"]) > 0:
                yield PartialError("Stream is not empty.")
            
            result = await sender.client.delete_stream(stream_id)
            if result["result"] != "success":
                yield PartialError(result["msg"])

  
    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "stream_tuples", 
        type=lambda t: split(t, sep=",", exact_split=2, discard_empty=False),
        description="List of (stream,description)-tuples",
        greedy=True
        )
    async def create(
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
                yield PartialError("empty stream name")
                continue
            
            result: dict[str, Any] = await sender.client.add_subscriptions(
                streams=[{"name": stream, "description": desc}]
            )

            if result["result"] != "success":
                yield PartialError(result["msg"])
                continue

            yield PartialSuccess(f"Stream {stream} created.")

    
    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "stream_tuples",
        lambda t: split(t, sep=",", exact_split=2),
        description="list of (`stream_name_old`,`stream_name_new`)-tuples", 
        greedy=True
        )
    async def rename(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ):

        for old, new in args.stream_tuples:
            # Used for error messages.
            line: str = f"{old} -> {new}"

            try:
                old_id: int = await sender.client.get_stream_id(old)["stream_id"]
            except Exception as e:
                self.logger.exception(e)
                yield PartialError(f"Failed to get stream id for {old}")
                continue

            result: dict[str, Any] = await sender.client.update_stream(
                {"stream_id": old_id, "new_name": f"'{new}'"}
            )
            if result["result"] != "success":
                yield PartialError(result["msg"])
                continue
            yield PartialSuccess(f"Renamed {line}")

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
        for stream in args.stream_names:           
            result: dict[str, Any] = await sender.client.get_stream_id(stream)

            if result["result"] != "success":
               yield PartialError(result["msg"])

            stream_id: int = result["stream_id"]

            result = await sender.client.mark_stream_as_read(stream_id)

            if result["result"] != "success":
                yield PartialError(result["msg"])
        