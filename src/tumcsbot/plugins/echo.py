#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from time import sleep
import random
from typing import Any, Iterable

from tumcsbot.lib import Response
from tumcsbot.plugin import PluginCommandMixin, PluginThread
from tumcsbot.plugin_decorators import command, arg, opt, privilege, Privilege
from tumcsbot.command_parser import CommandParser


class Echo(PluginCommandMixin, PluginThread):

    def _init_plugin(self) -> None:
        pass
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("text", str, "The message text")
    def uppercase(self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Echo the message text in uppercase.
        """
        return Response.build_message(message, args.text.upper())
    
    @command
    @arg("text", str, "The message text")
    def lowercase(self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Echo the message text in lowercase.
        """
        return Response.build_message(message, args.text.lower())
    
    @command
    @arg("text", str, "The message text", greedy=True)
    def meme(self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Echo the message text in meme case.
        """
        text = " ".join(args.text)
        random_case = lambda c: c.upper() if bool(random.getrandbits(1)) else c.lower()
        return Response.build_message(message, "".join(random_case(c) for c in text))
    
    @command
    @arg("text", str, "The message text", greedy=True)
    @opt("d", long_opt="delay", type=int, description="The delay in seconds")
    def delay(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Echo the message text with a delay.
        """
        delay = opts.delay or 5
        sleep(delay)
        return Response.build_message(message, args.text)
