#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
from typing import Any, Iterable

from tumcsbot.lib import Response, get_classes_from_path
from tumcsbot.plugin import PluginCommandMixin, _Plugin, PluginThread, PluginTable, Privilege
from tumcsbot.db import DB

HELP_TEMPLATE = cleandoc(
    """
    Hi {}!

    Use `help <command name>` to get more information about a certain command.
    Please consider that my command line parsing is comparable to the POSIX shell. \
    So in order to preserve arguments containing whitespace characters from splitting, they need to be quoted. \
    Special strings such as regexes containing backslash sequences may require single quotes instead of double quotes.

    Currently, I understand the following commands:

    {}

    Have a nice day! :-)
    """
)


class Help(PluginCommandMixin, PluginThread):
    """Post a help message to the requesting user."""

    # This plugin depends on all the others because it needs their db entries.
    dependencies = PluginCommandMixin.dependencies + [
        plugin_class.plugin_name()
        for plugin_class in get_classes_from_path("tumcsbot.plugins", _Plugin)  # type: ignore
    ]

    def handle_message(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        command: str = message["command"].strip()
        if not command:
            return self._help_overview(message)
        return self._help_command(message, command)

    @staticmethod
    def _format_description(description: str) -> str:
        """Format the usage description of a command."""
        # Remove surrounding whitespace.
        description.strip()
        return description

    @staticmethod
    def _format_syntax(syntax: str) -> str:
        """Format the syntax string of a command."""
        return "```text\n" + syntax.strip() + "\n```\n"
    
    @staticmethod
    def _get_help_info(command: str | None = None, privilige: Privilege = Privilege.USER) -> list[tuple[str, str, str]]:
        """Get help information from each command.

        Return a list of tuples (command name, syntax, description).
        """
        # todo: privilage level
        with DB.session() as session:
            if command is not None:
                plugins = [session.query(PluginTable).filter_by(name=command).first()]
            else:
                plugins = session.query(PluginTable).all()
        
        result: list[tuple[str, str, str]] = [
            (
                p.name,
                p.syntax,
                p.description,
                # todo: handle formatting
            )
            for p in plugins
        ]
        # Sort by name.
        return sorted(result, key=lambda tuple: tuple[0])

    def _help_command(
        self, message: dict[str, Any], command: str
    ) -> Response | Iterable[Response]:
        info_tuple: tuple[str, str, str] | None = None

        result = Help._get_help_info(command)
        if len(result) is None:
            return Response.command_not_found(message)
        info_tuple = result[0]

        help_message: str = "\n".join(info_tuple[1:])

        return Response.build_message(
            message, help_message, msg_type="private", to=message["sender_email"]
        )

    def _help_overview(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        # Get the command names.
        help_message: str = "\n".join(
            map(lambda tuple: "- " + tuple[0], Help._get_help_info())
        )

        return Response.build_message(
            message,
            HELP_TEMPLATE.format(
                message["sender_full_name"], help_message
            ),
            msg_type="private",
            to=message["sender_email"],
        )
