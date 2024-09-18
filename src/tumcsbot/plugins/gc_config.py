#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import asyncio
import logging
from typing import Any, Iterable, AsyncGenerator

from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.conf import Conf
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import (
    DMResponse,
    PartialError,
    PartialSuccess,
    Privilege,
    ZulipChannel,
    ZulipUser,
    response_type,
)
from tumcsbot.plugin import Event, Plugin, PluginCommandMixin
from tumcsbot.plugin_decorators import arg, command, opt, privilege

from tumcsbot.plugins.garbage_collector import GarbageCollectorIgnoreChannelsTable
from tumcsbot.plugins.userinput import UserInput


class GCConfig(PluginCommandMixin, Plugin):
    """
    Manage configuration variables for the garbage collector.
    """

    @command
    @privilege(Privilege.ADMIN)
    @opt(
        "s",
        "seconds",
        int,
        description="The number of seconds a channel has to be inactive before it is considered for deletion.",
    )
    @opt(
        "m",
        "minutes",
        int,
        description="The number of minutes a channel has to be inactive before it is considered for deletion.",
    )
    @opt(
        "h",
        "hours",
        int,
        description="The number of hours a channel has to be inactive before it is considered for deletion.",
    )
    @opt(
        "d",
        "days",
        int,
        description="The number of days a channel has to be inactive before it is considered for deletion.",
    )
    async def threshold(
        self,
        _sender: ZulipUser,
        _session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> Iterable[Response]:
        """
        Set the number of seconds a channel has to be inactive before it is considered for deletion.
        """
        seconds = opts.seconds or 0
        minutes = opts.minutes or 0
        hours = opts.hours or 0
        days = opts.days or 0

        threshhold = (
            seconds + (minutes * 60) + (hours * 60 * 60) + (days * 24 * 60 * 60)
        )
        Conf.set("garbage_collector_no_activity_threshold_seconds", str(threshhold))

        yield DMResponse(f"No activity threshold set to {threshhold} seconds.")

    @command
    @privilege(Privilege.ADMIN)
    @opt(
        "s",
        "seconds",
        int,
        description="The number of seconds the bot waits for a response from the channel admins.",
    )
    @opt(
        "m",
        "minutes",
        int,
        description="The number of minutes the bot waits for a response from the channel admins.",
    )
    @opt(
        "h",
        "hours",
        int,
        description="The number of hours the bot waits for a response from the channel admins.",
    )
    @opt(
        "d",
        "days",
        int,
        description="The number of days the bot waits for a response from the channel admins.",
    )
    async def confirmation_time(
        self,
        _sender: ZulipUser,
        _session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> Iterable[Response]:
        """
        Set the number of seconds the bot waits for a response from the channel admins.
        """
        seconds = opts.seconds or 0
        minutes = opts.minutes or 0
        hours = opts.hours or 0
        days = opts.days or 0

        time_to_responde = (
            seconds + (minutes * 60) + (hours * 60 * 60) + (days * 24 * 60 * 60)
        )
        Conf.set("garbage_collector_time_to_responde_seconds", str(time_to_responde))

        yield DMResponse(f"Time to responde set to {time_to_responde} seconds.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "channels",
        ZulipChannel,
        description="The channels to ignore by the garbage collector",
        greedy=True,
    )
    async def ignore(
        self,
        _sender: ZulipUser,
        session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Ignore channels by the garbage collector.
        """
        channels = args.channels

        already_ignored = session.query(GarbageCollectorIgnoreChannelsTable).all()
        already_ignored = [s.Channel.id for s in already_ignored]
        try:
            for channel in channels:
                if channel.id in already_ignored:
                    yield PartialError(f"{channel.mention} is already ignored.")
                    continue

                session.add(GarbageCollectorIgnoreChannelsTable(Channel=channel))
                yield PartialSuccess(f"{channel.mention} is now ignored.")

            session.commit()
        except Exception as e:
            session.rollback()
            logging.exception(e)
            yield DMResponse(f"Error: {e}")
            return

        for s in channels:
            await s

