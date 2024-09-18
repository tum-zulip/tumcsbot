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
from zulip import ZulipChannel

from tumcsbot.lib.response import Response
from tumcsbot.plugin import Event, Plugin, PluginCommandMixin
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import DB, TableBase
from tumcsbot.plugin_decorators import *
from tumcsbot.plugins.usergroup import UserGroup
from tumcsbot.plugins.usergroup import Usergroup

class Group(TableBase): # type: ignore
    __tablename__ = 'Groups'

    GroupId = Column(String, primary_key=True)
    GroupEmote = Column(String, nullable=False, unique=True)
    
    _s = relationship("", back_populates="GroupMember")
    usergroup: Mapped[UserGroup] = relationship(ondelete='CASCADE')

    @hybrid_property
    def s(self) -> list[ZulipChannel]:
        return [member. for member in self._s]


class GroupMember(TableBase): # type: ignore
    __tablename__ = 'GroupMembers'

    GroupId = Column(String, ForeignKey('Groups.GroupId', ondelete='CASCADE'), primary_key=True)
     = Column(ZulipChannel, primary_key=True)
    

class GroupClaim(TableBase): # type: ignore
    __tablename__ = 'GroupClaims'

    MessageId = Column(Integer, primary_key=True)
    GroupId = Column(String, ForeignKey('Groups.Id', ondelete='CASCADE'), primary_key=True)


class GroupClaimAll(TableBase): # type: ignore
    # todo: why is this necessary?
    __tablename__ = 'GroupClaimsAll'

    MessageId = Column(Integer, primary_key=True)

    # Define the constraint to ensure MessageId is unique across all GroupClaims tables
    UniqueConstraint('MessageId', name='uq_group_claims_all_message_id')

class group(PluginCommandMixin, Plugin):
    zulip_events = ["message", "reaction", ""]
    syntax = cleandoc(
        """
        group subscribe <group_id>
          or group unsubscribe [-s | -w] <group_id>
          or group add <group_id> <emoji>
          or group remove <group_id>
          or group add_s <group_id> <_pattern>...
          or group remove_s <group_id> <_pattern>...
          or group list
          or group claim <group_id>
          or group claim_message <group_id> <message_id>
          or group unclaim <group_id> <message_id>
          or group announce [-u]
          or group unannounce <message_id>
          or group fix <group_id>
          or group fix_all
        """
    )
    description = cleandoc(
        """
        Manage  groups using identifiers.
        **Note that "s" here only cover public s!**

        Subscribe to / unsubscribe from a group using \
        `group (un)subscribe`.
        If you use the `-s` switch for `group unsubscribe`, you will \
        not only unsubscribe from your group subscription, but also \
        from all the s belonging to this group as long as they \
        do not also belong to another group you are subscribed to. \
        (It's not an `s`. On my world it means "s".)
        if you use the `-w` switch for `group unsubscribe`, you will \
        only unsubscribe from the group, but keep the s of this \
        group you are currently subscribed to.
        **The default behavior** for `group unsubscribe` (so without \
        any flags) is equivalent to the `-s` option.

        Create/remove a  group with `group add`/`group remove` by \
        specifing an identifier and an emoji. Note that removing a  \
        group has no other consequences than removing the associations \
        in the bot!
        Use `group add_s` to add a newline-separated list of \
        regexes representing the s which should be considered as \
        part of this  group. Use `group remove_s` to do the \
        opposite. Note that you have to quote the regexes!
        With `group list`, you get a list of all group ids with their \
        associated  patterns.
        Use `group claim` to make a message "special" for a given \
        group. If a user reacts on a "special" message with the emoji \
        that is assigned to the group the message is special for, the \
        user gets subscribed to all s belonging to this group. \
        A message with the `group claim` command in the first line may \
        also contain arbitrary other text.
        `group announce` triggers a message from the bot \
        which will be "special" for all groups and in which the bot \
        will maintain a list of all groups. If used with the `-u` \
        switch, all existing announcement messages messages will be \
        updated to the current announcement message template.
        `group fix` makes sure that every subscriber of the given group \
        is subscribed to all s of this group. This command is \
        intended to fix situations where the usual mechanism failed for \
        some reason. You will receive additional debug information in \
        case the fix command fails, too. Please forward this information \
        then to the bot owner.
        `group fix_all` does the same as `group fix` for every group.

        [administrator/moderator rights needed except for (un)subscribe]
        """
    )
    _announcement_msg: str = cleandoc(
        """
        Hi! :smile:

        I have the pleasure to announce some  groups here.
        You may subscribe to a  group in order to be automatically \
        subscribed to all s belonging to that group. Also, you \
        will be kept updated when new s are added to the group.
        Just react to this message with the emoji of the  group \
        you like to subscribe to. Remove your emoji to unsubscribe \
        from this group. (1)

         group | emoji
        ------------ | -----
        {}
        *to be continued*

        In case the emojis do not work for you, you may write me a PM:
        - `group subscribe <group_id>`
        - `group unsubscribe <group_id>`

        Have a nice day! :sunglasses:

        (1) Note that this will also unsubscribe you from the existing \
        s of this group. If you only want to cancel the \
        subscription without being unsubscribed from existing s, \
        just write me a PM:
        - `group unsubscribe -w <group_id>`
        """
    )
    _announcement_msg_table_row_fmt: str = "%s | :%s:"
    _announcement_msg_table_row_regex: str = r"\n*%s \| :[^:]+:\s*\n*"
    _claim_all_sql: str = "insert into GroupClaimsAll values (?)"
    _claim_group_sql: str = "insert into GroupClaims values (?,?)"
    _get_all_emojis_sql: str = "select Emoji from Groups"
    _get_claims_for_all_sql: str = "select MessageId from GroupClaimsAll"
    _get_claims_for_group: str = "select MessageId from GroupClaims where GroupId = ?"
    _get_emoji_from_group_sql: str = "select Emoji from Groups where Id = ?"
    _get_group_from_emoji_sql: str = "select Id from Groups where Emoji = ?"
    _get_group_subscribers_sql: str = "select UserId from GroupUsers where GroupId = ?"
    _get_s_sql: str = "select Channels from Groups where Id = ? collate nocase"
    _get_s_from_user_sql: str = (
        "select Channels from Groups join GroupUsers on Id = GroupId where UserId = ?"
    )
    _insert_sql: str = "insert into Groups values (?,?,?)"
    _is_group_claimed_by_msg_sql: str = (
        "select * from GroupClaims where GroupId = ? and MessageId = ?"
    )
    _is_message_announcement_sql: str = (
        "select * from GroupClaimsAll where MessageId = ?"
    )
    _list_sql: str = "select * from Groups"
    _remove_sql: str = "delete from Groups where Id = ? collate nocase"
    _subscribe_user_sql: str = "insert into GroupUsers values (?,?)"
    _update_s_sql: str = (
        "update Groups set Channels = ? where Id = ? collate nocase"
    )
    _unclaim_msg_from_group_sql: str = (
        "delete from GroupClaims where MessageId = ? and GroupId = ?"
    )
    _unclaim_msg_for_all_sql: str = "delete from GroupClaimsAll where MessageId = ?"
    _unsubscribe_user_sql: str = (
        "delete from GroupUsers where UserId = ? and GroupId = ?"
    )

    def _init_plugin(self) -> None:
        # Get own database connection.
        self._db: DB = DB()

        # Init command parsing.
        self.command_parser = CommandParser()
        self.command_parser.add_subcommand("subscribe", args={"group_id": str})
        self.command_parser.add_subcommand(
            "unsubscribe", opts={"s": None, "w": None}, args={"group_id": str}
        )
        self.command_parser.add_subcommand(
            "add", args={"group_id": str, "emoji": Regex.get_emoji_name}
        )
        self.command_parser.add_subcommand("remove", args={"group_id": str})
        self.command_parser.add_subcommand(
            "add_s", args={"group_id": str}, greedy={"s": str}
        )
        self.command_parser.add_subcommand(
            "remove_s", args={"group_id": str}, greedy={"s": str}
        )
        self.command_parser.add_subcommand("list")
        self.command_parser.add_subcommand("claim", args={"group_id": str})
        self.command_parser.add_subcommand(
            "claim_message", args={"group_id": str, "message_id": str}
        )
        self.command_parser.add_subcommand(
            "unclaim", args={"group_id": str, "message_id": int}
        )
        self.command_parser.add_subcommand("announce", opts={"u": None})
        self.command_parser.add_subcommand("unannounce", args={"message_id": int})
        self.command_parser.add_subcommand("fix", args={"group_id": str})
        self.command_parser.add_subcommand("fix_all")

        # Init some usefule constants.
        self._get_emoji: re.Pattern[str] = re.compile(r"\s*:?([^:]+):?\s*")
        # (removing trailing 'api/' from host url).
        self.message_link: str = (
            "[{0}](" + self.client.base_url[:-4] + "#narrow/id/{0})"
        )

    def handle_event(self, event: Event) -> Response | Iterable[Response]:
        if event.data["type"] == "reaction":
            return self.handle_reaction_event(event.data)
        if event.data["type"] == "":
            return self.handle__event(event.data)
        return self.handle_message(event.data["message"])
# ==============================================================================================================================================================================

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to subscribe to.")
    async def subscribe(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Subscribe to Group.
        """
        group: Group = args.group_id
        members: UserGroup = group.usergroup

        Usergroup.add_user_to_group(session, sender, members)
        yield DMResponse(f"You have subscribed to the group `{group.GroupId}`")
    
    @command
    @arg("group_id", Group.GroupId, description="The group id to unsubscribe from.")
    @opt("s", description="Unsubscribe from all s of this group.")
    @opt("w", description="Keep the s of this group you are currently subscribed to.")
    async def unsubscribe(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unsubscribe from Group.
        """
        group: Group = args.group_id
        members: UserGroup = group.usergroup
        s: list[ZulipChannel] = group.s

        if opts.s and opts.w:
            raise DMError("The `-s` and `-w` flags are mutually exclusive, see `help group`.")
        
        Usergroup.remove_user_from_group(session, sender, members)
        yield DMResponse(f"You have unsubscribed from the group `{group.GroupId}`")

        if opts.s or not opts.w:
            for  in s:
                # todo: does this function exist as async?
                sender.client.remove_subscriptions()
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", int, description="The group id to add.")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the reaction.")
    async def add(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Create a whole Group.
        """
        id: int = args.group_id
        emoji_name: str | None = args.emoji 
        
        group._create_group(session,id,emoji_name)
        yield DMResponse(f"group `{id}` created")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to remove.")
    def remove(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Delete a whole Group.
        """
        group: Group = args.group_id

        group._delete_group(session,group)
        return DMResponse(f"group `{group.GroupId}` deleted")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to add s to.")
    @arg("s", str, description="The  patterns to add.", greedy=True)
    def add_s(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add s to a Group.
        """
        group: Group = args.group_id
        _patterns: list[str] = args.s

        group._add_s(session,sender,group,_patterns)
        return DMResponse(f"Added s to group `{group.GroupId}`.")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to remove s from.")
    @arg("s", str, description="The  patterns to remove.", greedy=True)
    def remove_s(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Remove s to a Group.
        """   
        group: Group = args.group_id
        _patterns: list[str] = args.s

        group._remove_s(session,sender,group,_patterns)
        return DMResponse(f"Removed s to group `{group.GroupId}`.")
    
    @command(name="list")
    @privilege(Privilege.ADMIN)
    async def _list(
        self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List all Groups with their associated patterns.
        """
        response: str = (
            "Group Id | Emoji | Channels | ClaimedBy\n---- | ---- | ---- | ----"
        )

        message_link: str = (
            "[{0}](" + sender.client.base_url[:-4] + "#narrow/id/{0})"
        )

        groups: list[Group] = session.query(Group).all()
        if len(groups) == 0:
            raise DMError(f"No  groups found")
       
        for group in groups:
            group_id = group.GroupId
            emoji = group.GroupEmote
            s = group.s
                

            s_concat: str = ", ".join(f"'{s}'" for s in s.split("\n"))
            claims: str = ", ".join(
                [
                    message_link.format(claim.MessageId) 
                    for claim in session.query(GroupClaim).filter(GroupClaim.GroupId==group.GroupId).all()
                ]
            )
            response += (
                f"\n{group_id} | {emoji} :{emoji}: | `{s_concat}` | {claims}"
            )

        response += "\n\nMessages claimed for all groups: " + ", ".join(
            message_link.format(claim.MessageId)
            for claim in session.query(GroupClaimAll).all()
        )

        return DMResponse(response)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The id of a group for which to claim message.",optional=True)
    @arg("message_id", int, description="The id of the message to claim.")
    @opt("a",long_opt="all",description="Claim message in all existing groups.")
    async def claim_message_new(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """ 
        Make a message "special" for a given group.
        If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, the user gets subscribed to all s belonging to this group 
        """
        group: Group | None = args.group_id
        msg_id: int = args.message_id

        if not opts.a and not group:
             raise DMError("Either argument `group_id` or flag `-a` necessary.")
        if opts.a and group:
             raise DMError("The argument `group_id` and flag `-a` are mutually exclusive.")

        msg = await sender.client.get_message_by_id(msg_id)

        if msg["type"] != "":
            raise DMError("Claim only  messages.")
            
        await group._claim(group, session, msg_id,all=opts.a)
        
        if group:
            resp = f"Claimed message in group `{group.GroupId}`."
        else:
            resp = f"Claimed message in all groups."

        yield DMMessage(sender, resp)

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The id of a group for which to claim message.",optional=True)
    @opt("a",long_opt="all",description="Claim message in all existing groups.")
    async def claim_new(self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """ 
        Make the written message "special" for a given group.
        If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, the user gets subscribed to all s belonging to this group 
        """
        group: Group | None = args.group_id

        if not opts.a and not group:
             raise DMError("Either argument `group_id` or flag `-a` necessary.")
        if opts.a and group:
             raise DMError("The argument `group_id` and flag `-a` are mutually exclusive.")
 
        msg = message
        msg_id = message["id"]

        if msg["type"] != "":
            raise DMError("Claim only  messages.")
            
        await group._claim(group, session, msg_id, all=opts.a)

        if group:
            resp = f"Claimed message in group `{group.GroupId}`."
        else:
            resp = f"Claimed message in all groups."

        yield DMMessage(sender, resp)

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to claim.")
    def claim(self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """ 
        Make a message "special" for a group. 
        """
        group: Group = args.group_id

        if message["type"] != "":
            raise DMError("Claim only  messages.")
        group._claim(message, group, session, None)
        return DMResponse(f"Claimed message in group `{group.GroupId}`.")
    
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to claim.")
    @arg("message_id", int, description="The message id to claim.")
    def claim_message(self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Make a message "special" for a group.
        """
        group: Group = args.group_id
        msg_id: int = args.message_id

        group._claim(message, group, session, msg_id)
        return DMResponse(f"Claimed message in group `{group.GroupId}`.")
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to unclaim.")
    @arg("message_id", int, description="The message id to unclaim.")
    def unclaim(self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Reverts "special" status of claimed message. 
        """
        group: Group = args.group_id
        msg_id: int = args.message_id

        return group._unclaim(group, session, msg_id)
    
    @command
    @privilege(Privilege.ADMIN)
    @opt("u", description="Update all announcement messages.")
    def announce(self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Triggers a "special" message from the bot for all groups with a list of all groups. 
        """

        if opts.u:
            return self._update_announcement_messages(sender,session)
        if message["type"] != "":
            raise DMError("Claim only  messages.")
        return group._announce(sender, session, message)
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("message_id", int, description="The message id to unannounce.")
    def unannounce(self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Reverts "special" status of announced message. 
        """
        msg_id: int = args.message_id

        return group._unannounce(session, msg_id)
    

    @command
    @privilege(Privilege.ADMIN)
    @arg("group_id", Group.GroupId, description="The group id to fix.")
    def fix(self,
        sender: ZulipUser,
        _session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Makes sure every subscriber of the group is subscribed to all s of this group. 
        """
        group: Group = args.group_id

        return group._fix(sender, group)
    
    @command
    @privilege(Privilege.ADMIN)
    def fix_all(self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Group fix for every Group.
        """
        
        return group._fix_all(sender, session)
    

    # =============== CLASS METHODS =============================================================================
    @staticmethod
    def _create_group(session: Session, id: int, emote:str) -> None:
        """
        Create a new Group.

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
            session.query(Group).filter(Group.GroupId == id).first()
            is not None
        ):
            raise DMError(f"group '{id}' already exists")

        group = Group(GroupId=id,GroupEmote=emote)
        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create group '{id}'. {str(e)}") from e


    @staticmethod
    def _delete_group(session: Session, group: Group) -> None:
        """
        Delete a Group.

        Args:
            session: The database session.
            group: The group to delete.

        Raises:
            DMError: If the group deletion fails.

        Returns:
            None
        """
        try:
            session.query(Group).filter(Group.GroupId == group.GroupId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not delete group '{group.GroupId}'. {str(e)}") from e
        

    @staticmethod
    def _remove_s(session: Session, sender:ZulipUser, group:Group,_patterns:list[str]):
        """
        Remove the s of a list of  patterns from a Group.

        Args:
            session: The database session.
            sender: The sender of the message
            group: The group in which s to delete.
            _patterns: A list of -regexes 

        Raises:
            DMError: If a  deletion fails.

        Returns:
            None
        """
        for _reg in _patterns:
            s = sender.client.get_s_from_regex(_reg)
            for  in s:
                if session.query(GroupMember).filter(GroupMember. == ).filter(GroupMember.GroupId == group.GroupId).first() is None:
                   raise DMError(f"{.name()} is not in group '{group.GroupId}'")
                try:
                    # search for the listed s in the db and delete them
                    session.query(GroupMember).filter(GroupMember. == ).filter(GroupMember.GroupId == group.GroupId).delete()
                    session.commit()
                except sqlalchemy.exc.IntegrityError as e:
                    session.rollback()
                    yield DMError(f"Could not delete  '{.name()}' from group '{group.GroupId}'. {str(e)}")
                
    @staticmethod
    def _add_s(session: Session, sender:ZulipUser, group:Group,_patterns:list[str]):
        """
        Add the s of a list of  patterns to a Group.

        Args:
            session: The database session.
            sender: The sender of the message
            group: The group in which s to add.
            _patterns: A list of -regexes 

        Raises:
            DMError: If a  addition fails.

        Returns:
            None
        """
        
        for _reg in _patterns:
            s = sender.client.get_s_from_regex(_reg)
            for  in s:
                if  in group.s:
                       raise DMError(f"{.name()} is already in group '{group.GroupId}'")
                try:
                    session.add(GroupMember(GroupId=group.GroupId,=))
                    session.commit()
                except sqlalchemy.exc.IntegrityError as e:
                    session.rollback()
                    raise DMError( f"Could not add {.name()} to group '{group.GroupId}'.") from e

    @staticmethod
    def _claim(message, group:Group, session:Session, message_id:int|None):
        """
          Make a message "special" for a given group. 
          If a user reacts on a "special" message with the emoji that is assigned to the group the message is special for, 
          the user gets subscribed to all s belonging to this group.
        

        Args:
            message: The message that was written to the bot.
            group: The group for which message is claimed.
            session: The database session.
            message_id: The message id of the message that has to be claimed, None if message written to bot should be claimed

        Raises:
            DMError: If a claiming fails.

        Returns:
            None
        """
        
        if message_id is None:
            message_id = message["id"]

        if group.GroupId:
            try: 
                session.query(GroupClaim).filter(GroupClaim.GroupId == group.GroupId).add(message_id)
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                yield DMError(f"Could not claim message '{message_id}' for group '{group.GroupId}'. {str(e)}")
        else:
            try: 
                session.query(GroupClaimAll).add(message_id)
                session.commit()
            except sqlalchemy.exc.IntegrityError as e:
                session.rollback()
                yield DMError(f"Could not claim message '{message_id}'. {str(e)}")

        return ReactionResponse("ok")

    @staticmethod
    def _unclaim(group:Group,session:Session,message_id:int):
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
        
        if session.query(GroupClaim).filter(GroupClaim.GroupId == group.GroupId).filter(GroupClaim.MessageId == message_id).first() is None:
            raise DMError(f"Message {message_id} is not in claimed in group '{group.GroupId}'")
        try:
            session.query(GroupClaim).filter(GroupClaim.GroupId==group.GroupId).filter(GroupClaim.MessageId == message_id).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not unclaim message {message_id} in group '{group.GroupId}'.") from e
        return ReactionResponse("ok")

    @staticmethod
    def _announce(sender:ZulipUser, session:Session, message):
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
        
        announcement_msg: str = group._build_announcement_message(session)

        # Remove the requesting message.
        sender.client.delete_message(message["id"])

        # Send own message.
        result: dict[str, Any] = DMResponse(announcement_msg)
        if result["result"] != "success":
            return Response.none()

        # Insert the id of the bots message into the database.
        try: 
            session.query(GroupClaimAll).add(result["id"])
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            yield DMError(f"Could not claim message '{result["id"]}'. {str(e)}")

        # Get all the currently existing emojis.
        all_emojis: list[str] = [
            group.GroupEmote for group in session.query(Group).all()
        ]

        if not all_emojis:
            return Response.none()

        # React with all those emojis on this message.
        for emoji in all_emojis:
            sender.client.send_response(
                Response.build_reaction_from_id(result["id"], emoji)
            )

        return Response.none()
    
    @staticmethod
    def _build_announcement_message(session:Session) -> str:
            _announcement_msg_table_row_fmt: str = "%s | :%s:"
            _announcement_msg: str = cleandoc(
        """
        Hi! :smile:

        I have the pleasure to announce some  groups here.
        You may subscribe to a  group in order to be automatically \
        subscribed to all s belonging to that group. Also, you \
        will be kept updated when new s are added to the group.
        Just react to this message with the emoji of the  group \
        you like to subscribe to. Remove your emoji to unsubscribe \
        from this group. (1)

         group | emoji
        ------------ | -----
        {}
        *to be continued*

        In case the emojis do not work for you, you may write me a PM:
        - `group subscribe <group_id>`
        - `group unsubscribe <group_id>`

        Have a nice day! :sunglasses:

        (1) Note that this will also unsubscribe you from the existing \
        s of this group. If you only want to cancel the \
        subscription without being unsubscribed from existing s, \
        just write me a PM:
        - `group unsubscribe -w <group_id>`
        """
    )

            table: str = "\n".join(
                _announcement_msg_table_row_fmt % (group.GroupId, group.GroupEmote)
                for group in session.query(Group).all()
            )

            # Send own message.
            return _announcement_msg.format(table)

    @staticmethod
    def _update_announcement_messages(sender:ZulipUser, session:Session):
        """
          Update the content of all announcement messages.

        Args:
            sender: ZulipUser sending the message
            session: The database session.

        Raises:
            DMError: If a claiming fails.

        Returns:
            None
        """

        def update(msg: dict[str, Any]) -> None:
            msg["content"] = group._build_announcement_message()

        if group._do_for_all_announcement_messages(sender, session, [update]):
            return ReactionResponse("ok")

        raise DMError("failed, see logs / `logfile`")

    @staticmethod
    def _do_for_all_announcement_messages(sender:ZulipUser, session:Session,funcs: list[Callable[[dict[str, Any]], Any]]) -> bool:
        """
          Apply functions to all announcement messages.

          The return values of the functions will be ignored. The message
          dict may be modified inplace.
        """
        success: bool = True

        for claim in session.query(GroupClaimAll).all():
            msg_id = claim.MessageId

            request: dict[str, Any] = {
                "anchor": msg_id,
                "num_before": 0,
                "num_after": 1,
            }
            result: dict[str, Any] = sender.client.get_messages(request)
            if result["result"] != "success" or not result["messages"]:
                yield PartialError("could not get message %s", str(request))
                success = False
                continue
            msg: dict[str, Any] = result["messages"][0]
            for func in funcs:
                func(msg)
            # todo: does this function exist as async? 
            result = sender.client.update_message(
                {"message_id": msg_id, "content": msg["content"]}
            )
            if result["result"] != "success":
                yield PartialError("could not edit message %d: %s", msg_id, str(result) )
                success = False

        return success

    @staticmethod
    def _unannounce(session:Session, message_id:int|None):
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
        if session.query(GroupClaimAll).filter(GroupClaimAll.MessageId == message_id).filter(GroupClaim.MessageId == message_id).first() is None:
            raise DMError(f"Message {message_id} is not yet claimed.")
        try:
            session.query(GroupClaimAll).filter(GroupClaimAll.MessageId == message_id).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not unclaim message {message_id}.") from e
        return ReactionResponse("ok")

    @staticmethod
    def _fix(sender:ZulipUser,group:Group):
        """
          Makes sure that every subscriber of the given group is subscribed to all s of this group.

        Args:
            sender: ZulipUser sending the message
            group: The Group to fix.

        Raises:
            DMError: If a fixing fails.

        Returns:
            None
        """
        err_msg: str | None = group._subscribe_users_to_s(
            sender,
            group._get_group_subscriber_ids([group]),
            group.s
        )
        if err_msg is not None:
            raise DMError(f"failed for some/all s: {err_msg}")
        return ReactionResponse("ok")

    @staticmethod
    def _fix_all(sender:ZulipUser,session:Session):
        """
          Fixing all Channels of all Groups
        
        Args:
            sender: ZulipUser sending the message
            session: The database session.

        Raises:
            DMError: If a fixing fails.

        Returns:
            None
        """
        groups: list[Group] = session.query(Group).all()
        
        for group in groups:
            group._fix(sender,group)
    
    @staticmethod
    def _subscribe_users_to_s(
        sender:ZulipUser, user_ids: list[int], s: list[ZulipChannel]
    ) -> str | None:
        """
        Subscribe the users to all s of the Group.
        """
        ret: tuple[bool, str | None] = sender.client.subscribe_users_multiple_s(
            user_ids=user_ids,
            s=[
                (.name(), None)
                for  in s
            ],
        )

        return None if ret[0] else ret[1]
    
    @staticmethod
    def _get_group_subscriber_ids(groups: list[Group]) -> list[int]:
        """
        Get the user_ids of all subscribers of the given groups.
        """
        result: set[ZulipUser] = set()

        for group in groups:
            result = result.union(
                set(group.usergroup.members())
            )
        result_ids = map(lambda user: user.id(),result)

        return list(result_ids)
    
# ==============================================================================================================================================================================
    def handle_reaction_event(
        self, event: dict[str, Any]
    ) -> Response | Iterable[Response]:
        group_id: str | None = self._get_group_id_from_emoji_event(
            event["message_id"], event["emoji_name"]
        )

        if group_id is None:
            return Response.none()
        if event["op"] == "add":
            return self._subscribe(event["user_id"], group_id)
        if event["op"] == "remove":
            return self._unsubscribe(
                event["user_id"], group_id, message=None, with_s=True
            )

        return Response.none()

    def handle__event(
        self, event: dict[str, Any]
    ) -> Response | Iterable[Response]:
        for  in event["s"]:
            # Get all the groups this  belongs to.
            group_ids: list[str] = self._get_group_ids_from_(["name"])
            # Get all user ids to subscribe to this new  ...
            user_ids: list[int] = self._get_group_subscribers(group_ids)
            # ... and subscribe them.
            self.client.subscribe_users(user_ids, ["name"])

        return Response.none()

    def is_responsible(self, event: Event) -> bool:
        return (
            super().is_responsible(event)
            or (
                event.data["type"] == "reaction"
                and event.data["op"] in ["add", "remove"]
                and event.data["user_id"] != self.client.id
            )
            or (event.data["type"] == "" and event.data["op"] == "create")
        )

    def _add(
        self, message: dict[str, Any], group_id: str, emoji: str
    ) -> Response | Iterable[Response]:
        """Command `group add <id> <emoji>`."""
        if "\n" in group_id:
            return Response.build_message(
                message, "The group id must not contain newlines."
            )

        try:
            self._db.execute(self._insert_sql, group_id, emoji, "", commit=True)
        except IntegrityError as e:
            return Response.build_message(message, str(e))

        # Update the announcement messages.
        if not self._announcements_add_group(group_id):
            return Response.build_message(
                message, "Group added, but announcement failed for some messages."
            )

        return Response.ok(message)

    def _announce_outdated(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        announcement_msg: str = self._build_announcement_message()

        # Remove the requesting message.
        self.client.delete_message(message["id"])

        # Send own message.
        result: dict[str, Any] = self.client.send_response(
            Response.build_message(message, announcement_msg)
        )
        if result["result"] != "success":
            return Response.none()

        # Insert the id of the bots message into the database.
        try:
            self._db.execute(self._claim_all_sql, result["id"], commit=True)
        except Exception as e:
            return Response.build_message(message, str(e))

        # Get all the currently existing emojis.
        result_sql: list[tuple[Any, ...]] = self._db.execute(self._get_all_emojis_sql)
        if not result_sql:
            return Response.none()

        # React with all those emojis on this message.
        for emoji in map(lambda t: cast(str, t[0]), result_sql):
            self.client.send_response(
                Response.build_reaction_from_id(result["id"], emoji)
            )

        return Response.none()

    def _announcements_add_group(self, group_id: str) -> bool:
        """Add the given group to all announcement messages."""
        emoji: str | None = self._get_emoji_from_group(group_id)
        if not emoji:
            return False
        to_insert: str = self._announcement_msg_table_row_fmt % (group_id, emoji)

        pattern: re.Pattern[str] = re.compile(r"\n*\*to be continued\*\n*")

        return self._do_for_all_announcement_messages(
            [
                lambda msg: msg.update(
                    content=pattern.sub(
                        "\n" + to_insert + "\n*to be continued*\n\n", msg["content"]
                    )
                ),
                lambda msg: self.client.send_response(
                    Response.build_reaction(msg, emoji)
                ),
            ]
        )

    def _announcements_remove_group(self, group_id: str) -> bool:
        """Remove the given group from all announcement messages."""
        emoji: str | None = self._get_emoji_from_group(group_id)
        if not emoji:
            return False

        pattern: re.Pattern[str] = re.compile(
            self._announcement_msg_table_row_regex % re.escape(group_id)
        )

        return self._do_for_all_announcement_messages(
            [
                lambda msg: msg.update(content=pattern.sub("\n", msg["content"])),
                lambda msg: self.client.remove_reaction(
                    {"message_id": msg["id"], "emoji_name": emoji}
                ),
            ]
        )

    def _build_announcement_message(self) -> str:
        table: str = "\n".join(
            self._announcement_msg_table_row_fmt % (group_id, emoji)
            for group_id, emoji, _ in self._db.execute(self._list_sql)
        )

        # Send own message.
        return self._announcement_msg.format(table)

    def _change_s(
        self,
        session: Session,
        message: dict[str, Any],
        group_id: str,
        command: str,
        change__regs: list[str],
    ) -> Response | Iterable[Response]:
        """Command `group (add_s|remove_s) <id> <>...`."""
        # Validate the regexes.
        for reg in change__regs:
            try:
                re.compile(reg)
            except re.error as e:
                yield PartialError("invalid regex: %s\n%s", reg, str(e))

        result_sql: list[tuple[Any, ...]] = self._db.execute(
            self._get_s_sql, group_id, commit=True
        )
        if not result_sql:
            return Response.build_message(message, f"Group {group_id} does not exist.")

        # Current  patterns.
        _list: list[str] = result_sql[0][0].split("\n")
        # The string containing the new list of  patterns (newline separated).
        # The patterns have to be non-empty.
        new_s: str = "\n".join(
            filter(
                bool,
                set(_list + change__regs)
                if command == "add_s"
                else [s for s in _list if s not in change__regs],
            )
        )

        try:
            self._db.execute(
                self._update_s_sql, new_s, group_id, commit=True
            )
        except Exception as e:
            self.logger.exception(e)
            return Response.build_message(message, str(e))

        # Subscribe the group subscribers to the new s.
        self._subscribe_users_to__regexes(
            self._get_group_subscribers([group_id]), change__regs
        )

        return Response.ok(message)

    def _claim_outdated(
        self, message: dict[str, Any], group_id: str, message_id: int | None
    ) -> Response | Iterable[Response]:
        """Command `group claim <group_id>` or `group claim_message <group_id> [message_id]."""
        if message_id is None:
            message_id = message["id"]

        if group_id:
            self._db.execute(self._claim_group_sql, message_id, group_id, commit=True)
        else:
            self._db.execute(self._claim_all_sql, message_id, commit=True)

        return Response.ok(message)

    def _do_for_all_announcement_messages(
        self, funcs: list[Callable[[dict[str, Any]], Any]]
    ) -> bool:
        """Apply functions to all announcement messages.

        The return values of the functions will be ignored. The message
        dict may be modified inplace.
        """
        success: bool = True

        for (msg_id,) in self._db.execute(self._get_claims_for_all_sql):
            request: dict[str, Any] = {
                "anchor": msg_id,
                "num_before": 0,
                "num_after": 1,
            }
            result: dict[str, Any] = self.client.get_messages(request)
            if result["result"] != "success" or not result["messages"]:
                self.logger.warning("could not get message %s", str(request))
                success = False
                continue
            msg: dict[str, Any] = result["messages"][0]
            for func in funcs:
                func(msg)
            result = self.client.update_message(
                {"message_id": msg_id, "content": msg["content"]}
            )
            if result["result"] != "success":
                self.logger.warning(
                    "could not edit message %d: %s", msg_id, str(result)
                )
                success = False

        return success

    def _fix_outdated(
        self,
        message: dict[str, Any],
        group_id: str,
    ) -> Response | Iterable[Response]:
        err_msg: str | None = self._subscribe_users_to__regexes(
            self._get_group_subscribers([group_id]),
            self._get__regs_from_group_id(group_id),
        )
        if err_msg is not None:
            return Response.build_message(
                message, f"failed for some/all s: {err_msg}"
            )
        return Response.ok(message)

    def _fix_all_outdated(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        responses: list[Response] = []
        for group_id, _, _ in self._db.execute(self._list_sql):
            response: Response | Iterable[Response] = self._fix(message, group_id)
            if isinstance(response, IterableClass):
                responses.extend(response)
            else:
                responses.append(response)
        return responses

    def _get_emoji_from_group(self, group_id: str) -> str | None:
        """Get the emoji for a given group id."""
        result_sql: list[tuple[Any, ...]] = self._db.execute(
            self._get_emoji_from_group_sql, group_id
        )
        if not result_sql:
            self.logger.debug("no emoji found for group %s", group_id)
            return None
        return cast(str, result_sql[0][0])

    def _get_group_id_from_emoji_event(self, message_id: int, emoji: str) -> str | None:
        result_sql: list[tuple[Any, ...]]

        result_sql = self._db.execute(self._get_group_from_emoji_sql, emoji)
        if not result_sql:
            return None
        group_id: str = cast(str, result_sql[0][0])

        # Check whether the message is claimed by this group.
        result_sql = self._db.execute(
            self._is_group_claimed_by_msg_sql, group_id, message_id
        )
        if not result_sql:
            result_sql = self._db.execute(self._is_message_announcement_sql, message_id)

        return group_id if result_sql else None

    def _get_group_ids_from_(self, _name: str) -> list[str]:
        """Get the ids of the groups the given  name belongs to."""
        result: list[str] = []

        for group_id, _, _regs_str in self._db.execute(self._list_sql):
            _regs: list[str] = _regs_str.split("\n")
            for _reg in _regs:
                if not _name_match(_reg, _name):
                    continue
                result.append(group_id)
                break

        return result

    def _get_group_subscribers(self, group_ids: list[str]) -> list[int]:
        """Get the user_ids of all subscribers of the given groups.

        Return no duplicate user_ids.
        """
        result: set[int] = set()

        for group_id in group_ids:
            result = result.union(
                set(
                    user_id
                    for (user_id,) in self._db.execute(
                        self._get_group_subscribers_sql, group_id
                    )
                )
            )

        return list(result)

    def _get__regs_from_group_id(self, group_id: str) -> list[str]:
        _regs: list[str] = []
        for (_regs_str,) in self._db.execute(self._get_s_sql, group_id):
            if not _regs_str:
                continue
            _regs.extend(_regs_str.split("\n"))
        return _regs

    def _list_helper(self, message: dict[str, Any]) -> Response | Iterable[Response]:
        """Command `group list`."""
        response: str = (
            "Group Id | Emoji | Channels | ClaimedBy\n---- | ---- | ---- | ----"
        )

        for group_id, emoji, s in self._db.execute(self._list_sql):
            s_concat: str = ", ".join(f"'{s}'" for s in s.split("\n"))
            claims: str = ", ".join(
                [
                    self.message_link.format(msg_id)
                    for (msg_id,) in self._db.execute(
                        self._get_claims_for_group, group_id
                    )
                ]
            )
            response += (
                f"\n{group_id} | {emoji} :{emoji}: | `{s_concat}` | {claims}"
            )

        response += "\n\nMessages claimed for all groups: " + ", ".join(
            self.message_link.format(msg_id)
            for msg_id, in self._db.execute(self._get_claims_for_all_sql)
        )

        return Response.build_message(message, response)

    def _remove(
        self,
        message: dict[str, Any],
        group_id: str,
    ) -> Response | Iterable[Response]:
        msg_success: bool = self._announcements_remove_group(group_id)

        self._db.execute(self._remove_sql, group_id, commit=True)

        if msg_success:
            return Response.ok(message)

        return Response.build_message(
            message, "Group removed, but removal failed for some announcement messages."
        )

    def _subscribe(
        self, user_id: int, group_id: str, message: dict[str, Any] | None = None
    ) -> Response | Iterable[Response]:
        """Subscribe a user to a group."""
        msg: str

        try:
            self._db.execute(self._subscribe_user_sql, user_id, group_id, commit=True)
        except IntegrityError as e:
            self.logger.exception(e)
            # User already subscribed.
            msg = f"I think you are already subscribed to group {group_id}."
            if message:
                return Response.build_message(message, msg)
            return Response.build_message(
                message=None, content=msg, msg_type="private", to=[user_id]
            )

        err_msg: str | None = self._subscribe_users_to__regexes(
            [user_id], self._get__regs_from_group_id(group_id)
        )

        if err_msg is None:
            if message is not None:
                return Response.ok(message)
            return Response.build_message(
                message=None,
                content=f"Subscribed to group {group_id}.",
                msg_type="private",
                to=[user_id],
            )

        msg = f"Failed to subscribe you to some/all s: {err_msg}."

        if message is not None:
            return Response.build_message(message, msg)
        # Write a private message to the user.
        return Response.build_message(
            message=None, content=msg, msg_type="private", to=[user_id]
        )

    def _unannounce(
        self, message: dict[str, Any], message_id: str
    ) -> Response | Iterable[Response]:
        self._db.execute(self._unclaim_msg_for_all_sql, message_id, commit=True)
        return Response.ok(message)

    def _unclaim_outdated(
        self, message: dict[str, Any], group_id: str, message_id: str
    ) -> Response | Iterable[Response]:
        try:
            msg_id: int = int(message_id)
        except ValueError:
            return Response.build_message(message, f"{message_id} is not an integer.")
        self._db.execute(
            self._unclaim_msg_from_group_sql, msg_id, group_id, commit=True
        )
        return Response.ok(message)

    def _unsubscribe(
        self,
        user_id: int,
        group_id: str,
        message: dict[str, Any] | None = None,
        with_s: bool = False,
    ) -> Response | Iterable[Response]:
        """Unsubscribe a user from a group.

        If `real` is True, also unsubscribe the user from all s
        belonging to this group (which do not belong to another 
        group the user is subscribed to).
        """
        self._db.execute(self._unsubscribe_user_sql, user_id, group_id, commit=True)

        if not with_s:
            if message is not None:
                return Response.ok(message)
            return Response.build_message(
                message=None,
                content=f"Unsubscribed from group {group_id}.",
                msg_type="private",
                to=[user_id],
            )

        # Get the s of the group we want to unsubscribe from.
        _regs_group: list[str] = self._get__regs_from_group_id(group_id)
        s_group: set[str] = set(
            
            for _reg in _regs_group
            for  in self.client.get_s_from_regex(_reg)
        )
        # Get the s of all the other groups this user might be subscribed to.
        _regs: list[tuple[Any, ...]] = self._db.execute(
            self._get_s_from_user_sql, user_id
        )
        s: set[str] = set(
            
            for (_reg,) in _regs
            for  in self.client.get_s_from_regex(_reg)
        )
        unsubscribe_s: list[str] = list(s_group - s)
        # Make sure we do not unsubscribe from a  which belongs to another
        # group the user is subscribed to.
        result: dict[str, Any] = self.client.remove_subscriptions(
            s=unsubscribe_s, principals=[user_id]
        )
        msg: str
        if result["result"] != "success":
            msg = f"Unsubscribed from group {group_id}. Failed to unsubscribe from (s): {unsubscribe_s}: {result}"
        else:
            msg = f"Unsubscribed from group {group_id}. Unsubscribed from (s): {unsubscribe_s}"

        if message is not None:
            return Response.build_message(message=message, content=msg)
        else:
            return Response.build_message(
                message=None,
                content=msg,
                msg_type="private",
                to=[user_id],
            )

    def _update_announcement_messages(
        self, message: dict[str, Any]
    ) -> Response | Iterable[Response]:
        """Update the content of all announcement messages."""

        def update(msg: dict[str, Any]) -> None:
            msg["content"] = self._build_announcement_message()

        if self._do_for_all_announcement_messages([update]):
            return Response.ok(message)

        return Response.build_message(message, "failed, see logs / `logfile`")
