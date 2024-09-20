#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any, AsyncGenerator

from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.utils import split
from tumcsbot.plugin import PluginCommand, Plugin
from tumcsbot.plugin_decorators import command, arg, privilege, opt
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.db import Session
from tumcsbot.lib.types import (
    Privilege,
    PartialError,
    PartialSuccess,
    ZulipUser,
    DMResponse,
    DMError,
    response_type,
)


class Channels(PluginCommand, Plugin):

    @command(name="list")
    @arg("pattern", str, description="The pattern to search for.")
    async def _list(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Search for channels that match the given pattern.
        """

        result: list[str] = await self.client.get_channels_from_regex(args.pattern)

        if not result:
            yield DMResponse("No matches found.")
        else:
            yield DMResponse(", ".join([f"#**{s}**" for s in result]))

    @command
    @privilege(Privilege.ADMIN)
    @opt(
        "r",
        description="select the channels according to the given regular expressions",
        priv=Privilege.ADMIN,
    )
    @arg(
        "channel_names",
        ty=str,
        description="Channels that should be archived",
        greedy=True,
    )
    async def archive(
        self,
        sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Archive the given channels.
        The list of channels is interpreted in a way that autocompleted
        channel names (à la `#**channel name**`) are auto-detected.
        If the `-r` option is present, select the channels according to the
        given regular expressions, which have to match the full channel name.
        Note that only empty channels will be archived.
        """

        if opts.r:
            channels = await sender.client.get_channels_from_regex(args.channel_names)
        else:
            channels = args.channel_names

        for s in channels:
            channel: str | None = Regex.get_channel_name(s)
            if channel is None:
                yield PartialError(f"error: {s} cannot be parsed")
                continue

            result: dict[str, Any] = await sender.client.get_channel_id(channel)

            if result["result"] != "success":
                yield PartialError(result["msg"])
                continue

            channel_id: int = result["stream_id"]

            # Check if channel is empty.
            result = await sender.client.get_messages(
                {
                    "anchor": "oldest",
                    "num_before": 0,
                    "num_after": 1,
                    "narrow": [{"operator": "stream", "operand": channel_id}],
                }
            )
            if result["result"] != "success":
                yield PartialError(result["msg"])
                continue

            if len(result["messages"]) > 0:
                yield PartialError(f"Channel {channel} is not empty.")
                continue

            result = await sender.client.delete_channel(channel_id)
            if result["result"] != "success":
                yield PartialError(result["msg"])
            yield PartialSuccess(f"Channel {channel} archived.")

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the channel to create.")
    @arg(
        "description",
        str,
        description="The description of the channel to create.",
        optional=True,
        greedy=True,
    )
    async def create(
        self,
        sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        todo: this is wrong documentation
        Create a public channel for every (channel,description)-tuple \
        passed to this command. You may provide a quoted empty string \
        as description.
        The (channel name, channel description)-tuples may be separated \
        by any whitespace.

        Notes:
        - It is not yet possible to have single-quotes (`'`) in channel \
        names or descriptions.
        """

        result: dict[str, Any] = await sender.client.add_subscriptions(
            channels=[{"name": args.name, "description": " ".join(args.description)}]
        )
        if result["result"] != "success":
            raise DMError(result["msg"])
        yield DMResponse(f"Channel {args.name} created.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "channel_tuples",
        lambda t: split(t, sep=",", exact_split=2),
        description="list of (`channel_name_old`,`channel_name_new`)-tuples",
        greedy=True,
    )
    async def rename(
        self,
        sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:

        for old, new in args.channel_tuples:
            # Used for error messages.
            line: str = f"{old} -> {new}"

            try:
                old_chan: dict[str, Any] = await sender.client.get_channel_id(old)
                old_id: int = old_chan["stream_id"]
            except Exception as e:
                self.logger.exception(e)
                yield PartialError(f"Failed to get channel id for {old}")
                continue

            result: dict[str, Any] = await sender.client.update_channel(
                {"stream_id": old_id, "new_name": f"'{new}'"}
            )
            if result["result"] != "success":
                yield PartialError(result["msg"])
                continue
            yield PartialSuccess(f"Renamed {line}")
