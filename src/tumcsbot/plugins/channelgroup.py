#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from inspect import cleandoc

from typing import Any, Iterable, AsyncGenerator, cast
from sqlalchemy import Column, String, Integer, ForeignKey, Boolean
import sqlalchemy
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.ext.hybrid import hybrid_property
from tumcsbot.lib.regex import Regex

from tumcsbot.lib.response import Response
from tumcsbot.lib.client import AsyncClient, Event
from tumcsbot.plugin import Plugin, PluginCommand
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import DB, Session, TableBase
from tumcsbot.plugin_decorators import command, privilege, opt, arg
from tumcsbot.plugins.usergroup import UserGroup
from tumcsbot.plugins.usergroup import Usergroup
from tumcsbot.lib.types import (
    DMError,
    DMMessage,
    DMResponse,
    ReactionResponse,
    Privilege,
    response_type,
    ZulipUser,
    ZulipChannel,
)


class ChannelGroup(TableBase):  # type: ignore
    """Represents a ChannelGroup in the system."""

    __tablename__ = "ChannelGroups"

    ChannelGroupId = Column(String, primary_key=True)
    ChannelGroupEmote = Column(String, nullable=False, unique=True)
    UserGroupId = Column(
        Integer, ForeignKey("UserGroups.GroupId", ondelete="CASCADE"), nullable=False
    )

    _channels = relationship(
        "ChannelGroupMember", back_populates="groups", cascade="all, delete-orphan"
    )

    _usergroup = relationship(
        "UserGroup",
        cascade="all, delete-orphan",
        back_populates="_channelgroup",
        single_parent=True,
    )

    _course = relationship(
        "CourseDB",
        back_populates="_channels",
    )

    @hybrid_property
    def channels(self) -> list[ZulipChannel]:
        return [member.Channel for member in self._channels]

    @hybrid_property
    def usergroup(self) -> UserGroup:
        return cast(UserGroup, self._usergroup)


class ChannelGroupMember(TableBase):  # type: ignore
    """Represents a ChannelGroup member (Channel) in the system."""

    __tablename__ = "ChannelGroupMembers"

    ChannelGroupId = Column(
        String,
        ForeignKey("ChannelGroups.ChannelGroupId", ondelete="CASCADE"),
        primary_key=True,
    )
    Channel = Column(ZulipChannel, primary_key=True)  # type: ignore

    groups: Mapped[list["ChannelGroup"]] = relationship(
        viewonly=True, back_populates="_channels"
    )


class GroupClaim(TableBase):  # type: ignore
    """Represents a message being claimed in a specific ChannelGroup in the system."""

    __tablename__ = "GroupClaims"

    MessageId = Column(Integer, primary_key=True)
    GroupId = Column(
        String,
        ForeignKey("ChannelGroups.ChannelGroupId", ondelete="CASCADE"),
        primary_key=True,
    )


class GroupClaimAll(TableBase):  # type: ignore
    """Represents a message being claimes in all ChannelGroups in the system."""

    __tablename__ = "GroupClaimsAll"

    MessageId = Column(Integer, primary_key=True)
    IsAnnouncement = Column(Boolean, default=False)

    # Define the constraint to ensure MessageId is unique across all GroupClaims tables
    # UniqueConstraint('MessageId', name='uq_group_claims_all_message_id')


class Channelgroup(PluginCommand, Plugin):
    """
    Manage ChannelGroups.
    """

    # ========================================================================================================================
    #       EVENT HANDLER
    # ========================================================================================================================

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        data: dict[str, Any] = event.data
        if data["type"] == "reaction":
            self.logger.debug("User reacted to a claimed message")
            return await self.handle_reaction_event(data)
        if data["type"] == "delete_message":
            self.logger.debug("User deleted (potentially claimed) a message")
            return await self.handle_delete_message(data)
        if data["type"] == "stream":
            op = data["op"]
            if op == "create":
                op = "created"
            elif op == "delete":
                op = "deleted"
            else:
                op = "unknown operation (" + op + ")"

            self.logger.info(
                "Channels %s %s", ', '.join([f'#**{s['name']}**' for s in data['streams']]), op
            )
            self.logger.debug(data)
            return await self.handle_channel_event(data)
        self.logger.debug("%s", event)
        return await self.handle_message(data["message"])

    async def handle_delete_message(
        self, event: dict[str, Any]
    ) -> Response | Iterable[Response]:
        Id: int = event["message_id"]
        with DB.session() as session:
            try:
                session.query(GroupClaim).filter(GroupClaim.MessageId == Id).delete()
                session.query(GroupClaimAll).filter(
                    GroupClaimAll.MessageId == Id
                ).delete()
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                session.rollback()
        return Response.none()

    async def handle_reaction_event(
        self, event: dict[str, Any]
    ) -> Response | Iterable[Response]:
        emj: str = event["emoji_name"]
        user_id: int = event["user_id"]
        group_id: str | None = Channelgroup.get_group_id_from_emoji_event(emj)

        try:
            if group_id is None:
                return Response.none()
            if event["op"] == "add":
                await Channelgroup.subscribe_h(self.client, user_id, group_id)
            if event["op"] == "remove":
                await Channelgroup.unsubscribe_h(self.client, user_id, group_id)
        except DMError:
            self.logger.info("Failed to (un)subscribe the user to Channelgroup")
            Response.build_message(
                message=None,
                content=f"Failed to (un)subscribe to Channelgroup {group_id} via Emote-Reaction :{emj}:",
                to=user_id,
            )

        return Response.none()

    async def handle_channel_event(
        self, event: dict[str, Any]
    ) -> Response | Iterable[Response]:
        for channel in event["streams"]:
            # Channels
            name_d: str = channel["name"]
            id_d: int = channel["stream_id"]

            group_ids_d: list[str] = Channelgroup.get_group_ids_from_channel_id(
                id_d
            )

            if group_ids_d:
                self.logger.info(
                    "Channel %s being deleted from groups %s", name_d, group_ids_d
                )

            for group_id in group_ids_d:
                with DB.session() as session:
                    s: ChannelGroup | None = (
                        session.query(ChannelGroup)
                        .filter(ChannelGroup.ChannelGroupId == group_id)
                        .one()
                    )
                    if s is not None:
                        await Channelgroup.remove_channels_by_id(
                            session, s, [id_d]
                        )

            # messages
            with DB.session() as session:
                claims: list[GroupClaim] = session.query(GroupClaim).all()
                for claim in claims:
                    msg = await self.client.get_message_by_id(int(claim.MessageId))
                    if msg["type"] == "stream" and msg["stream_id"] == id_d:
                        try:
                            session.query(GroupClaim).filter(
                                GroupClaim.MessageId == msg["id"]
                            ).delete()
                            session.commit()
                        except sqlalchemy.exc.IntegrityError:
                            session.rollback()

                claimsAll: list[GroupClaimAll] = session.query(GroupClaimAll).all()
                for claim in claimsAll:
                    msg = await self.client.get_message_by_id(int(claim.MessageId))
                    if msg["type"] == "stream" and msg["stream_id"] == id_d:
                        try:
                            session.query(GroupClaimAll).filter(
                                GroupClaimAll.MessageId == msg["id"]
                            ).delete()
                            session.commit()
                        except sqlalchemy.exc.IntegrityError:
                            session.rollback()

        return Response.none()

    async def is_responsible(self, event: Event) -> bool:
        return (
            await super().is_responsible(event)
            or (
                event.data["type"] == "reaction"
                and event.data["op"] in ["add", "remove"]
                and event.data["user_id"] != self.client.id
                and Channelgroup.message_is_claimed(
                    event.data["message_id"], event.data["emoji_name"]
                )
            )
            or (
                event.data["type"] == "stream" and event.data["op"] == "delete"
            )
            or (
                event.data["type"] == "delete_message"
                and event.data["message_type"] == "stream"
            )
        )

    # ========================================================================================================================
    #       SUBCOMMANDS
    # ========================================================================================================================

    @command(name="list")
    @privilege(Privilege.USER)
    @opt("a", long_opt="all", description="Display all existing Channelgroups.")
    async def _list(
        self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List all your Channelgroups with their associated patterns.
        """
        response: str = (
            "Group Id | Emoji | Channels | Claimed Msg\n---- | ---- | ---- | ----"
        )

        message_link: str = "[{0}](" + self.client.base_url[:-4] + "#narrow/id/{0})"

        groups: list[ChannelGroup]
        if opts.a:
            groups = session.query(ChannelGroup).all()
        else:
            groups = Channelgroup.get_groups_for_user(session, sender)
            response = (
                sender.mention_silent
                + " is in the following Channelgroups:\n\n"
                + response
            )

        if len(groups) == 0:
            if opts.a:
                raise DMError("No Channel groups found")
            raise DMError("You are not in any Channelgroups")

        for group in groups:
            group_id = group.ChannelGroupId
            emoji = group.ChannelGroupEmote
            channels: list[str] = await Channelgroup.get_channel_names(
                session, [group]
            )

            channels_concat: str = ", ".join(f"`{s}`" for s in channels)
            claims: str = ", ".join(
                [
                    message_link.format(claim.MessageId)
                    for claim in session.query(GroupClaim)
                    .filter(GroupClaim.GroupId == group.ChannelGroupId)
                    .all()
                ]
            )
            response += (
                f"\n{group_id} | {emoji} :{emoji}: | {channels_concat} | {claims}"
            )

        response += "\n\nMessages claimed for all groups: \n" + ", ".join(
            message_link.format(claim.MessageId)
            for claim in session.query(GroupClaimAll).all()
        )

        yield DMResponse(response)

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", str, description="The identifier of the Channelgroup to add.")
    @arg(
        "emoji", Regex.get_emoji_name, description="The emoji to use for the reaction."
    )
    async def create(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Create a new Channelgroup.
        """
        Id: str = args.group_id
        emoji_name: str | None = args.emoji

        if not emoji_name:
            raise DMError(f"{emoji_name} is not a valid emote.")

        await Channelgroup.create_group(session, Id, emoji_name, self.client)
        yield DMResponse(f"Channelgroup `{Id}` created.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup to delete.",
    )
    async def delete(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Delete a Channelgroup.
        """
        group: ChannelGroup = args.group_id

        await Channelgroup.delete_group_h(session, group, self.client)
        yield DMResponse(f"Channelgroup `{group.ChannelGroupId}` deleted")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of a Channelgroup to add channels to.",
    )
    @arg("channels", ZulipChannel, description="The channel names to add.", greedy=True)
    async def add_channels(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add channels to a Channelgroup.
        """
        group: ChannelGroup = args.group_id

        Channelgroup.add_zulip_channels(session, args.channels, group)
        yield DMResponse(f"Added channels to Channelgroup `{group.ChannelGroupId}`.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of a Channelgroup to remove channels from.",
    )
    @arg("channels", ZulipChannel, description="The channel names to remove.", greedy=True)
    async def remove_channels(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Remove channels from a Channelgroup.
        """
        group: ChannelGroup = args.group_id

        await Channelgroup.remove_zulip_channels(
            session, args.channels, group
        )
        yield DMResponse(
            f"Removed channels from Channelgroup `{group.ChannelGroupId}`."
        )

    @command
    @privilege(Privilege.USER)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup to subscribe to.",
    )
    async def subscribe(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe to a Channelgroup.
        """
        group: ChannelGroup = args.group_id
        members: UserGroup = Channelgroup.get_usergroup(session, group)
        user_id: list[int] = [sender.id]
        channel_names: list[str] = await Channelgroup.get_channel_names(
            session, [group]
        )

        channels: list[tuple[str, str | None]] = [
            (channel_name, None) for channel_name in channel_names
        ]

        await self.client.subscribe_users_multiple_channels(user_id, channels)

        Usergroup.add_user_to_group(session, sender, members)

        yield DMResponse(
            f"You have subscribed to the Channelgroup `{group.ChannelGroupId}`"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup the users should get subscribed to.",
    )
    @arg(
        "user",
        ZulipUser,
        "The users that should get subscribed to the Channelgroup.",
        greedy=True,
    )
    async def subscribe_users(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe a list of users to a Channelgroup.
        """
        group: ChannelGroup = args.group_id
        members: UserGroup = Channelgroup.get_usergroup(session, group)
        users: list[ZulipUser] = args.user
        user_ids: list[int] = [user.id for user in users]
        channel_names: list[str] = await Channelgroup.get_channel_names(
            session, [group]
        )

        channels: list[tuple[str, str | None]] = [
            (channel_name, None) for channel_name in channel_names
        ]

        await self.client.subscribe_users_multiple_channels(user_ids, channels)

        for user in users:
            Usergroup.add_user_to_group(session, user, members)

        yield DMResponse(
            f"You have subscribed the users to the Channelgroup `{group.ChannelGroupId}`"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "channelgroup_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup the users should get subscribed to.",
    )
    @arg(
        "usergroup",
        UserGroup.GroupName,
        "The name of the usergroup that should get subscribed to the Channelgroup.",
    )
    async def subscribe_usergroup(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe the members of a usergroup to a Channelgroup.
        """
        group: ChannelGroup = args.channelgroup_id
        members: UserGroup = Channelgroup.get_usergroup(session, group)
        ugroup: UserGroup = args.usergroup
        users: list[ZulipUser] = await Usergroup.get_users_for_group(session, ugroup)
        user_ids: list[int] = Usergroup.get_user_ids_for_group(session, ugroup)
        channel_names: list[str] = await Channelgroup.get_channel_names(
            session, [group]
        )

        channels: list[tuple[str, str | None]] = [
            (channel_name, None) for channel_name in channel_names
        ]

        await self.client.subscribe_users_multiple_channels(user_ids, channels)

        for user in users:
            Usergroup.add_user_to_group(session, user, members)

        yield DMResponse(
            f"You have subscribed the users to the Channelgroup `{group.ChannelGroupId}`"
        )

    @command
    @privilege(Privilege.USER)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup to unsubscribe from.",
    )
    @opt(
        "t",
        long_opt="total",
        description="Unsubscribe from all channels of this group. (default)",
    )
    @opt(
        "k",
        long_opt="keep",
        description="Keep the channels of this group you are currently subscribed to.",
    )
    async def unsubscribe(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unsubscribe from a Channelgroup.
        """
        group: ChannelGroup = args.group_id
        members: UserGroup = Channelgroup.get_usergroup(session, group)
        user_id: int = sender.id
        channel_names: list[str] = await Channelgroup.get_unique_channel_names(
            session, sender, group
        )

        if opts.k and opts.t:
            raise DMError(
                "The `-k` and `-t` flags are mutually exclusive, see `help channelgroup`."
            )

        Usergroup.remove_user_from_group(session, sender, members)

        if opts.t or not opts.k:
            await self.client.remove_subscriptions(user_id, channel_names)

        yield DMResponse(
            f"You have unsubscribed from the Channelgroup `{group.ChannelGroupId}`"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup the users should get unsubscribed from.",
    )
    @arg(
        "user",
        ZulipUser,
        "The users that should get unsubscribed from the Channelgroup",
        greedy=True,
    )
    @opt(
        "t",
        long_opt="total",
        description="Unsubscribe the users from all channels of this group. (default)",
    )
    @opt(
        "k",
        long_opt="keep",
        description="Unsubscribe the users from the Channelgroup, but keep the individual subscriptions for the user.",
    )
    async def unsubscribe_users(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unsubscribe a list of users from a Channelgroup.
        """
        group: ChannelGroup = args.group_id
        members: UserGroup = Channelgroup.get_usergroup(session, group)
        users: list[ZulipUser] = args.user
        user_ids: list[int] = [user.id for user in users]
        channel_names: list[str] = await Channelgroup.get_unique_channel_names(
            session, sender, group
        )

        if opts.k and opts.t:
            raise DMError(
                "The `-k` and `-t` flags are mutually exclusive, see `help channelgroup`."
            )

        for user in users:
            Usergroup.remove_user_from_group(session, user, members)

        if opts.t or not opts.k:
            for ID in user_ids:
                await self.client.remove_subscriptions(ID, channel_names)

        yield DMResponse(
            f"You have unsubscribed the users from the channelgroup `{group.ChannelGroupId}`"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "channelgroup_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup the users should get unsubscribed from.",
    )
    @arg(
        "usergroup",
        UserGroup.GroupName,
        "The name of the usergroup that should get unsubscribed from the Channelgroup",
    )
    @opt(
        "t",
        long_opt="total",
        description="Unsubscribe the users from all channels of this group. (default)",
    )
    @opt(
        "k",
        long_opt="keep",
        description="Unsubscribe the users from the Channelgroup, but keep the individual subscriptions for the user.",
    )
    async def unsubscribe_usergroup(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unsubscribe the members of a usergroup from a Channelgroup.
        """
        group: ChannelGroup = args.channelgroup_id
        members: UserGroup = Channelgroup.get_usergroup(session, group)
        ugroup: UserGroup = args.usergroup
        users: list[ZulipUser] = await Usergroup.get_users_for_group(session, ugroup)
        user_ids: list[int] = Usergroup.get_user_ids_for_group(session, ugroup)
        channel_names: list[str] = await Channelgroup.get_unique_channel_names(
            session, sender, group
        )

        if opts.k and opts.t:
            raise DMError(
                "The `-k` and `-t` flags are mutually exclusive, see `help channelgroup`."
            )

        for user in users:
            Usergroup.remove_user_from_group(session, user, members)

        if opts.t or not opts.k:
            for ID in user_ids:
                await self.client.remove_subscriptions(ID, channel_names)

        yield DMResponse(
            f"You have unsubscribed the users from the channelgroup `{group.ChannelGroupId}`"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of the Channelgroup to fix.",
        optional=True,
    )
    @opt(
        "a",
        long_opt="all",
        description="Fix subscriptions for all existing Channelgroups.",
    )
    async def fix(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Makes sure every subscriber of the given Channelgroup is subscribed to all channels of this group.
        """
        group: ChannelGroup | None = args.group_id

        if opts.a:
            await Channelgroup.fix_all_h(self.client, session)
        else:
            if group is None:
                raise DMError(
                    "Missing `group_id` of Channelgroup, see `help channelgroup`."
                )
            await Channelgroup.fix_h(self.client, session, group)

        yield ReactionResponse("ok")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "group_id",
        ChannelGroup.ChannelGroupId,
        description="The identifier of a Channelgroup for which to claim message.",
        optional=True,
    )
    @opt(
        "a",
        long_opt="all",
        description="Claim the message in all existing Channelgroups.",
    )
    async def claim(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Make the message, written in a channel and addressed to @**TUMCSBot**, "special" for a given Channelgroup.
        If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, the user gets subscribed to all channels belonging to this group
        """
        group: ChannelGroup = args.group_id
        msg_id: int = message["id"]

        if not opts.a and not group:
            raise DMError(
                "Either argument `group_id` or flag `-a` necessary, see `help channelgroup`."
            )
        if opts.a and group:
            raise DMError(
                "The argument `group_id` and flag `-a` are mutually exclusive, see `help channelgroup`."
            )

        if message["type"] != "stream":
            raise DMError("Claim only channel messages.")

        channel = await self.client.get_channel_by_id(message["stream_id"])
        if not channel:
            raise DMError("Stream not found")
        name = channel["name"]

        await Channelgroup.claim_h(
            group=group, session=session, client=self.client, message=message, All=opts.a
        )

        if not opts.a:
            resp = f"Claimed message {msg_id} in #**{name}** for Channelgroup `{group.ChannelGroupId}`."
        else:
            resp = f"Claimed message {msg_id} in #**{name}** for all Channelgroups."

        yield DMMessage(sender, resp)

    @command
    @privilege(Privilege.ADMIN)
    @arg("message_id", int, description="The id of the message to claim.")
    @arg(
        "group_id",
        str,
        description="The id of a Channelgroup for which to claim message.",
        optional=True,
    )
    @opt(
        "a",
        long_opt="all",
        description="Claim the message in all existing Channelgroups.",
    )
    async def claim_message(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Make a specified message "special" for a given Channelgroup.
        If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, the user gets subscribed to all channels belonging to this group.
        """
        group_id: str = args.group_id
        msg_id: int = args.message_id

        if not opts.a:
            if not group_id:
                raise DMError(
                    "Either argument `group_id` or flag `-a` necessary, see `help channelgroup`."
                )

            group: ChannelGroup | None = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupId == group_id)
                .one_or_none()
            )
            if not group:
                raise DMError(
                    f"Uuups, it looks like i could not find any Channelgroup associated with `{group_id}` :botsceptical:"
                )

        else:
            if group_id:
                raise DMError(
                    "The argument `group_id` and flag `-a` are mutually exclusive, see `help channelgroup`."
                )

            group = None

        msg = await self.client.get_message_by_id(msg_id)

        if msg["type"] != "stream":
            raise DMError("Claim only channel messages.")

        channel = await self.client.get_channel_by_id(msg["stream_id"])
        if not channel:
            raise DMError("Channel not found")

        name = channel["name"]

        await Channelgroup.claim_h(
            group=group, session=session, client=self.client, message=msg, All=opts.a
        )

        if group is not None:
            resp = f"Claimed message {msg_id} in #**{name}** for Channelgroup `{group.ChannelGroupId}`."
        else:
            resp = f"Claimed message {msg_id} in #**{name}** for all Channelgroups."

        yield DMResponse(resp)

    @command
    @privilege(Privilege.ADMIN)
    @arg("message_id", int, description="The id of the message to unclaim.")
    @arg(
        "group_id",
        str,
        description="The identifier of a Channelgroup for which to unclaim message.",
        optional=True,
    )
    @opt(
        "a",
        long_opt="all",
        description="Unclaim the message for all existing Channelgroups.",
    )
    async def unclaim_message(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Reverts "special" status of a claimed message.
        """
        group_id: str = args.group_id
        msg_id: int = args.message_id

        if not opts.a:
            if not group_id:
                raise DMError(
                    "Either argument `group_id` or flag `-a` necessary, see `help channelgroup`."
                )

            group: ChannelGroup | None = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupId == group_id)
                .one_or_none()
            )
            if not group:
                raise DMError(
                    f"Uuups, it looks like i could not find any Channelgroup associated with `{group_id}` :botsceptical:"
                )

        else:
            if group_id:
                raise DMError(
                    "The argument `group_id` and flag `-a` are mutually exclusive, see `help channelgroup`."
                )

            group = None

        msg = await self.client.get_message_by_id(msg_id)

        if msg["type"] != "stream":
            raise DMError("Unclaim only channel messages.")

        channel = await self.client.get_channel_by_id(msg["stream_id"])
        if not channel:
            raise DMError("Channel not found")
        name = channel["name"]

        await Channelgroup.unclaim_h(
            group=group, session=session, message_id=msg_id, All=opts.a
        )

        if group is not None:
            resp = f"Unlaimed message {msg_id} in #**{name}** for Channelgroup `{group.ChannelGroupId}`."
        else:
            resp = f"Unlaimed message {msg_id} in #**{name}** for all Channelgroups."

        yield DMResponse(resp)

    @command
    @privilege(Privilege.ADMIN)
    async def announce(
        self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        By writing the message in a channel addressed to @**TUMCSBot**, a "special" message from the bot for all Channelgroups with a list of all existing groups is triggered.
        """

        if message["type"] != "stream":
            raise DMError("Claim only channel messages.")

        await Channelgroup.announce_h(session, message, self.client)

        channel = await self.client.get_channel_by_id(message["stream_id"])
        if not channel:
            raise DMError("Channel not found")
        name = channel["name"]
        yield DMMessage(sender, f"Announced message in Channel #**{name}**.")

    @command
    @privilege(Privilege.ADMIN)
    @arg("message_id", int, description="The id of the message to unannounce.")
    async def unannounce(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Reverts "special" status of an announced message.
        """
        msg_id: int = args.message_id

        msg = await self.client.get_message_by_id(msg_id)
        if not msg:
            raise DMError("Message not found")

        channel = await self.client.get_channel_by_id(msg["stream_id"])
        if not channel:
            raise DMError("Channel not found")

        name = channel["name"]

        await Channelgroup.unannounce_h(session, msg_id, self.client)

        yield DMResponse(f"Unannounced message in Channel #**{name}**.")

    # ========================================================================================================================
    #       CLASS METHODS
    # ========================================================================================================================

    @staticmethod
    async def create_group(session: Session, ID: str, emote: str, client: AsyncClient) -> None:
        """
        Create a new ChannelGroup.

        Args:
            session: The database session.
            ID: The id of the group.
            emote: the emote of the group.

        Raises:
            DMError: If the group creation fails.

        Returns:
            None
        """
        if (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupId == ID)
            .first()
            is not None
        ):
            raise DMError(f"Channelgroup `{ID}` already exists")

        ugroup: UserGroup = Channelgroup.create_usergroup(session, ID)
        group = ChannelGroup(
            ChannelGroupId=ID, ChannelGroupEmote=emote, UserGroupId=ugroup.GroupId
        )
        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create Channelgroup `{ID}`.") from e

        await Channelgroup.update_announcement_messages(session, client)

    @staticmethod
    async def create_and_get_group(session: Session, ID: str, emote: str, client: AsyncClient) -> ChannelGroup:
        """
        Create a new ChannelGroup.

        Args:
            session: The database session.
            id: The id of the group.
            emote: the emote of the group.

        Raises:
            DMError: If the group creation fails.

        Returns:
            Channelgroup
        """
        if (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupId == ID)
            .first()
            is not None
        ):
            raise DMError(f"Channelgroup `{ID}` already exists")

        ugroup: UserGroup = Channelgroup.create_usergroup(session, ID)
        group = ChannelGroup(
            ChannelGroupId=ID, ChannelGroupEmote=emote, UserGroupId=ugroup.GroupId
        )
        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create Channelgroup `{ID}`.") from e

        await Channelgroup.update_announcement_messages(session, client)
        return group

    @staticmethod
    def create_usergroup(session: Session, ID: str) -> UserGroup:
        """
        Create a new UserGroup for the subscribers of a ChannelGroup.

        Args:
            session: The database session.
            ID: The id of the group.

        Raises:
            DMError: If the group creation fails.

        Returns:
            Usergroup
        """
        name: str = "ugrp_strgrp" + ID
        group: UserGroup = UserGroup(GroupName=name)

        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create usergroup '{name}'.") from e

        return group

    @staticmethod
    async def delete_group_h(session: Session, group: ChannelGroup, client: AsyncClient) -> None:
        """
        Delete a ChannelGroup.

        Args:
            session: The database session.
            group: The group to delete.

        Raises:
            DMError: If the group deletion fails.

        Returns:
            None
        """
        u_id: int = int(group.UserGroupId)
        try:
            session.query(ChannelGroup).filter(
                ChannelGroup.ChannelGroupId == group.ChannelGroupId
            ).delete()
            session.query(UserGroup).filter(UserGroup.GroupId == u_id).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(
                f"Could not delete Channelgroup `{group.ChannelGroupId}`."
            ) from e

        await Channelgroup.update_announcement_messages(session, client)

    @staticmethod
    async def remove_zulip_channels(
        session: Session,
        channels: list[ZulipChannel],
        group: ChannelGroup,
    ) -> None:
        """
        Remove the channels of a list of channels from a ChannelGroup.

        Args:
            session: The database session.
            channels: A list of channel names
            group: The group in which channels to delete.

        Raises:
            DMError: If a channel deletion fails.

        Returns:
            None
        """
        for channel in channels:
            if (
                session.query(ChannelGroupMember)
                .filter(ChannelGroupMember.Channel == channel.id)
                .filter(ChannelGroupMember.ChannelGroupId == group.ChannelGroupId)
                .first()
                is None
            ):
                continue
            try:
                # search for the listed channels in the db and delete them
                session.query(ChannelGroupMember).filter(
                    ChannelGroupMember.Channel == channel.id
                ).filter(
                    ChannelGroupMember.ChannelGroupId == group.ChannelGroupId
                ).delete()
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                session.rollback()

    @staticmethod
    async def remove_channels_by_id(
        session: Session,
        group: ChannelGroup,
        channel_ids: list[int],
    ) -> None:
        for Id in channel_ids:
            if (
                session.query(ChannelGroupMember)
                .filter(ChannelGroupMember.Channel == Id)
                .filter(ChannelGroupMember.ChannelGroupId == group.ChannelGroupId)
                .first()
                is None
            ):
                continue
            try:
                # search for the listed channels in the db and delete them
                session.query(ChannelGroupMember).filter(
                    ChannelGroupMember.Channel == Id
                ).filter(
                    ChannelGroupMember.ChannelGroupId == group.ChannelGroupId
                ).delete()
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                session.rollback()

    @staticmethod
    def add_zulip_channels(
        session: Session, channels: list[ZulipChannel], group: ChannelGroup
    ) -> None:
        failed: list[str] = []
        for channel in channels:
            if (
                session.query(ChannelGroupMember)
                .filter(ChannelGroupMember.ChannelGroupId == group.ChannelGroupId)
                .filter(ChannelGroupMember.Channel == channel)  # type: ignore
                .first()
            ):
                continue
            try:
                session.add(
                    ChannelGroupMember(
                        ChannelGroupId=group.ChannelGroupId, Channel=channel
                    )
                )
                session.commit()
            except sqlalchemy.exc.IntegrityError:
                session.rollback()
                failed.append(f"#**{channel.name}**")

        if failed:
            s: str = " ".join(failed)
            raise DMError(
                f"Could not add channel(s) {s} to Channelgroup `{group.ChannelGroupId}`."
            )

    @staticmethod
    async def subscribe_h(client: AsyncClient, user_id: int, group_id: str) -> None:
        """
        Subscribe a single user to a ChannelGroup.

        Args:
            client: The AsycClient for API calls.
            user_id: The id of the user.
            group_id: The id of the Channelgroup.

        Returns:
            None
        """
        with DB.session() as session:
            group: ChannelGroup = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupId == group_id)
                .one()
            )
            members: UserGroup = Channelgroup.get_usergroup(session, group)
            sender: ZulipUser = ZulipUser(user_id)
            ZulipUser.set_client(client)
            await sender
            channel_names: list[str] = await Channelgroup.get_channel_names(
                session, [group]
            )

            channels: list[tuple[str, str | None]] = [
                (channel_name, None) for channel_name in channel_names
            ]

            await client.subscribe_users_multiple_channels([user_id], channels)

            Usergroup.add_user_to_group(session, sender, members)

    @staticmethod
    async def unsubscribe_h(client: AsyncClient, user_id: int, group_id: str) -> None:
        """
        Unsubscribe a single user from a ChannelGroup.

        Args:
            client: The AsycClient for API calls.
            user_id: The id of the user.
            group_id: The id of the Channelgroup.

        Returns:
            None
        """
        with DB.session() as session:
            group: ChannelGroup = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupId == group_id)
                .one()
            )
            members: UserGroup = Channelgroup.get_usergroup(session, group)
            sender: ZulipUser = ZulipUser(user_id)
            sender.set_client(client)
            await sender
            channel_names: list[str] = await Channelgroup.get_unique_channel_names(
                session, sender, group
            )

            Usergroup.remove_user_from_group(session, sender, members)

            await client.remove_subscriptions(user_id, channel_names)

    @staticmethod
    async def claim_h(
        group: ChannelGroup | None, session: Session, client: AsyncClient,  message: dict[str, Any], All: bool = False
    ) -> None:
        """
        Make a message "special" for a given group or for all ChannelGroups.

        Args:
            group: The group for which message is claimed.
            session: The database session.
            message_id: The message id of the message that has to be claimed.
            all: Flag wether the message should be claimed by all Channelgroups.

        Raises:
            DMError: If a claiming fails.

        Returns:
            None
        """
        message_id : int = message["id"]

        if not All:
            if group is None:
                raise DMError("No group specified.")

            if (
                session.query(GroupClaim)
                .filter(GroupClaim.MessageId == message_id)
                .filter(GroupClaim.GroupId == group.ChannelGroupId)
                .first()
            ):
                raise DMError(
                    f"Message already claimed by Channelgroup `{group.ChannelGroupId}`."
                )
            try:
                session.add(
                    GroupClaim(MessageId=message_id, GroupId=group.ChannelGroupId)
                )
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(
                    f"Could not claim message '{message_id}' for Channelgroup `{group.ChannelGroupId}`."
                ) from e

            # React with the emoji on the claimed message
            emoji: str = str(group.ChannelGroupEmote)

            await client.send_response(Response.build_reaction(message, emoji=emoji))


        else:
            if (
                session.query(GroupClaimAll)
                .filter(GroupClaimAll.MessageId == message_id)
                .first()
            ):
                raise DMError("Message already claimed by all Channelgroups.")
            try:
                session.add(GroupClaimAll(MessageId=message_id, IsAnnouncement=False))
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(f"Could not claim message '{message_id}'.") from e

            # React with all the emojis on the claimed message
            all_emojis: list[str] = [
            str(group.ChannelGroupEmote) for group in session.query(ChannelGroup).all()
            ]

            for emoji in all_emojis:
                await client.send_response(Response.build_reaction(message, emoji=emoji))

    @staticmethod
    async def unclaim_h(
        group: ChannelGroup | None, session: Session, message_id: int, All: bool = False
    ) -> None:
        """
        Reverts "special status" of a claimed message.

        Args:
            group: The group for which the message is unclaimed.
            session: The database session.
            message_id: The message id of the message that has to be unclaimed.
            all: Flag wether the message should be unclaimed by all Channelgroups.

        Raises:
            DMError: If a unclaiming fails.

        Returns:
            None
        """

        if not All:
            if group is None:
                raise DMError("No group specified.")

            if (
                session.query(GroupClaim)
                .filter(GroupClaim.GroupId == group.ChannelGroupId)
                .filter(GroupClaim.MessageId == message_id)
                .first()
                is None
            ):
                raise DMError(
                    f"Message {message_id} is not in claimed in Channelgroup '{group.ChannelGroupId}'"
                )
            try:
                session.query(GroupClaim).filter(
                    GroupClaim.GroupId == group.ChannelGroupId
                ).filter(GroupClaim.MessageId == message_id).delete()
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(
                    f"Could not unclaim message '{message_id}' in Channelgroup `{group.ChannelGroupId}`."
                ) from e
        else:
            # delete msg from claim_all_db
            try:
                session.query(GroupClaimAll).filter(
                    GroupClaimAll.MessageId == message_id
                ).delete()
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(f"Could not unclaim message '{message_id}'.") from e

            # delete msg from claim_db of every channel
            for g in session.query(ChannelGroup).all():
                if (
                    session.query(GroupClaim)
                    .filter(GroupClaim.GroupId == g.ChannelGroupId)
                    .filter(GroupClaim.MessageId == message_id)
                    .first()
                ):
                    try:
                        session.query(GroupClaim).filter(
                            GroupClaim.GroupId == g.ChannelGroupId
                        ).filter(GroupClaim.MessageId == message_id).delete()
                        session.commit()
                    except sqlalchemy.exc.IntegrityError as e:
                        session.rollback()
                        DMResponse(
                            f"Could not unclaim message '{message_id}' in Channelgroup `{g.ChannelGroupId}`."
                        )

    @staticmethod
    async def announce_h(
        session: Session, message: dict[str, Any], client: AsyncClient
    ) -> None:
        """
        Triggers a (for all groups) claimed announcement message from the bot with a list o all existing Channelgroups.

        Args:
            sender: The ZulipUser sending the message.
            session: The database session.
            message: The message written to the bot.

        Raises:
            DMError: If a anouncment fails.

        Returns:
            None
        """

        announcement_msg: str = Channelgroup._build_announcement_message(session)

        # Remove the requesting message.
        response = await client.delete_message(message["id"])

        if response["result"] != "success":
            raise DMError("Could not delete message.")

        # Send own message.
        botMessage: dict[str, Any] = await client.send_response(
            Response.build_message(message, announcement_msg)
        )
        if botMessage["result"] != "success":
            raise DMError("Could not announce.")

        # Insert the id of the bots message into the database.
        Id = botMessage["id"]
        try:
            session.add(GroupClaimAll(MessageId=Id, IsAnnouncement=True))
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not claim message '{ Id }'.") from e

        # Get all the currently existing emojis.
        all_emojis: list[str] = [
            str(group.ChannelGroupEmote) for group in session.query(ChannelGroup).all()
        ]

        if not all_emojis:
            raise DMError()

        # React with all those emojis on this message.
        for emoji in all_emojis:
            await client.send_response(Response.build_reaction(botMessage, emoji=emoji))

    @staticmethod
    def _build_announcement_message(session: Session) -> str:
        """
        Creates the comtent of an announcement message.
        """

        table_names: str = ""
        table_sep: str = "---- "
        table_emojis: str = ""

        for group in session.query(ChannelGroup).all():
            table_names += f"| {group.ChannelGroupId} "
            table_sep += "| ----- "
            table_emojis += f"| :{group.ChannelGroupEmote}:"

        _announcement_msg: str = cleandoc(
            """
                Hi! :bothappypad:
                I have the pleasure to announce some channel groups here.
                You may subscribe to a channel group in order to be automatically \
                subscribed to all channels belonging to that group. Also, you \
                will be kept updated when new channels are added to the group.
                Just react to this message with the emoji of the channel group \
                you like to subscribe to. Remove your emoji to unsubscribe \
                from this group. (1)

                | channel group {}
                {}
                | emoji {}


                *to be continued*

                In case the emojis do not work for you, you may write me a PM:
                - `group subscribe <group_id>`
                - `group unsubscribe <group_id>`

                
                Have a nice day! :sunglasses:

                (1) Note that this will also unsubscribe you from the existing \
                channels of this group. If you only want to cancel the \
                subscription without being unsubscribed from existing channels, \
                just write me a PM:
                - `group unsubscribe -k <group_id>`
                """
        )

        return _announcement_msg.format(table_names, table_sep, table_emojis)

    @staticmethod
    async def unannounce_h(
        session: Session, message_id: int, client: AsyncClient
    ) -> None:
        """
        Reverts "special status" of a announced message.

        Args:
            sender: The ZulipUser sending the message.
            session: The database session.
            message_id: The message id of the message that has to be unannounced

        Raises:
            DMError: If a unannouning fails.

        Returns:
            None
        """

        if (
            session.query(GroupClaimAll)
            .filter(GroupClaimAll.MessageId == message_id)
            .first()
            is None
        ):
            raise DMError(f"Message '{message_id}' is not yet claimed.")
        try:
            session.query(GroupClaimAll).filter(
                GroupClaimAll.MessageId == message_id
            ).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not unclaim message '{message_id}'.") from e

        await client.delete_message(message_id)

    @staticmethod
    async def fix_h(client: AsyncClient, session: Session, group: ChannelGroup) -> None:
        """
        Makes sure that every subscriber of the given group is subscribed to all channels of this group.

        Args:
            sender: ZulipUser sending the message
            session: The database session.
            group: The ChannelGroup to fix.

        Raises:
            DMError: If a fixing fails.

        Returns:
            None
        """
        ugroup: UserGroup = Channelgroup.get_usergroup(session, group)
        user_ids: list[int] = Usergroup.get_user_ids_for_group(session, ugroup)
        channel_names: list[str] = await Channelgroup.get_channel_names(
            session, [group]
        )

        channels: list[tuple[str, str | None]] = [
            (channel_name, None) for channel_name in channel_names
        ]

        await client.subscribe_users_multiple_channels(user_ids, channels)

    @staticmethod
    async def fix_all_h(client: AsyncClient, session: Session) -> None:
        """
          Fixing all Channels of all ChannelGroups

        Args:
            sender: ZulipUser sending the message
            session: The database session.

        Raises:
            DMError: If a fixing fails.

        Returns:
            None
        """
        groups: list[ChannelGroup] = session.query(ChannelGroup).all()

        for group in groups:
            await Channelgroup.fix_h(client, session, group)

    # ========================================================================================================================
    #       HELPER METHODS
    # ========================================================================================================================

    @staticmethod
    def get_group_id_from_emoji_event(emoji: str) -> str | None:
        """
        Get the identifier of a Channelgroup by an emoji name.
        Returns None if given emoji is not associated with any ChannelGroup.
        """
        result: str | None = None
        with DB.session() as session:
            sg: ChannelGroup | None = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupEmote == emoji)
                .one_or_none()
            )
            if sg:
                result = str(sg.ChannelGroupId)
        return result

    @staticmethod
    def get_group_ids_from_channel_id(Id: int) -> list[str]:
        """
        Get a list of all ChannelGroup-identifiers that a channel is a member in.
        Returns empty list if channel is not member of any ChannelGroup.
        """
        result: set[str]
        with DB.session() as session:
            result = {
                str(sg.ChannelGroupId)
                for sg in session.query(ChannelGroupMember)
                .filter(ChannelGroupMember.Channel == Id)
                .all()
            }
        return list(result)

    @staticmethod
    def get_group_subscribers(groups: list[str]) -> list[int]:
        """
        Get a list of all User-IDs of the subscribers from all ChannelGroups in a given list of ChannelGroup-identifiers.
        Returns empty list either when there are no ChannelGroups associated with the given identifiers or if there are no subscribers for in the ChannelGroups.
        """
        result: list[int] = []
        with DB.session() as session:
            for g_id in groups:
                u_group: UserGroup = Channelgroup.get_usergroup_by_id(session, g_id)
                res: list[int] = Usergroup.get_user_ids_for_group(session, u_group)
                result.extend(res)
        return result

    @staticmethod
    def message_is_claimed(msg_id: int, em: str) -> bool:
        """
        Decides whether the message of a given message-id is in any form claimed
        (either by all Channelgroups or by the Channelgroup associated with a given emote).
        """
        claimedByOne: bool
        claimedByAll: bool
        group_id: str | None = Channelgroup.get_group_id_from_emoji_event(em)

        if group_id is None:
            return False

        with DB.session() as session:
            claimedByOne = (
                session.query(GroupClaim)
                .filter(GroupClaim.MessageId == msg_id)
                .filter(GroupClaim.GroupId == group_id)
                .first()
                is not None
            )
            claimedByAll = (
                session.query(GroupClaimAll)
                .filter(GroupClaimAll.MessageId == msg_id)
                .first()
                is not None
            )
        return claimedByOne or claimedByAll

    @staticmethod
    async def get_channel_names(
        session: Session, groups: list[ChannelGroup]
    ) -> list[str]:
        """
        Get a list of the names of all channels that are members at least one of the Channelgroups in a list of ChannelGroups.
        """
        channels: set[str] = set()

        for group in groups:
            for s in (
                session.query(ChannelGroupMember)
                .filter(ChannelGroupMember.ChannelGroupId == group.ChannelGroupId)
                .all()
            ):
                chan : ZulipChannel = cast(ZulipChannel,s.Channel)
                await chan
                channels.add(chan.name)
        return list(channels)

    @staticmethod
    async def get_channels(session: Session, group: ChannelGroup) -> list[ZulipChannel]:
        """
        Get a list of all channels that are members of a given Channelgroup.
        """
        channels: set[ZulipChannel] = set()
        for s in (
            session.query(ChannelGroupMember)
            .filter(ChannelGroupMember.ChannelGroupId == group.ChannelGroupId)
            .all()
        ):
            chan : ZulipChannel = cast(ZulipChannel,s.Channel)
            await chan
            channels.add(chan)

        return list(channels)

    @staticmethod
    async def get_unique_channel_names(
        session: Session, user: ZulipUser, group: ChannelGroup
    ) -> list[str]:
        """
        Get a list of the names of all channels that are members only in a given ChannelGroup and not in any other ChannelGroup.
        """
        groups: list[ChannelGroup] = Channelgroup.get_groups_for_user(session, user)
        groups.remove(group)

        channelsToKeep: list[str] = await Channelgroup.get_channel_names(
            session, groups
        )

        channels: list[str] = []
        for s in (
            session.query(ChannelGroupMember)
            .filter(ChannelGroupMember.ChannelGroupId == group.ChannelGroupId)
            .all()
        ):
            result = cast(ZulipChannel,s.Channel)
            await result
            name: str = result.name
            channels.append(name)
        return [channel for channel in channels if channel not in channelsToKeep]

    @staticmethod
    def get_usergroup(session: Session, group: ChannelGroup) -> UserGroup:
        """
        Get the UserGroup for the subscribers of a given ChannelGroup.
        """
        s: ChannelGroup = (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupId == group.ChannelGroupId)
            .one()
        )
        Id: int = int(s.UserGroupId)
        return session.query(UserGroup).filter(UserGroup.GroupId == Id).one()

    @staticmethod
    def get_usergroup_by_id(session: Session, group_id: str) -> UserGroup:
        """
        Get the UserGroup for the subscribers of a ChannelGroup, given its ChannelGroup-identifier.
        """
        s: ChannelGroup = (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupId == group_id)
            .one()
        )
        Id: int = int(s.UserGroupId)
        return session.query(UserGroup).filter(UserGroup.GroupId == Id).one()

    @staticmethod
    def get_groups_for_user(session: Session, user: ZulipUser) -> list[ChannelGroup]:
        """
        Get a list of ChannelGroups that a given user is subscribed to.
        """
        ug_ids: list[int] = [
            int(ugroup.GroupId)
            for ugroup in Usergroup.get_groups_for_user(session, user)
        ]

        result: list[ChannelGroup] = []

        for Id in ug_ids:
            s = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.UserGroupId == Id)
                .one_or_none()
            )
            if s:
                result.append(s)

        return result

    @staticmethod
    async def update_announcement_messages(
        session: Session, client: AsyncClient,
    ) -> None:
        """
        Updates the content of an announcement message.
        """
        new_content: str = Channelgroup._build_announcement_message(session)

        msg_ids: list[int] = [
            int(claim.MessageId)
            for claim in session.query(GroupClaimAll).all() if bool(claim.IsAnnouncement)
        ]

        for msg_id in msg_ids:
            response = await client.edit_message(msg_id, new_content)

            if response["result"] != "success":
                raise DMError("Could not update message.")
