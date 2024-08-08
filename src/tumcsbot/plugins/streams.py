#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any

from zulip import ZulipStream
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.utils import split
from tumcsbot.plugin import PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import command, arg, privilege, opt
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.types import Privilege, PartialError, PartialSuccess, ZulipUser, DMResponse, DMError  


class Streams(PluginCommandMixin, Plugin):

    @command(name="list")
    @arg("pattern", str, description="The pattern to search for.")
    async def _list(
        self,
        sender: ZulipUser,
        _session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ):
        """
        Search for streams that match the given pattern.
        """
    
        result: list[str] = await self.client.get_streams_from_regex(args.pattern)
 
        if not result:
            yield DMResponse("No matches found.")
        else:
            yield DMResponse(", ".join([f"#**{s}**" for s in result]))

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
        greedy=True,
    )
    async def archive(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ):
        """
        Archive the given streams.
        The list of streams is interpreted in a way that autocompleted
        stream names (à la `#**stream name**`) are auto-detected.
        If the `-r` option is present, select the streams according to the
        given regular expressions, which have to match the full stream name.
        Note that only empty streams will be archived.
        """

        if opts.r:
            streams = await sender.client.get_streams_from_regex(args.stream_names)
        else:
            streams = args.stream_names

        for s in streams:
            stream: str | None = Regex.get_stream_name(s)
            if stream is None:
                yield PartialError(f"error: {s} cannot be parsed")
                continue

            result: dict[str, Any] = await sender.client.get_stream_id(stream)

            if result["result"] != "success":
                yield PartialError(result["msg"])
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
            if result["result"] != "success":
                yield PartialError(result["msg"])
                continue

            if len(result["messages"]) > 0:
                yield PartialError(f"Stream {stream} is not empty.")
                continue

            result = await sender.client.delete_stream(stream_id)
            if result["result"] != "success":
                yield PartialError(result["msg"])
            yield PartialSuccess(f"Stream {stream} archived.")

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the stream to create.")
    @arg(
        "description",
        str,
        description="The description of the stream to create.",
        optional=True,
        greedy=True,
    )
    async def create(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ):
        """
        todo: this is wrong documentation
        Create a public stream for every (stream,description)-tuple \
        passed to this command. You may provide a quoted empty string \
        as description.
        The (stream name, stream description)-tuples may be separated \
        by any whitespace.

        Notes:
        - It is not yet possible to have single-quotes (`'`) in stream \
        names or descriptions.
        """

        result: dict[str, Any] = await sender.client.add_subscriptions(
            streams=[{"name": args.name, "description": " ".join(args.description)}]
        )
        if result["result"] != "success":
            raise DMError(result["msg"])
        yield DMResponse(f"Stream {args.name} created.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "stream_tuples",
        lambda t: split(t, sep=",", exact_split=2),
        description="list of (`stream_name_old`,`stream_name_new`)-tuples",
        greedy=True,
    )
    async def rename(
        self,
        sender: ZulipUser,
        _session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
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

