#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

# TODO: replacement for zulip usergroups. Replace as soon as api allows bot requests for usergroups

import logging
from typing import Any, AsyncGenerator
from sqlalchemy import Column, Integer, String, ForeignKey
import sqlalchemy
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.ext.hybrid import hybrid_property
import yaml

from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import Session, TableBase, serialize_model
from tumcsbot.plugin import PluginCommandMixin, Plugin
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
)
from tumcsbot.plugin_decorators import arg, command, opt, privilege


class UserGroup(TableBase): # type: ignore
    """Represents a user group in the system."""

    __tablename__ = "UserGroups"

    GroupId = Column(Integer, primary_key=True, autoincrement=True)
    GroupName = Column(String, unique=True)

    _members = relationship(
        "UserGroupMember",
        back_populates="groups",
        cascade="all, delete-orphan"
    )

    @hybrid_property
    def members(self) -> list[ZulipUser]:
        return [member.User for member in self._members]
    
    _streamgroup = relationship(
        "StreamGroup",
        back_populates="_usergroup"
    )


class UserGroupMember(TableBase): # type: ignore
    """Represents a user group member in the system."""

    __tablename__ = "UserGroupMembers"

    GroupId = Column(
        Integer, ForeignKey("UserGroups.GroupId", ondelete="CASCADE"), primary_key=True
    )
    User = Column(ZulipUser, primary_key=True)

    # This establishes the relationship between UserGroupMember and UserGroup
    groups: Mapped[list["UserGroup"]] = relationship(viewonly=True, back_populates="_members")


class Usergroup(PluginCommandMixin, Plugin):
    """
    Manage user groups.
    Alternative to Zulip user groups, as the bot does not have access to the api.
    """

    @command(name="list")
    @arg(
        "user",
        ZulipUser,
        "The user for which the groups should be listed",
        optional=True,
    )
    @opt(
        "a",
        long_opt="all",
        description="Display all user groups with all users",
        privilege=Privilege.ADMIN,
    )
    async def _list(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List user groups
        """
        user: ZulipUser
        if args.user is not None:
            user = args.user
        else:
            user = sender

        if opts.a:
            groups: list[UserGroup]
            groups = session.query(UserGroup).all()
            if len(groups) == 0:
                raise DMError(f"No user groups found")

            for group in groups:
                members = []
                for m in group.members:
                    await m
                    members.append(m.mention_silent)

                members = members or ["No members"]

                yield DMResponse(f"## {group.GroupName}:\n" + ", ".join(members))

        else:
            if sender.id != user.id and not sender.isPrivileged:
                raise UserNotPrivilegedException("You can only list your own groups.")

            groups = Usergroup.get_groups_for_user(session, user)

            if len(groups) == 0:
                raise DMError(f"{user.mention_silent} is not in any user group")

            msg = ", ".join(f"`{g.GroupName}`" for g in groups)
            yield DMResponse(
                user.mention_silent + " is in the following usergroups:\n" + msg
            )

    @command
    @privilege(Privilege.ADMIN)
    @arg("group", UserGroup.GroupName, "The group the users should get added to")
    @arg("users", ZulipUser, "The users that should get added", greedy=True)
    async def add(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add users to group.
        """

        user: ZulipUser
        group: UserGroup = args.group
        for user in args.users:
            try:
                Usergroup.add_user_to_group(session, user, group)
                yield DMMessage(
                    user,
                    f"Hey {user.mention_silent},\nYou have been added to the usergroup `{group.GroupName}` by {sender.mention_silent}",
                )
                yield PartialSuccess(user.mention_silent)
            except DMError as e:
                yield PartialError(str(e) + "\n" + str(e.__cause__))

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, "The name of the user group")
    async def create(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Create an empty user group
        """
        Usergroup.create_group(session, args.name)
        yield DMResponse(f"User group `{args.name}` created")

    @command
    @privilege(Privilege.ADMIN)
    @arg("group", UserGroup.GroupName, "The group the user should get removed from")
    @arg(
        "user",
        ZulipUser,
        "The user that should get removed from groups."
    )
    @opt(
        "s",
        long_opt="silent",
        description="Do not notify the user",
    )
    async def remove(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        remove user from group
        """
        group: UserGroup = args.group
        user: ZulipUser = args.user
        Usergroup.remove_user_from_group(session, user, group)
        yield PartialSuccess(user.mention_silent)

        if not opts.s and user.id != sender.id:
            yield DMMessage(
                user,
                f"Hey {user.mention_silent},\nYou have been removed from the usergroup by {sender.mention_silent}:\n`{group.GroupName}`",
            )
    
    @command
    @privilege(Privilege.ADMIN)
    @arg("group", UserGroup.GroupName, "The group you wish to delete")
    @opt(
        "s",
        long_opt="silent",
        description="Do not notify the members",
    )
    async def delete(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        delete a usergroup
        """
        group: UserGroup = args.group
        members = group.members
        Usergroup.delete_group(session, group)
        if not opts.s:
                # notify all members
            for member in members:
                await member
                yield DMMessage(
                    member,
                    f"Hey {member.mention_silent},\nYou have been removed from the usergroup `{group.GroupName}` by {sender.mention_silent}",
                )
        yield DMResponse(f"User group `{group.GroupName}` deleted")

    @command
    @arg("group", UserGroup.GroupName, "The group you wish to leave")
    async def leave(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        leave a usergroup
        """
        for m in args.group.members:
            logging.debug("-" * 80 + "\nMEMBERS: %s(%s)", m, sender == m)
        Usergroup.remove_user_from_group(session, sender, args.group)
        yield DMResponse(f"You have left the usergroup `{args.group.GroupName}`")

    @command
    @privilege(Privilege.ADMIN)
    async def export(
        self,
        _sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Export all user groups as yaml.
        """
        groups = []
        for g in session.query(UserGroup).all():
            try:
                for m in g.members:
                    await m
                group_dict = serialize_model(g)
                groups.append(group_dict)
            except Exception as e:
                yield PartialError(f"Could not serialize group {g.GroupName}: {str(e)}")
                self.logger.exception(e)
                continue
            yield PartialSuccess(f"Exported group {g.GroupName}")
        yield DMResponse(
            "```yaml\n"
            + yaml.dump(groups, allow_unicode=True, sort_keys=False)
            + "\n```"
        )

    @staticmethod
    def get_groups(session: Session) -> list[UserGroup]:
        return session.query(UserGroup).all()

    @staticmethod
    def create_group(session: Session, name: str) -> None:
        """
        Create a new user group.

        Args:
            session: The database session.
            name: The name of the group.
            description: The description of the group.

        Raises:
            DMError: If the group creation fails.

        Returns:
            None
        """
        if (
            session.query(UserGroup).filter(UserGroup.GroupName == name).first()
            is not None
        ):
            raise DMError(f"Group '{name}' already exists")

        group = UserGroup(GroupName=name)
        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create group '{name}'. {str(e)}") from e

    @staticmethod
    def delete_group(session: Session, group: UserGroup) -> None:
        """
        Delete a user group.

        Args:
            session: The database session.
            group: The group to delete.

        Raises:
            DMError: If the group deletion fails.

        Returns:
            None
        """
        try:
            session.query(UserGroup).filter(UserGroup.GroupId == group.GroupId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not delete group '{group.GroupName}'. {str(e)}") from e

    @staticmethod
    def remove_user_from_group(
        session: Session, user: ZulipUser, group: UserGroup
    ) -> None:
        if session.query(UserGroupMember).filter(UserGroupMember.User == user).filter(UserGroupMember.GroupId == group.GroupId).first() is None:
            raise DMError(f"{user.mention_silent} is not in usergroup '{group.GroupName}'")
        try:
            session.query(UserGroupMember).filter(UserGroupMember.User == user).filter(
                UserGroupMember.GroupId == group.GroupId
            ).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(
                f"Could not remove {user.mention_silent} from usergroup '{group.name}'."
            ) from e

    @staticmethod
    def add_user_to_group(session: Session, user: ZulipUser, group: UserGroup) -> None:
        user_ids:list[int] = Usergroup.get_user_ids_for_group(session,group)

        if user.id in user_ids:
            raise DMError(
                f"{user.mention_silent} is already in usergroup '{group.GroupName}'"
            )

        try:
            session.add(UserGroupMember(GroupId=group.GroupId, User=user))
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(
                f"Could not add {user.mention_silent} to usergroup '{group.GroupName}'."
            ) from e

    @staticmethod
    def get_groups_for_user(session: Session, user: ZulipUser) -> list[UserGroup]:
        return (
            session.query(UserGroup)
            .filter(UserGroup.GroupId == UserGroupMember.GroupId)
            .filter(UserGroupMember.User == user)
            .all()
        )
    
    @staticmethod
    def get_user_ids_for_group(session:Session, group:UserGroup) -> list[int]:
        users: list[int] = []
        for s in session.query(UserGroupMember).filter(UserGroupMember.GroupId==group.GroupId).all():
            users.append(s.User.id)
        return users
    
    @staticmethod
    def get_users_for_group(session:Session, group:UserGroup) -> list[ZulipUser]:
        users: list[ZulipUser] = []
        for s in session.query(UserGroupMember).filter(UserGroupMember.GroupId==group.GroupId).all():
            users.append(s.User)
        return users

