#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
import json
from typing import Any, Iterable

from tumcsbot.lib import Response, get_classes_from_path
from tumcsbot.plugin import (
    ArgConfig,
    CommandConfig,
    OptConfig,
    SubCommandConfig,
    PluginCommandMixin,
    Plugin,
    PluginTable,
    Privilege,
)
from tumcsbot.db import DB
from tumcsbot.plugin_decorators import DMError

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


class Help(PluginCommandMixin, Plugin):
    """Post a help message to the requesting user."""

    # This plugin depends on all the others because it needs their db entries.
    dependencies = PluginCommandMixin.dependencies + [
        plugin_class.plugin_name()
        for plugin_class in get_classes_from_path("tumcsbot.plugins", Plugin)  # type: ignore
    ]

    async def handle_message(
        self, message: dict[str, Any]
    ) -> Response | Iterable[Response]:
        # Example: reuse your existing OpenAI setup
        # Point to the local server
        # client = OpenAI(base_url="http://localhost:1234/v1", api_key="not-needed")
        # self.logger.info("Sending message to OpenAI")
        # async with self.client.typing_direct(message["sender_id"]):
        #     # Example: create a completion using the local model (the default model for the organization
        #     completion = client.chat.completions.create(
        #         model="local-model", # this field is currently unused
        #         messages=[
        #           {"role": "system", "content": "You are a helpful assistant. You are a bot on the messenger plattform Zulip, the main communication channel for many students at the TUM."},
        #           {"role": "user", "content": message['content']}
        #         ],
        #         temperature=0.7,
        #     )
        #     return Response.build_message(message, completion.choices[0].message.content)

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
    def _get_help_info(
        command: str | None = None, privilige: Privilege = Privilege.USER
    ) -> list[CommandConfig]:
        """Get help information from each command.

        Return a list of tuples (command name, syntax, description).
        """
        # todo: privilage level
        with DB.session() as session:
            if command is not None:
                plugins = [c for c in session.query(PluginTable).filter_by(name=command).all() if c is not None]
            else:
                plugins = session.query(PluginTable).all()

        result: CommandConfig = [
            CommandConfig.from_dict(json.loads(str(p.config)))
            for p in plugins
            if p is not None
        ]
        # Sort by name.
        return sorted(result, key=lambda c: c.name)

    def _help_command(
        self, message: dict[str, Any], command: str
    ) -> Response | Iterable[Response]:

        result = Help._get_help_info(command)
        if len(result) == 0:
            raise DMError(f"Command '{command}' not found.")

        cmd = result[0]
        name = cmd.name or command
        msg = f"# {name.capitalize()}\n"

        only_mod_privileges = all(
            [sub.privilege > Privilege.USER for sub in cmd.subcommands]
        )
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
    def _format_subcommand(cmd_name: str, subcommand: SubCommandConfig) -> str:
        privilage = (
            " " + PRIVILAGE_MSG + "\n"
            if subcommand.privilege is not None
            and subcommand.privilege > Privilege.USER
            else ""
        )
        out = f"```spoiler {subcommand.name.replace('_', ' ').title()}\n{privilage}\n{subcommand.short_help_msg}\n````text\n{cmd_name} {subcommand.syntax}\n````\n\n"
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
            HELP_TEMPLATE.format(message["sender_full_name"], help_message),
            msg_type="private",
            to=message["sender_email"],
        )
