#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from collections.abc import Iterable as IterableClass
from inspect import cleandoc
import re
from sqlite3 import IntegrityError
from typing import cast, Any, Callable, Iterable
from sqlalchemy import Column, String, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.ext.hybrid import hybrid_property
from tumcsbot.lib.regex import Regex
# from zulip import ZulipStream

from tumcsbot.lib.response import Response
from tumcsbot.plugin import Event, Plugin, PluginCommandMixin
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import DB, TableBase
from tumcsbot.plugin_decorators import *
from tumcsbot.plugins.usergroup import UserGroup
from tumcsbot.plugins.usergroup import Usergroup
from tumcsbot.lib.types import (
    DMError,
    DMMessage,
    DMResponse,
    PartialError,
    PartialSuccess,
    Privilege,
    UserNotPrivilegedException,
    response_type,
    ZulipUser,
    ZulipStream,
    YAMLSerializableMixin,
)

class StreamGroup(TableBase):
    """Represents a StreamGroup in the system."""

    __tablename__ = 'StreamGroups'

    StreamGroupId = Column(String, primary_key=True)
    StreamGroupEmote = Column(String, nullable=False, unique=True)
    UserGroupId = Column(Integer, ForeignKey("UserGroups.GroupId", ondelete="CASCADE"),nullable=False)
    
    _streams = relationship(
        "StreamGroupMember", 
        back_populates="groups",
        cascade="all, delete-orphan"
    )

    _usergroup = relationship(
        "UserGroup",
        cascade="all, delete-orphan",
        back_populates="_streamgroup",
        single_parent=True
    )

    @hybrid_property
    def streams(self) -> list[ZulipStream]:
        return [member.Stream for member in self._streams]
           
    @hybrid_property
    def usergroup(self) -> UserGroup:
        return self._usergroup


class StreamGroupMember(TableBase):
    """Represents a StreamGroup member (Stream) in the system."""

    __tablename__ = 'StreamGroupMembers'

    StreamGroupId = Column(String, ForeignKey('StreamGroups.StreamGroupId', ondelete='CASCADE'), primary_key=True)
    Stream = Column(ZulipStream, primary_key=True)

    groups: Mapped[list["StreamGroup"]] = relationship(viewonly=True, back_populates="_streams")

    

class GroupClaim(TableBase):
    """Represents a message being claimed in a specific StreamGroup in the system."""

    __tablename__ = 'GroupClaims'

    MessageId = Column(Integer, primary_key=True)
    GroupId = Column(String, ForeignKey('StreamGroups.StreamGroupId', ondelete='CASCADE'), primary_key=True)


class GroupClaimAll(TableBase):
    """Represents a message being claimes in all StreamGroups in the system."""
   
    __tablename__ = 'GroupClaimsAll'

    MessageId = Column(Integer, primary_key=True)

    # Define the constraint to ensure MessageId is unique across all GroupClaims tables
    # UniqueConstraint('MessageId', name='uq_group_claims_all_message_id')

class Streamgroup(PluginCommandMixin, Plugin):
    """
    Manage SteamGroups.
    """

    async def handle_event(self, event: Event) -> Response | Iterable[Response]:
        if event.type == "reaction":
            return await self.handle_reaction_event(event.data)
        if event.data["type"] == "stream":
            return await self.handle_stream_event(event.data)
        return await self.handle_message(event.data["message"])

    @command(name="list")
    @privilege(Privilege.USER)
    @opt("a",long_opt="all",description="Display all existing Streamgroups.")
    async def _list(
        self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List all your Streamgroups with their associated patterns.
        """
        response: str = (
            "Group Id | Emoji | Streams | Claimed Msg\n---- | ---- | ---- | ----"
        )

        message_link: str = (
            "[{0}](" + sender.client.base_url[:-4] + "#narrow/id/{0})"
        )

        groups: list[StreamGroup]
        if opts.a:
            groups = session.query(StreamGroup).all()
        else:
            groups = Streamgroup._get_groups_for_user(session,sender)
            response = sender.mention_silent + " is in the following Streamgroups:\n\n" + response

        if len(groups) == 0:
            raise DMError(f"No stream groups found")
       
        for group in groups:
            group_id = group.StreamGroupId
            emoji = group.StreamGroupEmote
            streams: list[str] = await Streamgroup._get_stream_names(session,sender,group)
                
            streams_concat: str = ", ".join(f"`{s}`" for s in streams)
            claims: str = ", ".join(
                [
                    message_link.format(claim.MessageId) 
                    for claim in session.query(GroupClaim).filter(GroupClaim.GroupId==group.StreamGroupId).all()
                ]
            )
            response += (
                f"\n{group_id} | {emoji} :{emoji}: | {streams_concat} | {claims}"
            )

        response += "\n\nMessages claimed for all groups: \n" + ", ".join(
            message_link.format(claim.MessageId)
            for claim in session.query(GroupClaimAll).all()
        )

        yield DMResponse(response)
    

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", int, description="The id of the Streamgroup to add.")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the reaction.")
    async def create(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Create a new Streamgroup.
        """
        id: int = args.group_id
        emoji_name: str | None = args.emoji 
        
        Streamgroup._create_group(session,id,emoji_name)
        yield DMResponse(f"Streamgroup `{id}` created")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup to delete.")
    async def delete(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Delete a StreamGroup.
        """
        group: StreamGroup = args.group_id

        Streamgroup._delete_group(session,group)
        yield DMResponse(f"Streamgroup `{group.StreamGroupId}` deleted")

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of a Streamgroup to add streams to.")
    @arg("streams", str, description="The stream patterns to add.", greedy=True)
    async def add_streams(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add streams to a Streamgroup.
        """
        group: StreamGroup = args.group_id
        stream_patterns: list[str] = args.streams

        await Streamgroup._add_streams(session,sender,group,stream_patterns)
        yield DMResponse(f"Added streams to Streamgroup `{group.StreamGroupId}`.")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of a Streamgroup to remove streams from.")
    @arg("streams", str, description="The stream patterns to remove.", greedy=True)
    async def remove_streams(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Remove streams from a Streamgroup.
        """   
        group: StreamGroup = args.group_id
        stream_patterns: list[str] = args.streams

        await Streamgroup._remove_streams(session,sender,group,stream_patterns)
        yield DMResponse(f"Removed streams from Streamgroup `{group.StreamGroupId}`.")
    
    @command
    @privilege(Privilege.USER)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup to subscribe to.")
    async def subscribe(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe to a Streamgroup.
        """
        group: StreamGroup = args.group_id
        members: UserGroup = Streamgroup._get_usergroup(session,group)
        user_id:list[int] = [sender.id]
        stream_names: list[str] = await Streamgroup._get_stream_names(session, sender, group)

        streams: list[(str,None)] = [
            (stream_name, None) for stream_name in stream_names
        ]

        await sender.client.subscribe_users_multiple_streams(user_id,streams)

        Usergroup.add_user_to_group(session, sender, members)

        yield DMResponse(f"You have subscribed to the Streamgroup `{group.StreamGroupId}`")

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup the users shoud get subscribed to.")
    @arg("user", ZulipUser, "The users that should get subscribed to the Streamgroup.", greedy=True)
    async def subscribe_users(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe a list of users to a Streamgroup.
        """
        group: StreamGroup = args.group_id
        members: UserGroup = Streamgroup._get_usergroup(session,group)
        users: list[ZulipUser] = args.user
        user_ids:list[int] = [
            user.id for user in users
        ]
        stream_names: list[str] = await Streamgroup._get_stream_names(session, sender, group)

        streams: list[(str,None)] = [
            (stream_name, None) for stream_name in stream_names
        ]

        await sender.client.subscribe_users_multiple_streams(user_ids,streams)

        for user in users:
            Usergroup.add_user_to_group(session, user, members)

        yield DMResponse(f"You have subscribed the users to the Streamgroup `{group.StreamGroupId}`")

    @command
    @privilege(Privilege.ADMIN)
    @arg("streamgroup_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup the users shoud get subscribed to.")
    @arg("usergroup", UserGroup.GroupName, "The name of the usergroup that should get subscribed to the Streamgroup.")
    async def subscribe_usergroup(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe a usergroup to a Streamgroup.
        """
        group: StreamGroup = args.streamgroup_id
        members: UserGroup = Streamgroup._get_usergroup(session,group)
        ugroup: UserGroup = args.usergroup
        users: list[ZulipUser] = Usergroup.get_users_for_group(session,ugroup)
        user_ids:list[int] = Usergroup.get_user_ids_for_group(session,ugroup)
        stream_names: list[str] = await Streamgroup._get_stream_names(session, sender, group)

        streams: list[(str,None)] = [
            (stream_name, None) for stream_name in stream_names
        ]

        await sender.client.subscribe_users_multiple_streams(user_ids,streams)

        for user in users:
            Usergroup.add_user_to_group(session, user, members)

        yield DMResponse(f"You have subscribed the users to the Streamgroup `{group.StreamGroupId}`")

    @command
    @privilege(Privilege.USER)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup to unsubscribe from.")
    @opt("t", long_opt="total", description="Unsubscribe from all streams of this group. (default)")
    @opt("k", long_opt="keep",description="Keep the streams of this group you are currently subscribed to.")
    async def unsubscribe(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unsubscribe from a Streamgroup.
        """
        group: StreamGroup = args.group_id
        members: UserGroup = Streamgroup._get_usergroup(session,group)
        user_id:list[int] = sender.id
        stream_names: list[str] = await Streamgroup._get_stream_names(session, sender, group)

        if opts.k and opts.t:
            raise DMError("The `-k` and `-t` flags are mutually exclusive, see `help streamgroup`.")
        
        Usergroup.remove_user_from_group(session, sender, members)

        if opts.t or not opts.k:
            await sender.client.remove_subscriptions(user_id, stream_names)

        yield DMResponse(f"You have unsubscribed from the Streamgroup `{group.StreamGroupId}`")

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup the users shoud get unsubscribed from.")
    @arg("user", ZulipUser, "The users that should get unsubscribed from the Streamgroup", greedy=True)
    @opt("t", long_opt="total", description="Unsubscribe the users from all streams of this group. (default)")
    @opt("k", long_opt="keep",description="Unsubscribe the users from the Streamgroup, but keep the individual subscriptions for the user.")
    async def unsubscribe_users(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unsubscribe a list of users from a Streamgroup.
        """
        group: StreamGroup = args.group_id
        members: UserGroup = Streamgroup._get_usergroup(session,group)
        users: list[ZulipUser] = args.user
        user_ids:list[int] = [
            user.id for user in users
        ]
        stream_names: list[str] = await Streamgroup._get_stream_names(session, sender, group)

        if opts.k and opts.t:
            raise DMError("The `-k` and `-t` flags are mutually exclusive, see `help streamgroup`.")
        
        for user in users:
            Usergroup.remove_user_from_group(session, user, members)

        if opts.t or not opts.k:
            for id in user_ids:
                await sender.client.remove_subscriptions(id, stream_names)

        yield DMResponse(f"You have unsubscribed the users from the streamgroup `{group.StreamGroupId}`")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("streamgroup_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup the users shoud get unsubscribed from.")
    @arg("usergroup", ZulipUser, "The name of the usergroup that should get unsubscribed from the Streamgroup")
    @opt("t", long_opt="total", description="Unsubscribe the users from all streams of this group. (default)")
    @opt("k", long_opt="keep",description="Unsubscribe the users from the Streamgroup, but keep the individual subscriptions for the user.")
    async def unsubscribe_usergroup(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unsubscribe a usergroup from a Streamgroup.
        """
        group: StreamGroup = args.streamgroup_id
        members: UserGroup = Streamgroup._get_usergroup(session,group)
        ugroup: UserGroup = args.usergroup
        users: list[ZulipUser] = Usergroup.get_users_for_group(session,ugroup)
        user_ids:list[int] = Usergroup.get_user_ids_for_group(session,ugroup)
        stream_names: list[str] = await Streamgroup._get_stream_names(session, sender, group)

        if opts.k and opts.t:
            raise DMError("The `-k` and `-t` flags are mutually exclusive, see `help streamgroup`.")
        
        for user in users:
            Usergroup.remove_user_from_group(session, user, members)

        if opts.t or not opts.k:
            for id in user_ids:
                await sender.client.remove_subscriptions(id, stream_names)

        yield DMResponse(f"You have unsubscribed the users from the streamgroup `{group.StreamGroupId}`")


    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of the Streamgroup to fix.", optional=True)
    @opt("a", long_opt="all", description="Fix subscriptions for all existing Streamgroups.")
    async def fix(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Makes sure every subscriber of the given Streamgroup is subscribed to all streams of this group. 
        """
        group:StreamGroup | None = args.group_id

        if opts.a:
            await Streamgroup._fix_all(sender, session)
        else:
            if group is None:
                raise DMError("Missing `group_id` of Streamgroup, see `help streamgroup`.")
            await Streamgroup._fix(sender, session, group)

        yield ReactionResponse("ok")

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", StreamGroup.StreamGroupId, description="The id of a Streamgroup for which to claim message.",optional=True)
    @opt("a",long_opt="all",description="Claim message in all existing Streamgroups.")
    async def claim(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """ 
        Make the message, written in a stream and adressed to @**TUMCSBot**, "special" for a given Streamgroup.
        If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, the user gets subscribed to all streams belonging to this group 
        """
        group: StreamGroup | None = args.group_id
        msg_id:int = message["id"]

        if not opts.a and not group:
             raise DMError("Either argument `group_id` or flag `-a` necessary.")
        if opts.a and group:
             raise DMError("The argument `group_id` and flag `-a` are mutually exclusive.")

        if message["type"] != "stream":
            raise DMError("Claim only stream messages.")
        
        stream = await sender.client.get_stream_by_id(message["stream_id"])
        name = stream["name"]
            
        await Streamgroup._claim(group, session, msg_id, all=opts.a)

        if not opts.a:
            resp = f"Claimed message {msg_id} in #**{name}** for Streamgroup `{group.StreamGroupId}`."
        else:
            resp = f"Claimed message {msg_id} in #**{name}** for all Streamgroups."

        yield DMMessage(sender, resp)

    @command
    @privilege(Privilege.ADMIN)
    @arg("message_id", int, description="The id of the message to claim.")
    @arg("group_id", int, description="The id of a Streamgroup for which to claim message.",optional=True)
    @opt("a",long_opt="all",description="Claim message in all existing Streamgroups.")
    async def claim_message(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """ 
        Make a specified message "special" for a given Streamgroup.
        If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, the user gets subscribed to all streams belonging to this group 
        """
        if not opts.a:
            group_id: int  = args.group_id
            msg_id: int = args.message_id


            if not group_id:
                raise DMError("Either argument `group_id` or flag `-a` necessary.")
            
            group:StreamGroup | None= session.query(StreamGroup).filter(StreamGroup.StreamGroupId==group_id).one_or_none()
            if not group:
                raise DMError(f"Uuups, it looks like i could not find any Streamgroup associated with `{group_id}` :botsad:")

        else:
            group_id: int  = args.group_id
            msg_id: int = args.message_id

            if group_id:
                raise DMError("The argument `group_id` and flag `-a` are mutually exclusive.")
            
            group = None

        msg = await sender.client.get_message_by_id(msg_id)

        if msg["type"] != "stream":
            raise DMError("Claim only stream messages.")
        
        stream = await sender.client.get_stream_by_id(msg["stream_id"])
        name = stream["name"]
            
        await Streamgroup._claim(group=group, session=session, message_id=msg_id,all=opts.a)
        
        if not opts.a:
            resp = f"Claimed message {msg_id} in #**{name}** for Streamgroup `{group.StreamGroupId}`."
        else:
            resp = f"Claimed message {msg_id} in #**{name}** for all Streamgroups."

        yield DMResponse(resp)


    @command
    @privilege(Privilege.ADMIN)
    @arg("message_id", int, description="The id of the message to unclaim.")
    @arg("group_id", int, description="The id of a Streamgroup for which to unclaim message.",optional=True)
    @opt("a",long_opt="all",description="Unclaim message for all existing Streamgroups.")
    async def unclaim_message(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Reverts "special" status of a claimed message. 
        """
        if not opts.a:
            group_id: int  = args.group_id
            msg_id: int = args.message_id


            if not group_id:
                raise DMError("Either argument `group_id` or flag `-a` necessary.")
            
            group:StreamGroup | None= session.query(StreamGroup).filter(StreamGroup.StreamGroupId==group_id).one_or_none()
            if not group:
                raise DMError(f"Uuups, it looks like i could not find any Streamgroup associated with `{group_id}` :botsad:")

        else:
            group_id: int  = args.group_id
            msg_id: int = args.message_id

            if group_id:
                raise DMError("The argument `group_id` and flag `-a` are mutually exclusive.")
            
            group = None

        msg = await sender.client.get_message_by_id(msg_id)

        if msg["type"] != "stream":
            raise DMError("Unclaim only stream messages.")
        
        stream = await sender.client.get_stream_by_id(msg["stream_id"])
        name = stream["name"]
            
        await Streamgroup._unclaim(group=group, session=session, message_id=msg_id,all=opts.a)
        
        if not opts.a:
            resp = f"Unlaimed message {msg_id} in #**{name}** for Streamgroup `{group.StreamGroupId}`."
        else:
            resp = f"Unlaimed message {msg_id} in #**{name}** for all Streamgroups."

        yield DMResponse(resp)

    @command
    @privilege(Privilege.ADMIN)
    async def announce(self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        By writing the message in a stream adressed to @**TUMCSBot**, a "special" message from the bot for all Streamgroups with a list of all existing groups is triggered. 
        """

        if message["type"] != "stream":
            raise DMError("Claim only stream messages.")
        
        await Streamgroup._announce(sender, session, message)

        stream = await sender.client.get_stream_by_id(message["stream_id"])
        name = stream["name"]
        yield DMMessage(sender,f"Announced message in Stream #**{name}**.")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("message_id", int, description="The id of the message to unannounce.")
    async def unannounce(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Reverts "special" status of an announced message. 
        """
        msg_id: int = args.message_id

        msg = await sender.client.get_message_by_id(msg_id)
        stream = await sender.client.get_stream_by_id(msg["stream_id"])
        name = stream["name"]

        await Streamgroup._unannounce(sender, session, msg_id)

        yield DMResponse(f"Unannounced message in Stream #**{name}**.")
    

    # =============== CLASS METHODS =============================================================================
    @staticmethod
    def _create_group(session: Session, id: int, emote:str) -> None:
        """
        Create a new StreamGroup.

        Args:
            session: The database session.
            id: The id of the group.
            emote: the emote of the group.
        Raises:
            DMError: If the group creation fails.

        Returns:
            None
        """
        if (
            session.query(StreamGroup).filter(StreamGroup.StreamGroupId == id).first()
            is not None
        ):
            raise DMError(f"Streamgroup '{id}' already exists")

        ugroup:UserGroup = Streamgroup._create_usergroup(session,id)
        group = StreamGroup(StreamGroupId=id,StreamGroupEmote=emote,UserGroupId=ugroup.GroupId)
        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create Streamgroup '{id}'. {str(e)}") from e

    @staticmethod
    def _create_usergroup(session:Session, id:int) -> UserGroup:
        name:str = "ugrp_strgrp" + str(id)
        group:UserGroup= UserGroup(GroupName=name)

        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create usergroup '{name}'. {str(e)}") from e
        
        return group

    @staticmethod
    def _delete_group(session: Session, group: StreamGroup) -> None:
        """
        Delete a StreamGroup.

        Args:
            session: The database session.
            group: The group to delete.

        Raises:
            DMError: If the group deletion fails.

        Returns:
            None
        """
        u_id: int = group.UserGroupId
        try:
            session.query(StreamGroup).filter(StreamGroup.StreamGroupId == group.StreamGroupId).delete()
            session.query(UserGroup).filter(UserGroup.GroupId == u_id).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not delete Streamgroup '{group.StreamGroupId}'. {str(e)}") from e
        

    @staticmethod
    async def _remove_streams(session: Session, sender:ZulipUser, group:StreamGroup,stream_patterns:list[str]):
        """
        Remove the streams of a list of stream patterns from a StreamGroup.

        Args:
            session: The database session.
            sender: The sender of the message
            group: The group in which streams to delete.
            stream_patterns: A list of stream-regexes 

        Raises:
            DMError: If a stream deletion fails.

        Returns:
            None
        """
        for stream_reg in stream_patterns:
            streams:list[str] = await sender.client.get_streams_from_regex(stream_reg)

            for s in streams:
                stream:ZulipStream = ZulipStream(f"#**{s}**")
                await stream

                if session.query(StreamGroupMember).filter(StreamGroupMember.Stream == stream).filter(StreamGroupMember.StreamGroupId == group.StreamGroupId).first() is None:
                    continue
                try:
                    # search for the listed streams in the db and delete them
                    session.query(StreamGroupMember).filter(StreamGroupMember.Stream == stream).filter(StreamGroupMember.StreamGroupId == group.StreamGroupId).delete()
                    session.commit()
                except sqlalchemy.exc.IntegrityError as e:
                    session.rollback()
                    raise DMError(f"Could not delete stream #**{stream.name}** from Streamgroup '{group.StreamGroupId}'. {str(e)}")
                
    @staticmethod
    async def _add_streams(session: Session, sender:ZulipUser, group:StreamGroup,stream_patterns:list[str]):
        """
        Add the streams of a list of stream patterns to a StreamGroup.

        Args:
            session: The database session.
            sender: The sender of the message
            group: The group in which streams to add.
            stream_patterns: A list of stream-regexes 

        Raises:
            DMError: If a stream addition fails.

        Returns:
            None
        """
        
        for stream_reg in stream_patterns:
            streams:list[str] = await sender.client.get_streams_from_regex(stream_reg)

            for s in streams:
                stream:ZulipStream = ZulipStream(f"#**{s}**")
                await stream

                if session.query(StreamGroupMember).filter(StreamGroupMember.StreamGroupId==group.StreamGroupId).filter(StreamGroupMember.Stream == stream).first():
                    continue
                try:
                    session.add(StreamGroupMember(StreamGroupId=group.StreamGroupId,Stream=stream))
                    session.commit()
                except sqlalchemy.exc.IntegrityError as e:
                    session.rollback()
                    raise DMError( f"Could not add stream #**{s}** to Streamgroup '{group.StreamGroupId}'.")

    @staticmethod
    async def _claim(group:StreamGroup|None, session:Session, message_id:int, all=False):
        """
          Make a message "special" for a given group. 
          If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, 
          the user gets subscribed to all streams belonging to this group.
        

        Args:
            group: The group for which message is claimed.
            session: The database session.
            message_id: The message id of the message that has to be claimed
            all: Flag wether the message should be claimed by all streamgroups
            
        Raises:
            DMError: If a claiming fails.

        Returns:
            None
        """
        
        if not all:
            if session.query(GroupClaim).filter(GroupClaim.MessageId==message_id).filter(GroupClaim.GroupId==group.StreamGroupId).first():
                raise DMError(f"Message already claimed by Streamgroup {group.StreamGroupId}.")
            try: 
                session.add(GroupClaim(MessageId=message_id,GroupId=group.StreamGroupId))
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(f"Could not claim message '{message_id}' for Streamgroup `{group.StreamGroupId}`. {str(e)}")
        else:
            if session.query(GroupClaimAll).filter(GroupClaimAll.MessageId==message_id).first():
                raise DMError("Message already claimed by all Streamgroups.")
            try: 
                session.add(GroupClaimAll(MessageId=message_id))
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(f"Could not claim message '{message_id}'. {str(e)}")

    @staticmethod
    async def _unclaim(group:StreamGroup,session:Session,message_id:int,all:bool=False):
        """
          Reverts "special status" of a claimed message.

        Args:
            group: The group for which the message is unclaimed.
            session: The database session.
            message_id: The message id of the message that has to be unclaimed

        Raises:
            DMError: If a unclaiming fails.

        Returns:
            None
        """

        if not all:
            if session.query(GroupClaim).filter(GroupClaim.GroupId == group.StreamGroupId).filter(GroupClaim.MessageId == message_id).first() is None:
                raise DMError(f"Message {message_id} is not in claimed in Streamgroup '{group.StreamGroupId}'")
            try:
                session.query(GroupClaim).filter(GroupClaim.GroupId==group.StreamGroupId).filter(GroupClaim.MessageId == message_id).delete()
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(f"Could not unclaim message {message_id} in Streamgroup `{group.StreamGroupId}`.") from e
        else:
            # delete msg from claim_all_db
            try: 
                session.query(GroupClaimAll).filter(GroupClaimAll.MessageId==message_id).delete()
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                raise DMError(f"Could not unclaim message '{message_id}'. {str(e)}")
            
            # delete msg from claim_db of every stream
            for g in session.query(StreamGroup).all():
                if session.query(GroupClaim).filter(GroupClaim.GroupId == g.StreamGroupId).filter(GroupClaim.MessageId == message_id).first():
                    try:
                        session.query(GroupClaim).filter(GroupClaim.GroupId==g.StreamGroupId).filter(GroupClaim.MessageId == message_id).delete()
                        session.commit()
                    except sqlalchemy.exc.IntegrityError as e:
                        session.rollback()
                        DMResponse(f"Could not unclaim message {message_id} in Streamgroup `{g.StreamGroupId}`.")

    @staticmethod
    async def _announce(sender:ZulipUser, session:Session, message):
        """
           Triggers a message from the bot which will be "special" for all groups 
           and in which the bot will maintain a list of all groups.

        Args:
            sender: ZulipUser sending the message
            session: The database session.
            message: The message written to the best

        Raises:
            DMError: If a anouncment fails.

        Returns:
            None
        """
        
        announcement_msg: str = Streamgroup._build_announcement_message(session)

        # Remove the requesting message.
        await sender.client.delete_message(message["id"])

        # Send own message.
        botMessage: dict[str, Any] = await sender.client.send_response(
            Response.build_message(message, announcement_msg)
        )
        if botMessage["result"] != "success":
            raise DMError("Could not announce.")

        # Insert the id of the bots message into the database.
        id = botMessage["id"]
        try: 
            session.add(GroupClaimAll(MessageId=id))
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not claim message '{ id }'.")

        # Get all the currently existing emojis.
        all_emojis: list[str] = [
            group.StreamGroupEmote for group in session.query(StreamGroup).all()
        ]

        if not all_emojis:
            raise DMError()

        # React with all those emojis on this message.
        for emoji in all_emojis:
           await sender.client.send_response(
               Response.build_reaction( botMessage, emoji=emoji )
           )
           
                
    
    @staticmethod
    def _build_announcement_message(session:Session) -> str:
            _announcement_msg_table_row_fmt: str = "%s | :%s:"
            _announcement_msg: str = cleandoc(
        """
        Hi! :smile:

        I have the pleasure to announce some stream groups here.
        You may subscribe to a stream group in order to be automatically \
        subscribed to all streams belonging to that group. Also, you \
        will be kept updated when new streams are added to the group.
        Just react to this message with the emoji of the stream group \
        you like to subscribe to. Remove your emoji to unsubscribe \
        from this group. (1)

        stream group | emoji
        ------------ | -----
        {}


        *to be continued*

        In case the emojis do not work for you, you may write me a PM:
        - `group subscribe <group_id>`
        - `group unsubscribe <group_id>`

        
        Have a nice day! :sunglasses:

        (1) Note that this will also unsubscribe you from the existing \
        streams of this group. If you only want to cancel the \
        subscription without being unsubscribed from existing streams, \
        just write me a PM:
        - `group unsubscribe -k <group_id>`
        """
    )

            table: str = "\n".join(
                _announcement_msg_table_row_fmt % (group.StreamGroupId, group.StreamGroupEmote)
                for group in session.query(StreamGroup).all()
            )

            # Send own message.
            return _announcement_msg.format(table)

    @staticmethod
    async def _unannounce(sender:ZulipUser, session:Session, message_id:int|None):
        """
          Reverts "special status" of a announced message.

        Args:
            session: The database session.
            message_id: The message id of the message that has to be unannounced

        Raises:
            DMError: If a unannouning fails.

        Returns:
            None
        """

        if session.query(GroupClaimAll).filter(GroupClaimAll.MessageId == message_id).first() is None:
            raise DMError(f"Message {message_id} is not yet claimed.")
        try:
            session.query(GroupClaimAll).filter(GroupClaimAll.MessageId == message_id).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not unclaim message {message_id}.") from e
        
        await sender.client.delete_message(message_id)

    @staticmethod
    async def _fix(sender:ZulipUser,session:Session,group:StreamGroup):
        """
          Makes sure that every subscriber of the given group is subscribed to all streams of this group.

        Args:
            sender: ZulipUser sending the message
            group: The StreamGroup to fix.

        Raises:
            DMError: If a fixing fails.

        Returns:
            None
        """
        ugroup:UserGroup = Streamgroup._get_usergroup(session,group)
        user_ids:list[int] = Usergroup.get_user_ids_for_group(session,ugroup)
        stream_names: list[str] = await Streamgroup._get_stream_names(session,sender,group)

        streams: list[(str,None)] = [
            (stream_name, None) for stream_name in stream_names
        ]

        await sender.client.subscribe_users_multiple_streams(user_ids,streams)

    @staticmethod
    async def _fix_all(sender:ZulipUser,session:Session):
        """
          Fixing all Streams of all StreamGroups
        
        Args:
            sender: ZulipUser sending the message
            session: The database session.

        Raises:
            DMError: If a fixing fails.

        Returns:
            None
        """
        groups: list[StreamGroup] = session.query(StreamGroup).all()
        
        for group in groups:
            await Streamgroup._fix(sender,session,group)
    
    
    @staticmethod
    async def _get_stream_names(session:Session, sender:ZulipUser, group:StreamGroup) -> list[str]:
        streams: list[str] = []
        for s in session.query(StreamGroupMember).filter(StreamGroupMember.StreamGroupId==group.StreamGroupId).all():
            result = await sender.client.get_stream_by_id(s.Stream.id)
            if result==None:
                raise DMError(f"Stream with id {s.Stream.id} could be not found.")
            name:str = result["name"]
            streams.append(name)
        return streams
    
    @staticmethod
    def _get_usergroup(session:Session, group:StreamGroup) -> UserGroup:
        s: StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupId==group.StreamGroupId).one()
        id:int = s.UserGroupId
        return session.query(UserGroup).filter(UserGroup.GroupId==id).one()
    
    @staticmethod
    def _get_groups_for_user(session: Session, user: ZulipUser) -> list[StreamGroup]:
        ug_ids: list[int] = [
            ugroup.GroupId
            for ugroup in Usergroup.get_groups_for_user(session, user)
        ]

        result: list[StreamGroup] = []

        for id in ug_ids:
            s = session.query(StreamGroup).filter(StreamGroup.UserGroupId == id).one_or_none()
            if s:
                result.append(s)

        return result
    