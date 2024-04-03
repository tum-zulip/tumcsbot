#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import Any, AsyncGenerator
from sqlalchemy import Column, String
import sqlalchemy

from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import Session, TableBase
from tumcsbot.plugin import PluginCommandMixin, Plugin, ZulipUser
from tumcsbot.plugin_decorators import (
    command,
    privilege,
    arg,
    response_type,
    DMResponse,
    DMError,
)
from tumcsbot.lib.types import Privilege, response_type, ZulipUser


class Messages(TableBase):
    __tablename__ = "Messages"

    MsgId = Column(String, primary_key=True)
    MsgText = Column(String, nullable=False)


class Msg(PluginCommandMixin, Plugin):
    """
    Store a message for later use, send or delete a stored message \
    or list all stored messages. The text must be quoted but may
    contain line breaks.
    The identifiers are handled case insensitively.
    """

    @command
    @privilege(Privilege.ADMIN)
    @arg("id", str, description="Identifier of the message")
    @arg(
        "text",
        str,
        greedy=True,
        description="The formated message that should be stored. Must be quoted but may contain line breaks",
    )
    async def add(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add a message to the database.
        """
        ident = args.id.lower()
        try:
            session.add(Messages(MsgId=ident, MsgText=args.text))
            session.commit()
        except sqlalchemy.exc.IntegrityError:
            raise DMError(f"Identifier {ident} already exists.")
        yield DMResponse(f"Message with identifier {ident} added.")

    @command
    @privilege(Privilege.ADMIN)
    @arg("id", str, description="Identifier of the message")
    async def remove(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Remove a message from the database.
        """
        ident = args.id.lower()
        try:
            session.query(Messages).filter(Messages.MsgId == ident).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError:
            raise DMError(f"No message with identifier {ident} found.")

        yield DMResponse(f"Message with identifier {ident} removed.")

    @command(name="list")
    @privilege(Privilege.ADMIN)
    async def _list(
        self,
        _sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """ "
        List all stored messages.
        """
        response: str = "***List of Identifiers and Messages***\n"
        for msg in session.query(Messages).all():
            response += f"\n--------\nTitle: **{msg.MsgId}**\n{msg.MsgText}"
        yield DMResponse(response)

    @command
    @privilege(Privilege.ADMIN)
    @arg("id", str, description="Identifier of the message")
    async def send(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Delete the command message and send the stored message.
        """
        ident = args.id.lower()

        msg = session.query(Messages).filter(Messages.MsgId == ident).first()
        if msg is None:
            raise DMError(f"No message with identifier {ident} found.")

        await self.client.delete_message(message["id"])
        yield DMResponse(str(msg.MsgText))
