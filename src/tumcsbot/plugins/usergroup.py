#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

from typing import Any, Iterable
from sqlalchemy import Column, Integer, PrimaryKeyConstraint, String, ForeignKey
from sqlalchemy.orm import relationship, Mapped

from tumcsbot.lib import Response
from tumcsbot.command_parser import CommandParser
from tumcsbot.db import DB, TableBase
from tumcsbot.plugin import PluginCommandMixin, PluginThread
from tumcsbot.plugin_decorators import *


class UserGroup(TableBase):
    __tablename__ = "UserGroups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True)
    description = Column(String, default="")

    # This will allow accessing the members from the UserGroup object
    members: Mapped[list["UserGroupMember"]] = relationship()


class UserGroupMember(TableBase):
    __tablename__ = "UserGroupMembers"

    gid = Column(
        Integer, ForeignKey("UserGroups.id", ondelete="CASCADE"), primary_key=True
    )
    uid = Column(Integer, primary_key=True)

    # This establishes the relationship between UserGroupMember and UserGroup
    groups: Mapped[list["UserGroup"]] = relationship()

    __contstraints__ = (PrimaryKeyConstraint("uid", "gid"),)


class Usergroup(PluginCommandMixin, PluginThread):
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
    def _list(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
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
                yield DMResponse(
                    f"## {group.name}:\n"
                    + ", ".join(
                        [ZulipUser(uid).mention_silent for uid in group.members]
                        or ["No members"]
                    )
                )

        else:
            if sender.id != args.user.id and not sender.priviliged:
                raise UserNotPrivilegedException("You can only list your own groups.")

            groups = Usergroup.get_groups_for_user(session, args.user.id)

            if len(groups) == 0:
                raise DMError(f"{args.user.mention_silent} is not in any user group")

            msg = ", ".join(f"`{g.name}`" for g in groups)
            yield DMResponse(
                args.user.mention_silent + " is in the following user groups:\n" + msg
            )

    @command
    @privilege(Privilege.ADMIN)
    @arg("group", str, "The group the users should get added to")
    @arg("users", ZulipUser, "The users that should get added", greedy=True)
    def add(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
        """
        Add users to group.
        """
        user: ZulipUser
        for user in args.users:
            if not Usergroup.add_user_to_group(session, user.id, args.group):
                yield PartialError(user.mention_silent)
            else:
                yield DMMessage(
                    user,
                    f"Hey,\nYou have been added to the following user group by @_**{message['sender_full_name']}|{message['sender_id']}**:\n{args.group}"
                )
                yield PartialSuccess(user)

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, "The name of the user group")
    @arg("description", str, "The description of the user group.", optional=True)
    def creat(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
        """
        Create an empty user group
        """
        success = Usergroup.create_group(session, args.name, args.description)

        if not success:
            raise DMError(
                f"Error: Could not create group '{args.name}' with description '{args.description}'. Maybe the group already exists?",
            )

        yield Response.ok(message)

    @command
    @arg("group", str, "The group the user should get removed from")
    @arg(
        "user",
        ZulipUser,
        "The user that should get removed from groups. Default is the sender of the command",
        optional=True,
    )
    @opt(
        "s",
        long_opt="silent",
        description="Do not notify the user",
        privilege=Privilege.ADMIN,
    )
    def remove(
        self,
        sender: ZulipUser,
        session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> Response | Iterable[Response]:
        """
        remove user from group
        """
        if args.user != sender and not sender.priviliged:
            raise UserNotPrivilegedException(
                "You can only remove yourself from groups."
            )

        if args.user is None:
            args.user = sender

        Usergroup.remove_user_from_group(session, args.user.id, args.group)
        if args.user != sender:
            yield Response.ok(message)

        if not opts.s:
            yield DMMessage(
                args.user,
                f"Hey,\nYou have been removed to the following user group by @_**{message['sender_full_name']}|{message['sender_id']}**:\n`{args.group}`",
            )

    # TODO: replacement for zulip usergroups. Rreplace as soon as api allows bot requests for usergroups

    @staticmethod
    def get_groups(session) -> list[UserGroup]:
        return session.query(UserGroup).all()

    @staticmethod
    def create_group(session, name: str, description: str) -> bool:
        group = UserGroup(name=name, description=description)
        session.add(group)
        try:
            session.commit()
        except Exception as e:
            session.rollback()  # Assuming uniqueness violation or similar
            print(str(e))
            return False  # todo: replace with exception
        return True

    @staticmethod
    def delete_group(session, identifier: int | str) -> bool:
        gid = Usergroup.group_id_by_identifier(session, identifier)
        if gid is None:
            return False
        session.query(UserGroup).filter(UserGroup.id == gid).delete()
        session.commit()
        return True

    @staticmethod
    def remove_user_from_group(
        session, user_identifier: int | str, group_identifier: int | str
    ) -> bool:
        uid = Usergroup.user_id_by_identifier(session, user_identifier)
        gid = Usergroup.group_id_by_identifier(session, group_identifier)
        if uid is None or gid is None:
            return False
        session.query(UserGroupMember).filter(
            UserGroupMember.uid == gid, UserGroupMember.uid == uid
        ).delete()
        session.commit()
        return True

    @staticmethod
    def add_user_to_group(
        session, user_identifier: int | str, group_identifier: int | str
    ) -> bool:
        uid = Usergroup.user_id_by_identifier(session, user_identifier)
        gid = Usergroup.group_id_by_identifier(session, group_identifier)
        if uid is None or gid is None:
            return False
        session.add(UserGroupMember(id=gid, uid=uid))
        session.commit()
        return True

    @staticmethod
    def get_groups_for_user(session, user_identifier: int | str) -> list[UserGroup]:
        uid = Usergroup.user_id_by_identifier(session, user_identifier)
        return (
            session.query(UserGroup)
            .join(UserGroupMember)
            .filter(UserGroupMember.uid == UserGroup.id)
            .filter(UserGroupMember.uid == uid)
            .all()
        )

    @staticmethod
    def get_group_id_by_name(session, group_name: str) -> int | None:
        group = session.query(UserGroup).filter(UserGroup.name == group_name).first()
        return group.id if group is not None else None

    @staticmethod
    def group_id_by_identifier(session, identifier: int | str) -> int | None:
        if isinstance(identifier, int):
            return int(identifier)

        return Usergroup.get_group_id_by_name(session, str(identifier))

    @staticmethod
    def get_group_by_identifier(session, identifier: int | str) -> UserGroup | None:
        gid = Usergroup.group_id_by_identifier(session, identifier)
        if gid is None:
            return None

        return session.query(UserGroup).filter(UserGroup.id == gid).first()

    @staticmethod
    def get_group_members(session, identifier: int | str) -> list[int]:
        g = Usergroup.get_group_by_identifier(session, identifier)
        if g is None:
            return []
        members: list[int] = g.members
        return members

    def user_id_by_identifier(self, identifier: int | str) -> int | None:
        if isinstance(identifier, int):
            return int(identifier)
        return self.client.get_user_id_by_name(str(identifier))
