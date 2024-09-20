#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
import json
from typing import Any, Iterable

from tumcsbot.lib.response import Response
from tumcsbot.plugin import (
    PluginCommand,
    Plugin,
    PluginTable,
)
from tumcsbot.lib.types import (
    Privilege,
    ArgConfig,
    OptConfig,
    SubCommandConfig,
    CommandConfig,
)
from tumcsbot.lib.db import DB
from tumcsbot.lib.types import DMError
from tumcsbot.lib.utils import get_classes_from_path

HELP_TEMPLATE = cleandoc(
    """
    Hi {}!

    Use `help <command name>` to get more information about a certain command.
    Please consider that my command line parsing is comparable to the POSIX shell. \
    So in order to preserve arguments containing whitespace characters from splitting, they need to be quoted. \
    Special strings such as regexes containing backslash sequences may require single quotes instead of double quotes.

    Currently, you can use the following commands:
    {}

    Have a nice day! :bothappypad:
    """
)
PRIVILAGE_MSG = "**[administrator/moderator rights needed]**"


class Help(PluginCommand, Plugin):
    """Post a help message to the requesting user."""

    # This plugin depends on all the others because it needs their db entries.
    dependencies = PluginCommand.dependencies + [
        plugin_class.plugin_name()
        for plugin_class in get_classes_from_path("tumcsbot.plugins", Plugin)  # type: ignore
    ]

    async def handle_message(
        self, message: dict[str, Any]
    ) -> Response | Iterable[Response]:
        command: str = message["command"].strip()
        privileged = await self.client.user_is_privileged(message["sender_id"])
        if not command:
            return self._help_overview(message, privileged)
        return self._help_command(message, command, privileged)

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
    def _get_help_info(
        command: str | None = None, privilege: Privilege = Privilege.USER
    ) -> list[CommandConfig]:
        """Get help information from each command.

        Return a list of tuples (command name, syntax, description).
        """
        # todo: privilage level
        with DB.session() as session:
            if command is not None:
                plugins = [
                    c
                    for c in session.query(PluginTable).filter_by(name=command).all()
                    if c is not None
                ]
            else:
                plugins = session.query(PluginTable).all()

        result: list[CommandConfig] = [
            CommandConfig.from_dict(json.loads(str(p.config)))
            for p in plugins
            if p is not None
        ]
        # Sort by name.
        return sorted(result, key=lambda c: "" if c.name is None else c.name)

    def _help_command(
        self, message: dict[str, Any], command: str, privileged: bool = False
    ) -> Response | Iterable[Response]:
        result = Help._get_help_info(command)
        if len(result) == 0:
            raise DMError(f"Command '{command}' not found.")

        cmd = result[0]
        name = cmd.name or command
        msg = f"# {name.capitalize()}\n"

        only_mod_privileges = all(
            sub.privilege > Privilege.USER for sub in cmd.subcommands
        )
        if only_mod_privileges:
            msg += PRIVILAGE_MSG + "\n"

        msg += "\n" + cmd.short_help_msg
        CommandConfig

        msg += f"\n```text\n{cmd.syntax_for(Privilege.ADMIN if privileged else Privilege.USER)}\n```\n\n"

        msg += "## Subcommands:\n" if cmd.subcommands else ""
        for sub in cmd.subcommands:
            if not privileged and sub.privilege != Privilege.USER:
                continue
            if cmd.name is not None:
                msg += Help._format_subcommand(cmd.name, sub, privileged)

        return Response.build_message(
            message, content=msg, msg_type="private", to=message["sender_email"]
        )

    @staticmethod
    def _format_option(option: OptConfig) -> str:
        long_opt = f"/--{option.long_opt}" if option.long_opt else ""
        privilage = (
            " " + PRIVILAGE_MSG
            if option.privilege is not None and option.privilege > Privilege.USER
            else ""
        )
        return f"- `-{option.opt}{long_opt}`\t{option.description}{privilage}"

    @staticmethod
    def _format_argument(argument: ArgConfig) -> str:
        privilage = (
            " " + PRIVILAGE_MSG
            if argument.privilege is not None and argument.privilege > Privilege.USER
            else ""
        )
        greedy = " (greedy)" if argument.greedy else ""
        return f"- `{argument.name}`\t{argument.description}{greedy}{privilage}"

    @staticmethod
    def _format_subcommand(
        cmd_name: str, subcommand: SubCommandConfig, privileged: bool = False
    ) -> str:
        privilage = (
            " " + PRIVILAGE_MSG + "\n"
            if subcommand.privilege is not None
            and subcommand.privilege > Privilege.USER
            else ""
        )
        if subcommand.name is not None:
            out = f"""
```spoiler {subcommand.name.replace('_', ' ').title()}
{privilage}
{subcommand.short_help_msg}\n
````text\n
{cmd_name} {subcommand.syntax_for(Privilege.USER if not privileged else Privilege.ADMIN)}
````\n
"""
        for opt in subcommand.opts:
            if not privileged and opt.privilege and opt.privilege != Privilege.USER:
                continue
            out += Help._format_option(opt) + "\n"
        for arg in subcommand.args:
            if not privileged and arg.privilege and arg.privilege != Privilege.USER:
                continue
            out += Help._format_argument(arg) + "\n"
        out += "```\n"
        return out

    def _help_overview(
        self, message: dict[str, Any], privileged: bool = False
    ) -> Response | Iterable[Response]:
        # Get the command names.
        commands = Help._get_help_info()

        help_message: str = "\n".join(
            [
                " - " + cmd.name
                for cmd in commands
                if cmd.name is not None
                and (
                    privileged
                    or any(
                        subcmd.privilege == Privilege.USER for subcmd in cmd.subcommands
                    )
                )
            ]
        )

        return Response.build_message(
            message,
            HELP_TEMPLATE.format(message["sender_full_name"], help_message),
            msg_type="private",
            to=message["sender_email"],
        )
