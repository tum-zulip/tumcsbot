#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import urllib.parse
from typing import Any, AsyncGenerator
from tumcsbot.lib.db import Session

from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.plugin import PluginCommandMixin, Plugin, ZulipUser
from tumcsbot.plugin_decorators import command, arg
from tumcsbot.lib.types import response_type, ZulipUser, DMResponse


class Search(PluginCommandMixin, Plugin):

    syntax = "search <string>"
    msg_template: str = "Hi, I hope that these search results may help you: {}"
    path: str = "#narrow/streams/public/search/"

    @command
    @arg("search_string", str, description="The string to search for.", greedy=True)
    async def search(
        self,
        _sender: ZulipUser,
        _session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Get a url to a search for "string" in all public streams.
        """
        # todo: use argument instead of urrlib.parse.quote
        # Get search string and quote it.
        search: str = urllib.parse.quote(message["command"], safe="")
        # Fix strange behavior of Zulip which does not accept literal periods.
        search = search.replace(".", "%2E")
        # Get host url (removing trailing 'api/').
        base_url: str = self.client.base_url[:-4]
        # Build the full url.
        url: str = base_url + self.path + search
        # Remove requesting message.
        await self.client.delete_message(message["id"])
        yield DMResponse(self.msg_template.format(url))
    