#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc
from typing import cast, Any, Iterable

from tumcsbot.lib import Regex, Response
from tumcsbot.command_parser import CommandParser
from tumcsbot.plugin import PluginCommandMixin, PluginThread
from tumcsbot.plugin_decorators import *

class Subscribe(PluginCommandMixin, PluginThread):
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
    @arg("dest_stream", Regex.get_stream_name, description="The destination stream name.")
    @arg("streams", Regex.get_stream_name, description="The stream names.", greedy=True)
    def streams(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Subscribe all subscribers of the given streams to the destination stream.
        """
        failed: list[str] = []

        for stream in args.streams:
            if not self.client.subscribe_all_from_stream_to_stream(
                stream, args.dest_stream, None
            ):
                failed.append(stream)

        if not failed:
            return Response.ok(message)

        return Response.build_message(
            message, "Failed to subscribe the following streams:\n" + "\n".join(failed)
        )
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("dest_stream", Regex.get_stream_name, description="The destination stream name.")
    @arg("users", lambda string: Regex.get_user_name(string, get_user_id=True), description="The user names.", greedy=True)
    def users(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Subscribe all users with the specified names to the destination stream.
        """
        # First, get all the ids of the users whose ids we do not already know.
        user_ids: list[int] | None = self.client.get_user_ids_from_display_names(
            map(
                lambda o: o[0] if isinstance(o, tuple) else o,
                filter(
                    lambda o: isinstance(o, str)
                    or (isinstance(o, tuple) and o[1] is None),
                    args.users,
                ),
            )
        )
        if user_ids is None:
            return Response.build_message(message, "error: could not get the user ids.")

        user_ids.extend(
            map(
                lambda t: cast(int, t[1]),
                filter(lambda o: isinstance(o, tuple) and isinstance(o[1], int), args.users),
            )
        )

        if not self.client.subscribe_users(user_ids, args.dest_stream):
            return Response.error(message)

        return Response.ok(message)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("dest_stream", Regex.get_stream_name, description="The destination stream name.")
    @arg("user_emails", str, description="The user email addresses.", greedy=True)
    def user_emails(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Subscribe all users with the specified email addresses to the destination stream. \
        Note thet the email addresses need to match the `delivery_email` field. \
        Check if you and me are having access to it. \
        (In the Organization Settings of your Zulip Server, the value of `Who can access user email addresses` needs to be at least `Admins only`.)
        """
        user_ids: list[int] | None = self.client.get_user_ids_from_emails(args.user_emails)
        if user_ids is None:
            return Response.build_message(message, "error: could not get the user ids.")

        if not self.client.subscribe_users(
            user_ids, args.dest_stream, allow_private_streams=True
        ):
            return Response.error(message)

        return Response.ok(message)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("dest_stream", Regex.get_stream_name, description="The destination stream name.")
    def all_users(self, message: dict[str, Any], args: CommandParser.Args, opts: CommandParser.Opts) -> Response | Iterable[Response]:
        """
        Subscribe all users to the destination stream.
        """
        return self.subscribe_all_users(message, args.dest_stream)


    def _subscribe_all_users(
        self,
        message: dict[str, Any],
        dest_stream: str,
    ) -> Response | Iterable[Response]:

        result: dict[str, Any] = self.client.get_users()
        if result["result"] != "success":
            return Response.error(message)
        user_ids: list[int] = [user["user_id"] for user in result["members"]]

        if not self.client.subscribe_users(user_ids, dest_stream):
            return Response.error(message)

        return Response.ok(message)




