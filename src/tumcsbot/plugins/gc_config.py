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
    ZulipStream,
    ZulipUser,
    response_type,
)
from tumcsbot.plugin import Event, Plugin, PluginCommandMixin
from tumcsbot.plugin_decorators import arg, command, opt, privilege

from tumcsbot.plugins.garbage_collector import GarbageCollectorIgnoreStreamsTable
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
        _sender: ZulipUser,
        _session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
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
        _sender: ZulipUser,
        _session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
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

    @command
    @arg(
        "streams",
        ZulipStream,
        description="The streams to ignore by the garbage collector",
        greedy=True,
    )
    async def ignore(
        self,
        _sender: ZulipUser,
        session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> Iterable[Response]:
        """
        Ignore streams by the garbage collector.
        """
        streams = args.streams

        already_ignored = session.query(GarbageCollectorIgnoreStreamsTable).all()
        already_ignored = [s.Stream.id for s in already_ignored]
        try:
            for stream in streams:
                if stream.id in already_ignored:
                    yield PartialError(f"{stream.mention} is already ignored.")
                    continue

                session.add(GarbageCollectorIgnoreStreamsTable(Stream=stream))
                yield PartialSuccess(f"{stream.mention} is now ignored.")

            session.commit()
        except Exception as e:
            session.rollback()
            logging.exception(e)
            yield DMResponse(f"Error: {e}")
            return

        for s in streams:
            await s

    @command
    async def test(
        self,
        sender: ZulipUser,
        _session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        response1 = await self.client.send_response(
            Response.build_message(message, "Test successful.")
        )

        response2 = await self.client.send_response(
            Response.build_message(
                None, "Test successful.", to=[sender.id, 10], msg_type="private"
            )
        )

        coro1 = UserInput.choose(self.client, response1["id"], ["check", "cross_mark"])
        coro2 = UserInput.choose(self.client, response2["id"], ["check", "cross_mark"])

        res = await asyncio.gather(coro1, coro2)

        emote1, msg1 = res[0]
        emote2, msg2 = res[1]

        yield DMResponse(f"Test successful. {emote1} {emote2}")
        yield DMResponse(f"{msg1}")
        yield DMResponse(f"{msg2}")
