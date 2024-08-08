#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any, Iterable

from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.conf import Conf
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import DMResponse, Privilege, ZulipUser
from tumcsbot.plugin import Event, Plugin, PluginCommandMixin
from tumcsbot.plugin_decorators import arg, command, opt, privilege


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
        description="The number of seconds a stream has to be inactive before it is considered for deletion.",
    )
    @opt(
        "m",
        "minutes",
        int,
        description="The number of minutes a stream has to be inactive before it is considered for deletion.",
    )
    @opt(
        "h",
        "hours",
        int,
        description="The number of hours a stream has to be inactive before it is considered for deletion.",
    )
    @opt(
        "d",
        "days",
        int,
        description="The number of days a stream has to be inactive before it is considered for deletion.",
    )
    async def threshold(
        self,
        sender: ZulipUser,
        _session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Iterable[Response]:
        """
        Set the number of seconds a stream has to be inactive before it is considered for deletion.
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
        description="The number of seconds the bot waits for a response from the stream admins.",
    )
    @opt(
        "m",
        "minutes",
        int,
        description="The number of minutes the bot waits for a response from the stream admins.",
    )
    @opt(
        "h",
        "hours",
        int,
        description="The number of hours the bot waits for a response from the stream admins.",
    )
    @opt(
        "d",
        "days",
        int,
        description="The number of days the bot waits for a response from the stream admins.",
    )
    async def confirmation_time(
        self,
        sender: ZulipUser,
        _session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Iterable[Response]:
        """
        Set the number of seconds the bot waits for a response from the stream admins.
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
