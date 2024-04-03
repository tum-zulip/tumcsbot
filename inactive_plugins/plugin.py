#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
from typing import Any, Iterable

from tumcsbot.lib import Response
from tumcsbot.command_parser import CommandParser
from tumcsbot.plugin import Event, PluginCommandMixin,Plugin
from tumcsbot.plugin_decorators import *


class Plugin(PluginCommandMixin, Plugin):
    """
    Manage plugins.
    """

    @command
    @privilege(Privilege.ADMIN)
    @arg("plugin", str, description="The plugin to load.")
    async def load(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        load a plugin.
        """
        self.plugin_context.push_loopback(
            Event.start_event(self.plugin_name(), args.plugin)
        )
        yield Response.ok(message)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("plugin", str, description="The plugin to unload.")
    async def unload(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        unload a plugin.
        """
        self.plugin_context.push_loopback(
            Event.stop_event(self.plugin_name(), args.plugin)
        )
        yield Response.ok(message)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("plugin", str, description="The plugin to reload.")
    async def reload(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        reload a plugin.
        """
        self.plugin_context.push_loopback(
            Event.restart_event(self.plugin_name(), args.plugin)
        )
        yield Response.ok(message)
