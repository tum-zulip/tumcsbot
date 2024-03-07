#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

from typing import Any, Iterable
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship

from tumcsbot.lib import Response
from tumcsbot.command_parser import CommandParser
from tumcsbot.db import DB, TableBase
from tumcsbot.plugin import PluginCommandMixin, PluginThread
from tumcsbot.plugin_decorators import *

class UserGroup(TableBase):
    __tablename__ = 'UserGroups'

    GroupId = Column(Integer, primary_key=True, autoincrement=True)
    UGroup = Column(String, unique=True)
    description = Column(String)

    # This will allow accessing the members from the UserGroup object
    members = relationship("UserGroupMember", back_populates="group")

class UserGroupMember(TableBase):
    __tablename__ = 'UserGroupMembers'

    GroupId = Column(Integer, ForeignKey('UserGroups.GroupId', ondelete='CASCADE'), primary_key=True)
    UserId = Column(Integer, primary_key=True)

    # This establishes the relationship between UserGroupMember and UserGroup
    group = relationship("UserGroup", back_populates="members")


class Usergroup(PluginCommandMixin, PluginThread):

    def _init_plugin(self) -> None:
        # Get own database connection.
        self._db: DB = DB()
        # Check for database table.

    @command(name="list")
    @arg("user", str, "The user for which the groups should be listed", optional=True)
    @opt("a", long_opt="all", description="Display all user groups with all users", privilege=Privilege.ADMIN)
    def _list(
        self,
        message: dict[str, Any],
        args: CommandParser.Args,
        opts: CommandParser.Opts,
    ) -> Response | Iterable[Response]:
        """
        List user groups
        """
        user_id: int | None
        uid: int

        if args.user is not None:
            user_id = self.client.get_user_id_by_name(args.user)
            if user_id is None:
                return Response.build_message(
                    message, f"User not found: {args.user}", msg_type="private"
                )
            uid = user_id
        else:
            uid = message["sender_id"]
            user_result = self.client.get_user_by_id(uid)
            if user_result is None or user_result["result"] != "success":
                return Response.build_message(
                    message, f"User with id {uid} not found.", msg_type="private"
                )

            args.user = f"@_**{user_result['user']['full_name']}|{user_result['user']['user_id']}**"

        if opts.a:
            groups: list[UserGroup]
            with DB.session() as session:
                groups = Usergroup.get_groups(session=session)
            
            result_dict: dict[str, list[str]] = {}
            for group in groups:
                group_name = group["name"]
                result_dict[group_name] = list()
                for uid in group["members"]:
                    user_result = self.client.get_user_by_id(uid)
                    if user_result["result"] != "success":
                        return Response.build_message(
                            message,
                            f"An error occurred while querying user with ID {uid}",
                            msg_type="private",
                        )
                    result_dict[group_name].append(
                        f"@_**{user_result['user']['full_name']}|{uid}**"
                    )

            result_list = [
                f"## {group}:\n[" + ", ".join(users) + "]"
                for group, users in result_dict.items()
            ]
            return Response.build_message(
                message,
                "# Usergroups:\n" + "\n".join(result_list),
                msg_type="private",
            )
        else:
            if message["sender_id"] != uid and not self.client.user_is_privileged(
                message["sender_id"]
            ):
                return Response.privilege_err(message)
            
            with DB.session() as session:
                groups = Usergroup.get_groups_for_user(session=session, user_identifier=uid)

            if len(groups) == 0:
                return Response.build_message(
                    message, f"{args.user} is not in any user group", msg_type="private"
                )

            msg = ", ".join(f"`{g.UGroup}`" for g in groups)
            return Response.build_message(
                message,
                f"{args.user} is in the following user groups: {msg}",
                msg_type="private",
            )

    @command
    @privilege(Privilege.ADMIN)
    @arg("users", str, "The users that should get added to groups", greedy=True)
    @arg("groups", str, "The groups the users should get added to", greedy=True)
    def add(
        self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts
    ) -> Response | Iterable[Response]:
        """
        Add users to groups.
        """

        failures: dict[str, list[str]] = {}
        success: dict[str, list[str]] = {}

        if len(args.users) == 0 or len(args.groups) == 0:
            return Response.build_message(
                message,
                "Error: At least one user and one group must be specified.",
                msg_type="private",
            )

        for user in args.users:
            success[user] = list()
            failures[user] = list()
            for group in args.groups:
                if not self.add_user_to_group(user, group):
                    failures[user].append(group)
                else:
                    success[user].append(group)

        responses = []

        for user, groups in failures.items():
            if len(groups) > 0:
                groups_str = ", ".join(groups)
                responses.append(
                    Response.build_message(
                        message,
                        f"Error: Could not add user '{user}' to groups '{groups_str}'",
                        msg_type="private",
                    )
                )

        for user, groups in success.items():
            if len(groups) > 0:
                uid = self.client.get_user_id_by_name(user)
                if uid:
                    groups_str = ", ".join([f"`{g}`" for g in groups])
                    responses.append(
                        Response.build_message(
                            message=None,
                            msg_type="private",
                            content=f"Hey,\nYou have been added to the following user groups by @_**{message['sender_full_name']}|{message['sender_id']}**:\n{groups_str}",
                            to=[uid],
                        )
                    )
                else:
                    groups_str = ", ".join(groups)
                    responses.append(
                        Response.build_message(
                            message,
                            f"Error: Could not find id for user {user}.",
                            msg_type="private",
                        )
                    )
        responses.append(Response.ok(message))
        return responses

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, "The name of the user group")
    @arg("description", str, "The description of the user group (required for portability with builtin zulip usergroups)")
    def creat(
        self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts
    ) -> Response | Iterable[Response]:
        """
        Create an empty user group
        """
        success = self.create_group(args.name, args.description)

        if not success:
            return Response.build_message(
                message,
                f"Error: Could not create group '{args.name}' with description '{args.description}'.",
                msg_type="private",
            )

        return Response.ok(message)

    @command
    @arg("user", str, "The user that should get removed from groups. If no group is not specified, the user gets removed from all groups.", optional=True)
    @arg("group", str, "The group the user should get removed from. If no user is not specified, all users gets removed from this groups.", optional=True)
    def remove(
        self, message: dict[str, Any], args: CommandParser.Args, _: CommandParser.Opts
    ) -> Response | Iterable[Response]:
        """
        remove user(s) from group(s)
        """
        user_id = None

        if args.user is not None:
            user_id = self.client.get_user_id_by_name(args.user)
            if user_id is None:
                return Response.build_message(
                    message, f"Error: User not found: {args.user}", msg_type="private"
                )

        if (
            user_id
            and message["sender_id"] != user_id
            and not self.client.user_is_privileged(message["sender_id"])
        ):
            return Response.privilege_err(message)

        responses = []
        if args.user is not None and args.group is not None:
            self.remove_user_from_group(args.user, args.group)
            uid = self.client.get_user_id_by_name(args.user)
            if uid:
                responses.append(
                    Response.build_message(
                        message=None,
                        msg_type="private",
                        content=f"Hey,\nYou have been removed to the following user group by @_**{message['sender_full_name']}|{message['sender_id']}**:\n`{args.group}`",
                        to=[uid],
                    )
                )
            else:
                responses.append(
                    Response.build_message(
                        message,
                        f"Error: Could not find id for user {args.user}.",
                        msg_type="private",
                    )
                )
        elif args.user is not None:
            uid = self.client.get_user_id_by_name(args.user)
            if not uid:
                responses.append(
                    Response.build_message(
                        message,
                        f"Error: Could not find id for user {args.user}.",
                        msg_type="private",
                    )
                )
            else:
                groups = self.get_groups_for_user(args.user)
                for gid in groups:
                    self.remove_user_from_group(args.user, gid)
                print(groups)
                names = [
                    g_dict["name"]
                    for g_dict in [self.get_group_by_identifier(g) for g in groups]
                    if g_dict
                ]
                print([self.get_group_by_identifier(g) for g in groups])
                print("§" * 100)
                names_str = ", ".join([f"`{n}`" for n in names])
                responses.append(
                    Response.build_message(
                        message=None,
                        msg_type="private",
                        content=f"Hey,\nYou have been removed from the following user groups by @_**{message['sender_full_name']}|{message['sender_id']}**:\n[{names_str}]",
                        to=[uid],
                    )
                )
        elif args.group is not None:
            for member in self.get_group_members(args.group):
                responses.append(
                    Response.build_message(
                        message=None,
                        msg_type="private",
                        content=f"Hey,\nYou have been removed from the following user group by @_**{message['sender_full_name']}|{message['sender_id']}**:\n`{args.group}`",
                        to=[member],
                    )
                )
            self.delete_group(args.group)
        else:
            return Response.build_message(
                message,
                f"Error: At least a user or a group must be specified.",
                msg_type="private",
            )

        responses.append(Response.ok(message))
        return responses

    # TODO: replacement for zulip usergroups. Rreplace as soon as api allows bot requests for usergroups
    
    @staticmethod
    def get_groups(session): # todo: -> list[UserGroup]:
        return session.query(UserGroup).all()
    
    @staticmethod
    def create_group(session, name: str, description: str) -> bool:
        group = UserGroup(UGroup=name, Description=description)
        session.add(group)
        try:
            session.commit()
        except:
            session.rollback()  # Assuming uniqueness violation or similar
            return False # todo: replace with exception
        return True
    
    @staticmethod
    def delete_group(session, identifier: int | str) -> bool:
        gid = Usergroup.group_id_by_identifier(session, identifier)
        if gid is None:
            return False
        session.query(UserGroup).filter(UserGroup.GroupId == gid).delete()
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
        session.query(UserGroupMember).filter(UserGroupMember.GroupId == gid, UserGroupMember.UserId == uid).delete()
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
        session.add(UserGroupMember(GroupId=gid, UserId=uid))
        session.commit()
        return True

    @staticmethod
    def get_groups_for_user(session, user_identifier: int | str) -> list[UserGroup]:
        uid = Usergroup.user_id_by_identifier(session, user_identifier)
        return session.query(UserGroup).join(UserGroupMember).filter(UserGroupMember.GroupId == UserGroup.GroupId).filter(UserGroupMember.UserId == uid).all()

    @staticmethod
    def get_group_id_by_name(session, group_name: str) -> int | None:
        group = session.query(UserGroup).filter(UserGroup.UGroup == group_name).first()
        if group:
            return group.GroupId
        else:
            return None
    
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

        return session.query(UserGroup).filter(UserGroup.GroupId == gid).first()

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
