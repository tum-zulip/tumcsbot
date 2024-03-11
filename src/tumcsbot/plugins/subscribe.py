#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from typing import AsyncGenerator, Any
from tumcsbot.db import Session

from tumcsbot.lib import Regex, Response
from tumcsbot.command_parser import CommandParser
from tumcsbot.plugin import PluginCommandMixin, Plugin, Privilege, ZulipUser
from tumcsbot.plugin_decorators import (
    DMError,
    DMResponse,
    command,
    privilege,
    arg,
    response_type,
    PartialError,
    PartialSuccess,
)


class Subscribe(PluginCommandMixin, Plugin):
    """
    Subscribe all subscribers of the given streams or users to the destination stream.
    ---
    If the destination stream does not exist yet, it will be \
    automatically created (with an empty description).
    The stream names may be of the form `<stream_name>` or \
    `#**<stream_name>**` (autocompleted stream name).
    The user names may be of the form `<user_name>`, \
    `@**<user_name>**`, `@_**<user_name>**`, \
    `@**<user_name>|<user_id>**`, `@_**<user_name>|<user_id>**` \
    (autocompleted user names, possibly with the user id (an int)).

    **Stream or user names containing whitespace need to be quoted.**
    Note that the bot must have the permissions to invite users to \
    the destination stream. Also note that there may exist multiple \
    users with the same name and **all** of them will be subscribed \
    if you do not provide a user id for an ambiguous user name. If \
    you use Zulip's autocomplete feature for user names, the user \
    id is automatically added if neccessary.

    ````text
    subscribe streams "destination stream" "#**test stream**" mystream
    subscribe user_emails "destination stream" "foo@bar.com" "user42@zulip.org"
    ````
    """

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_stream", Regex.get_stream_name, description="The destination stream name."
    )
    @arg("streams", Regex.get_stream_name, description="The stream names.", greedy=True)
    async def streams(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe all subscribers of the given streams to the destination stream.
        """
        for stream in args.streams:
            if not await self.client.subscribe_all_from_stream_to_stream(
                stream, args.dest_stream, None
            ):
                yield PartialError(f"Failed to subscribe stream {stream}.")
            else:
                yield PartialSuccess(f"Subscribed stream {stream}.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_stream", Regex.get_stream_name, description="The destination stream name."
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
        Subscribe all users with the specified names to the destination stream.
        """
        # First, get all the ids of the users whose ids we do not already know.
        user_ids: list[int] = [await u.id for u in args.users]

        if not self.client.subscribe_users(user_ids, args.dest_stream):
            raise DMError("Failed to subscribe all users.")
        yield DMResponse("Subscribed users.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_stream", Regex.get_stream_name, description="The destination stream name."
    )
    @arg("user_emails", str, description="The user email addresses.", greedy=True)
    async def user_emails(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe all users with the specified email addresses to the destination stream. \
        Note thet the email addresses need to match the `delivery_email` field. \
        Check if you and me are having access to it. \
        (In the Organization Settings of your Zulip Server, the value of `Who can access user email addresses` needs to be at least `Admins only`.)
        """
        user_ids: list[int] | None = await self.client.get_user_ids_from_emails(
            args.user_emails
        )
        if user_ids is None:
            raise DMError("Failed to get user ids from emails.")

        if not await self.client.subscribe_users(
            user_ids, args.dest_stream, allow_private_streams=True
        ):
            raise DMError("Failed to subscribe all users.")

        yield Response.ok(message)

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "dest_stream", Regex.get_stream_name, description="The destination stream name."
    )
    async def all_users(
        self,
        _sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe all users to the destination stream.
        """
        result: dict[str, Any] = await self.client.get_users()
        user_ids: list[int] = [user["user_id"] for user in result["members"]]

        await self.client.subscribe_users(user_ids, args.dest_stream)
        yield Response.ok(message)
