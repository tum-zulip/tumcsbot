#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
from typing import Any, Iterable
from sqlalchemy import Column, String

from tumcsbot.lib import Response
from tumcsbot.command_parser import CommandParser
from tumcsbot.db import DB, TableBase
from tumcsbot.plugin import PluginCommandMixin, PluginThread
from tumcsbot.plugin_decorators import *

class Messages(TableBase):
    __tablename__ = "Messages"

    MsgId = Column(String, primary_key=True)
    MsgText = Column(String, nullable=False)

class Msg(PluginCommandMixin, PluginThread):
    syntax = cleandoc(
        """
        msg add <identifier> <text>
          or msg send|remove <identifier>
          or msg list
        """
    )
    description = cleandoc(
        """
        Store a message for later use, send or delete a stored message \
        or list all stored messages. The text must be quoted but may
        contain line breaks.
        The identifiers are handled case insensitively.
        [administrator/moderator rights needed]
        """
    )

    @command
    @privilege(Privilege.ADMIN)
    @arg("id", str, description="Identifier of the message")
    @arg("text", str, greedy=True, description="The formated message that should be stored. Must be quoted but may contain line breaks")
    def add(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        ident = args.id.lower()
        with DB.session() as session:
            session.add(Messages(MsgId=ident, MsgText=args.text))
        return Response.ok(message)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("id", str, description="Identifier of the message")
    def remove(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        ident = args.id.lower()
        with DB.session() as session:
            session.query(Messages).filter(Messages.MsgId == ident).delete()
        return Response.ok(message)
    
    @command(name="list")
    @privilege(Privilege.ADMIN)
    def _list(self, message: dict[str, Any], _: CommandParser.Args, __: CommandParser.Opts) -> Response | Iterable[Response]:
        response: str = "***List of Identifiers and Messages***\n"
        with DB.session() as session:
            for msg in session.query(Messages).all():
                response += f"\n--------\nTitle: **{msg.MsgId}**\n{msg.MsgText}"
        return Response.build_message(message, response)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("id", str, description="Identifier of the message")
    def send(self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts) -> Response | Iterable[Response]:
        ident = args.id.lower()
        with DB.session() as session:
            msg = session.query(Messages).filter(Messages.MsgId == ident).first()
            if msg is None:
                return Response.command_not_found(message)
            
            self.client.delete_message(message["id"])
            return Response.build_message(message, msg.MsgText)
