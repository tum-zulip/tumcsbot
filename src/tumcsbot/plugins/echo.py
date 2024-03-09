#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import asyncio
from time import sleep
import random
from typing import Any, Iterable

from tumcsbot.lib import Response
from tumcsbot.plugin import PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import *
from tumcsbot.command_parser import CommandParser


class Echo(PluginCommandMixin, Plugin):
    """
    Respond to messages by echoing them back.
    """
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("text", str, "The message text")
    @arg("user", ZulipUser, "The user to echo the message to")
    @opt("n", long_opt="number", type=int, description="The number of times to echo the message")
    def uppercase(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Echo the message text in uppercase. If the `number` option is given, echo the message multiple times.
        """
        for _ in range(opts.n or 1):
            yield DMMessage(args.user, args.text.upper())

    
    @command
    @arg("text", str, "The message text")
    def lowercase(self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Echo the message text in lowercase.
        """
        # await Confirmation(f"Are you sure you want me to lowercase *{args.text}*?")
        
        yield InlineResponse(args.text.lower())
    
    @command
    @arg("text", str, "The message text", greedy=True)
    def meme(self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Echo the message text in meme case.
        """
        text = " ".join(args.text)
        random_case = lambda c: c.upper() if bool(random.getrandbits(1)) else c.lower()
        return InlineResponse("".join(random_case(c) for c in text))
    
    @command
    @arg("text", str, "The message text", greedy=True)
    @opt("d", long_opt="delay", type=int, description="The delay in seconds")
    async def delay(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ):
        """
        Echo the message text with a delay.
        """
        delay = opts.d or 5
        await asyncio.sleep(delay)
        yield DMResponse(args.text)
