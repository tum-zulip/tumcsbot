#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

# TODO: replacement for zulip usergroups. Replace as soon as api allows bot requests for usergroups

from typing import Any, AsyncGenerator
from sqlalchemy import Column, Integer, PrimaryKeyConstraint, String, ForeignKey
import sqlalchemy
from sqlalchemy.orm import relationship, Mapped

from tumcsbot.command_parser import CommandParser
from tumcsbot.db import Session, TableBase
from tumcsbot.plugin import PluginCommandMixin, Plugin, Privilege, ZulipUser
from tumcsbot.plugin_decorators import (
    DMError,
    DMMessage,
    DMResponse,
    PartialError,
    PartialSuccess,
    UserNotPrivilegedException,
    command,
    arg,
    opt,
    privilege,
    response_type,
)


class UserGroup(TableBase):
    """Represents a user group in the system."""

    __tablename__ = "UserGroups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True)
    description = Column(String, default="")

    # This will allow accessing the members from the UserGroup object
    members: Mapped[list["UserGroupMember"]] = relationship(viewonly=True)


class UserGroupMember(TableBase):
    """Represents a user group member in the system."""

    __tablename__ = "UserGroupMembers"

    gid = Column(
        Integer, ForeignKey("UserGroups.id", ondelete="CASCADE"), primary_key=True
    )
    uid = Column(Integer, primary_key=True)

    # This establishes the relationship between UserGroupMember and UserGroup
    groups: Mapped[list["UserGroup"]] = relationship(viewonly=True)

    __contstraints__ = (PrimaryKeyConstraint("uid", "gid"),)


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
        if args.user is None:
            args.user = sender

        if opts.a:
            groups: list[UserGroup]
            groups = session.query(UserGroup).all()
            if len(groups) == 0:
                raise DMError(f"No user groups found")

            for group in groups:
                members = [
                    await ZulipUser(int(member.uid)).mention_silent
                    for member in group.members
                ] or ["No members"]

                yield DMResponse(f"## {group.name}:\n" + ", ".join(members))

        else:
            if await sender.id != await args.user.id and not await sender.privileged:
                # todo: normal exceptions
                raise UserNotPrivilegedException(
                    "You can only list your own groups.",
                    Privilege.ADMIN,
                    "usergroup list",
                )

            groups = await Usergroup.get_groups_for_user(session, args.user)

            if len(groups) == 0:
                raise DMError(
                    f"{await args.user.mention_silent} is not in any user group"
                )

            msg = ", ".join(f"`{g.name}`" for g in groups)
            yield DMResponse(
                await args.user.mention_silent
                + " is in the following usergroups:\n"
                + msg
            )

    @command
    @privilege(Privilege.ADMIN)
    @arg("group", str, "The group the users should get added to")
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
        s_ention = await sender.mention_silent
        for user in args.users:
            try:
                await Usergroup.add_user_to_group(session, user, args.group)
                yield DMMessage(
                    user,
                    f"Hey {await user.mention_silent},\nYou have been added to the usergroup `{args.group}` by {s_ention}",
                )
                yield PartialSuccess(await user.mention_silent)
            except DMError as e:
                yield PartialError(str(e))

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, "The name of the user group")
    @arg("description", str, "The description of the user group.", optional=True)
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
        if args.description is None:
            args.description = "No description available"
        await Usergroup.create_group(session, args.name, args.description)
        yield DMResponse(f"User group `{args.name}` created")

    @command
    @privilege(Privilege.ADMIN)
    @arg("group", str, "The group the user should get removed from")
    @arg(
        "user",
        ZulipUser,
        "The user that should get removed from groups. If no user is given, the entire group will be deleted.",
        optional=True,
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
        sid = await sender.id
        s_mention = await sender.mention_silent

        if args.user is None:
            # delete the group
            if not opts.s:
                # notify all members
                for user in await Usergroup.get_group_members(session, args.group):
                    yield DMMessage(
                        user,
                        f"Hey {await user.mention_silent},\nYou have been removed from the usergroup {args.group} by {s_mention}, because the group has been deleted.",
                    )
                await Usergroup.delete_group(session, args.group)
                yield DMResponse(f"User group `{args.group}` has been deleted")
        else:
            uid = await args.user.id
            u_mention = await args.user.mention_silent

            await Usergroup.remove_user_from_group(session, args.user, args.group)
            yield PartialSuccess(u_mention)

            if not opts.s and uid != sid:
                yield DMMessage(
                    args.user,
                    f"Hey {await args.user.mention_silent},\nYou have been removed to the usergroup by {s_mention}:\n`{args.group}`",
                )

    @command
    @arg("group", str, "The group you wish to leave")
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
        await Usergroup.remove_user_from_group(session, sender, args.group)
        yield DMResponse(f"You have left the usergroup `{args.group}`")

    @staticmethod
    def get_groups(session: Session) -> list[UserGroup]:
        return session.query(UserGroup).all()

    @staticmethod
    async def create_group(session: Session, name: str, description: str) -> None:
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
        group = UserGroup(name=name, description=description)
        try:
            session.add(group)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not create group '{name}'. {str(e)}") from e

    @staticmethod
    async def delete_group(session: Session, identifier: int | str) -> None:
        """
        Delete a user group from the database.

        Args:
            session (Session): The database session.
            identifier (int | str): The identifier of the group to delete.

        Raises:
            DMError: If the group cannot be deleted.

        Returns:
            None
        """
        g = await Usergroup.group_by_identifier(session, identifier)
        try:
            session.query(UserGroup).filter(UserGroup.id == g.id).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not delete group '{g.name}'. {str(e)}") from e

    @staticmethod
    async def remove_user_from_group(
        session: Session, user: ZulipUser, group_identifier: int | str
    ) -> None:
        """
        Remove a user from a user group.

        Args:
            session: The database session.
            user (ZulipUser): The user to be removed from the group.
            group_identifier (int | str): The identifier of the group.

        Raises:
            DMError: If the user is not in the specified user group.

        Returns:
            None
        """
        uid = await user.id
        g = await Usergroup.group_by_identifier(session, group_identifier)

        relation = (
            session.query(UserGroupMember)
            .filter(UserGroupMember.uid == uid)
            .filter(UserGroupMember.gid == g.id)
        )

        if relation.first() is None:
            raise DMError(f"{await user.mention_silent} is not in usergroup '{g.name}'")

        relation.delete()
        session.commit()

    @staticmethod
    async def add_user_to_group(
        session: Session, user: ZulipUser, group_identifier: int | str
    ) -> None:
        """
        Add a user to a group.

        Args:
            session: The database session.
            user: The user to be added to the group.
            group_identifier: The identifier of the group to add the user to.

        Raises:
            DMError: If the user is already in the group.

        """
        uid = await user.id
        g = await Usergroup.group_by_identifier(session, group_identifier)
        try:
            session.add(UserGroupMember(gid=g.id, uid=uid))
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(
                f"Could add {await user.mention_silent} usergroup '{group_identifier}'. Maybe the user is already in the group?"
            ) from e

    @staticmethod
    async def get_groups_for_user(session: Session, user: ZulipUser) -> list[UserGroup]:
        """
        Retrieve a list of UserGroup objects for a given user.

        Args:
            session: The database session.
            user: The ZulipUser object representing the user.

        Returns:
            A list of UserGroup objects that the user belongs to.
        """
        return (
            session.query(UserGroup)
            .filter(UserGroup.id == UserGroupMember.gid)
            .filter(UserGroupMember.uid == await user.id)
            .all()
        )

    @staticmethod
    async def group_by_identifier(session: Session, identifier: int | str) -> UserGroup:
        """
        Retrieve a UserGroup object based on the given identifier.

        Args:
            session (Session): The database session.
            identifier (int | str): The identifier of the group. It can be either an integer representing the group ID
                or a string representing the group name.

        Returns:
            UserGroup: The UserGroup object matching the identifier.

        Raises:
            DMError: If no group is found with the given identifier.
        """
        if isinstance(identifier, int):
            group = session.query(UserGroup).filter(UserGroup.id == identifier).first()
            if group is None:
                raise DMError(f"Could not find group with id {identifier}")
        else:
            group = (
                session.query(UserGroup).filter(UserGroup.name == identifier).first()
            )
            if group is None:
                raise DMError(f"Could not find group `{identifier}`")

        return group

    @staticmethod
    async def get_group_members(
        session: Session, identifier: int | str
    ) -> list[ZulipUser]:
        """
        Get the members of a user group.

        Args:
            session: The session object for database operations.
            identifier: The identifier of the user group.

        Returns:
            A list of ZulipUser objects representing the members of the user group.
        """
        g = await Usergroup.group_by_identifier(session, identifier)
        return [ZulipUser(int(m.uid)) for m in g.members]
