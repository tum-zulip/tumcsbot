#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import urllib.parse
from typing import Any, Iterable

from tumcsbot.lib import Response
from tumcsbot.plugin import PluginCommandMixin, PluginThread
from tumcsbot.plugin_decorators import *

class Search(PluginCommandMixin, PluginThread):
    syntax = "search <string>"
    description = 'Get a url to a search for "string" in all public streams.'
    msg_template: str = "Hi, I hope that these search results may help you: {}"
    path: str = "#narrow/streams/public/search/"

    @command
    @arg("string", str, description="The string to search for.", greedy=True)
    def search(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
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
        self.client.delete_message(message["id"])
        return Response.build_message(message, self.msg_template.format(url))
