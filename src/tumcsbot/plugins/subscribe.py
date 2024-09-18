#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import AsyncGenerator, Any
from tumcsbot.lib.db import Session

from tumcsbot.lib.response import Response
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.types import (
    Privilege,
    PartialError,
    PartialSuccess,
    DMError,
    DMResponse,
    ZulipChannel,
    response_type,
    ZulipUser,
)
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.plugin import PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import (
    command,
    privilege,
    arg,
)


class Subscribe(PluginCommandMixin, Plugin):
    """
    Subscribe users to a channel.
    ---
    If the destination channel does not exist yet, it will be automatically created (with an empty description).
    The channel names may be of the form `#**<channel_name>**` (autocompleted channel name).
    The user names may be of the form `@**<user_name>**`, `@_**<user_name>**`, `@**<user_name>|<user_id>**`, `@_**<user_name>|<user_id>**` (autocompleted user names, possibly with the user id (an int)).

    Note that the bot must have the permissions to invite users to the destination channel.
    Also note that there may exist multiple users with the same name and **all** of them will be subscribed if you do not provide a user id for an ambiguous user name.
    If you use Zulip's autocomplete feature for user names, the user id is automatically added if neccessary.
    """

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_channel", ZulipChannel, description="The destination channel name."
    )
    @arg("channels", ZulipChannel, description="The channel names.", greedy=True)
    async def channels(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe all subscribers of the given channels to the destination channel.
        """
        channels: list[ZulipChannel] = args.channels
        dest_channel: ZulipChannel = args.dest_channel
        for channel in channels:
            if not await self.client.subscribe_all_from_channel_to_channel(
                channel.name, dest_channel.name, None
            ):
                yield PartialError(f"Failed to subscribe channel {channel}.")
            else:
                yield PartialSuccess(f"Subscribed channel {channel}.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_channel", ZulipChannel, description="The destination channel name."
    )
    @arg(
        "users",
        ZulipUser,
        description="The user names.",
        greedy=True,
    )
    async def users(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe given users to the destination channel.
        """
        # First, get all the ids of the users whose ids we do not already know.
        users: list[ZulipUser] = args.users
        user_ids: list[int] = [user.id for user in users]
        dest_channel: ZulipChannel = args.dest_channel

        if not self.client.subscribe_users(user_ids, dest_channel.name):
            raise DMError("Failed to subscribe all users.")
        yield DMResponse("Subscribed users.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_channel", ZulipChannel, description="The destination channel name."
    )
    @arg("user_emails", str, description="The user email addresses.", greedy=True)
    async def user_emails(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe all users with the specified email addresses to the destination channel. \
        Note thet the email addresses need to match the `delivery_email` field. \
        Check if you and me are having access to it. \
        (In the Organization Settings of your Zulip Server, the value of `Who can access user email addresses` needs to be at least `Admins only`.)
        """
        user_ids: list[int] | None = await self.client.get_user_ids_from_emails(
            args.user_emails
        )
        dest_channel: ZulipChannel = args.dest_channel

        if user_ids is None:
            raise DMError("Failed to get user ids from emails.")

        if not await self.client.subscribe_users(
            user_ids, dest_channel.name, allow_private_channels=True
        ):
            raise DMError("Failed to subscribe all users.")

        yield DMResponse("Subscribed users.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_channel", ZulipChannel, description="The destination channel name."
    )
    async def all_users(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe all users to the destination channel.
        """
        result: dict[str, Any] = await self.client.get_users()
        user_ids: list[int] = [user["user_id"] for user in result["members"]]

        await self.client.subscribe_users(user_ids, args.dest_channel)
        yield DMResponse("Subscribed all users.")
