#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
from typing import Any, Iterable

from sqlalchemy import text

from tumcsbot.lib.db import DB
from tumcsbot.lib.response import Response
from tumcsbot.lib.types import DMResponse, Privilege
from tumcsbot.plugin import PluginCommandMixin,Plugin
from tumcsbot.plugin_decorators import arg, command, privilege


class Source(PluginCommandMixin, Plugin):
    """
    Execute SQL commands in the bot's database.
    """

    @command
    @privilege(Privilege.ADMIN)
    @arg("sql", str, description="The SQL command to execute.", greedy=True)
    async def select(
        self,
        _sender: Any,
        session: DB,
        args: Any,
        _opts: Any,
        _message: dict[str, Any],
    ) -> Iterable[Response]:
        """
        Execute a SELECT SQL command in the bot's database.
        """
        sql = args.sql
        result = session.execute(text("SELECT " + " ".join(sql))).fetchall()
        yield DMResponse("```" + "\n".join(str(row) for row in result) + "```")
