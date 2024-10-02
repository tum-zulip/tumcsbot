#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any, AsyncGenerator

from tumcsbot.lib.db import Session
from tumcsbot.lib.conf import Conf
from tumcsbot.plugin import PluginCommand, Plugin
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.types import (
    response_type,
    ZulipUser,
    DMResponse,
    Privilege,
    UserNotPrivilegedException,
)
from tumcsbot.plugin_decorators import (
    command,
    privilege,
    arg,
)

class ConfPlugin(PluginCommand, Plugin):
    """
    Manage configuration variables.
    """

    @command(name="list")
    @privilege(Privilege.ADMIN)
    async def _list(
        self,
        _sender: ZulipUser,
        _session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List all configuration variables.
        """
        response: str = "Key | Value\n ---- | ----"
        for key, value in Conf.list():
            response += f"\n{key} | {value}"
        yield DMResponse(response)

    @command
    @privilege(Privilege.ADMIN)
    @arg("key", str, description="The key of the configuration variable")
    @arg("value", str, description="The value of the configuration variable.")
    async def set(
        self,
        sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Set a configuration variable.
        """
        if not Conf.is_bot_owner(sender.id):
            raise UserNotPrivilegedException(
                "You must be the bot owner to set configuration variables."
            )
        Conf.set(args.key, args.value)
        yield DMResponse(f"Configuration variable '{args.key}' set to '{args.value}'.")

    @command
    @privilege(Privilege.ADMIN)
    @arg("key", str, description="The key of the configuration variable")
    async def remove(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Remove a configuration variable.
        """
        Conf.remove(args.key)
        yield DMResponse(f"Configuration variable '{args.key}' removed.")
