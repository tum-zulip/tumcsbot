#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
import json
from typing import Any, Iterable

from tumcsbot.lib import Response, get_classes_from_path
from tumcsbot.plugin import ArgConfig, CommandConfig, OptConfig, SubCommandConfig, PluginCommandMixin, _Plugin, PluginThread, PluginTable, Privilege
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
PRIVILAGE_MSG = "**[administrator/moderator rights needed]**"


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
    def _get_help_info(command: str | None = None, privilige: Privilege = Privilege.USER) -> list[CommandConfig]:
        """Get help information from each command.

        Return a list of tuples (command name, syntax, description).
        """
        # todo: privilage level
        with DB.session() as session:
            if command is not None:
                plugins = [session.query(PluginTable).filter_by(name=command).first()]
            else:
                plugins = session.query(PluginTable).all()
        
        result: CommandConfig = [
            CommandConfig.from_dict(json.loads(p.config))
            for p in plugins if p is not None
        ]
        # Sort by name.
        return sorted(result, key=lambda c: c.name)

    def _help_command(
        self, message: dict[str, Any], command: str
    ) -> Response | Iterable[Response]:

        result = Help._get_help_info(command)
        if len(result) == 0:
            return Response.command_not_found(message)
        
        cmd = result[0]

        msg = f"# {cmd.name.capitalize()}\n"

        only_mod_privileges = all([sub.privilege > Privilege.USER for sub in cmd.subcommands])
        if only_mod_privileges:
            msg += PRIVILAGE_MSG + "\n"

        msg += "\n" + cmd.short_help_msg
        
        msg += f"\n```text\n{cmd.syntax}\n```\n\n"

        msg += "## Subcommands:\n" if cmd.subcommands else ""
        for sub in cmd.subcommands:
            msg += Help._format_subcommand(cmd.name, sub)

        return Response.build_message(
            message, content=msg, msg_type="private", to=message["sender_email"]
        )
    
    @staticmethod
    def _format_option(option: OptConfig) -> str:
        long_opt = f"/--{option.long_opt}" if option.long_opt else ""
        privilage = " " + PRIVILAGE_MSG if option.privilege is not None and option.privilege > Privilege.USER else ""
        return f"- `-{option.opt}{long_opt}`\t{option.description}{privilage}"
    
    @staticmethod
    def _format_argument(argument: ArgConfig) -> str:
        privilage = " " + PRIVILAGE_MSG if argument.privilege is not None and argument.privilege > Privilege.USER else ""
        greedy = " (greedy)" if argument.greedy else ""
        return f"- `{argument.name}`\t{argument.description}{greedy}{privilage}"
    
    @staticmethod
    def _format_subcommand(cmd_name: str, subcommand: SubCommandConfig) -> str:
        privilage = " " + PRIVILAGE_MSG + "\n" if  subcommand.privilege is not None and subcommand.privilege > Privilege.USER else ""
        out = f"```spoiler {subcommand.name.capitalize()}\n{privilage}\n{subcommand.short_help_msg}\n````text\n{cmd_name} {subcommand.syntax}\n````\n\n"
        for opt in subcommand.opts:
            out += Help._format_option(opt) + "\n"
        for arg in subcommand.args:
            out += Help._format_argument(arg) + "\n"
        out += "```\n"
        return out

    def _help_overview(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        # Get the command names.
        help_message: str = "\n".join(
            map(lambda cfg: "- " + cfg.name, Help._get_help_info())
        )

        return Response.build_message(
            message,
            HELP_TEMPLATE.format(
                message["sender_full_name"], help_message
            ),
            msg_type="private",
            to=message["sender_email"],
        )
