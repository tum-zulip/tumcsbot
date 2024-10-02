#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any, AsyncGenerator

from sqlalchemy import text

from tumcsbot.lib.db import DB
from tumcsbot.lib.types import DMResponse, Privilege, response_type
from tumcsbot.plugin import PluginCommand, Plugin
from tumcsbot.plugin_decorators import arg, command, privilege


class Source(PluginCommand, Plugin):
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
    ) -> AsyncGenerator[response_type, None]:
        """
        Execute a SELECT SQL command in the bot's database.
        """
        sql = args.sql
        result = session.execute(text("SELECT " + " ".join(sql))).fetchall() # type: ignore
        yield DMResponse("```" + "\n".join(str(row) for row in result) + "```")
