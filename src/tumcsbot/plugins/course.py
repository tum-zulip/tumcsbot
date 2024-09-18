#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import asyncio
from collections.abc import Iterable as IterableClass
import difflib
from inspect import cleandoc
import inspect
import re
import logging
from typing import Any, AsyncGenerator, TypeVar

from sqlite3 import IntegrityError
from typing import Coroutine, Literal, cast, Any, Callable, Iterable, AsyncGenerator
import sqlalchemy
from sqlalchemy import (
    Column,
    String,
    Integer,
    ForeignKey,
    UniqueConstraint,
    update,
    Boolean,
)
from sqlalchemy.orm import relationship, Mapped
from sqlalchemy.ext.hybrid import hybrid_property
from tumcsbot.lib.regex import Regex

from tumcsbot.lib.response import Response
from tumcsbot.lib.client import AsyncClient
from tumcsbot.plugin import Event, Plugin, PluginCommandMixin
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import DB, TableBase, Session, TableBase, serialize_model
from tumcsbot.plugin_decorators import *
from tumcsbot.plugins.usergroup import UserGroup, Usergroup
from tumcsbot.plugins.userinput import UserInput
from tumcsbot.plugins.channelgroup import ChannelGroup, Channelgroup
from tumcsbot.plugins.channels import Channels
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
    ZulipChannel,
    YAMLSerializableMixin,
)

T = TypeVar("T")

class CourseDB(TableBase):  # type: ignore
    """Represents a course in the system."""

    __tablename__ = "Courses"

    CourseId = Column(Integer, primary_key=True, autoincrement=True)
    CourseName = Column(String, unique=True)
    CourseDescription = Column(String, nullable=True)
    CourseLanguage = Column(String, nullable=False)

    Channels = Column(
        String,
        ForeignKey("ChannelGroups.ChannelGroupId", ondelete="CASCADE"),
        nullable=False,
    )

    TutorsUserGroup = Column(
        Integer, ForeignKey("UserGroups.GroupId", ondelete="CASCADE"), nullable=False
    )

    InstructorsUserGroup = Column(
        Integer, ForeignKey("UserGroups.GroupId", ondelete="CASCADE"), nullable=False
    )

    TutorChannel = Column(ZulipChannel, nullable=True)
    InstructorChannel = Column(ZulipChannel, nullable=True)
    FeedbackChannel = Column(ZulipChannel, nullable=True) # not Null if anonymous feedback enabled

    _channels = relationship(
        "ChannelGroup",
        back_populates="_course",
        cascade="all, delete-orphan",
        single_parent=True,
    )
    _tutors = relationship(
        "UserGroup",
        back_populates="_courseT",
        cascade="all, delete-orphan",
        single_parent=True,
        foreign_keys="CourseDB.TutorsUserGroup",
    )

    _instructors = relationship(
        "UserGroup",
        back_populates="_courseI",
        cascade="all, delete-orphan",
        single_parent=True,
        foreign_keys="CourseDB.InstructorsUserGroup",
    )


class Course(PluginCommandMixin, Plugin):
    """
    Manage Courses.
    """

    # ========================================================================================================================
    #       SUBCOMMANDS
    # ========================================================================================================================

    @command(name="list")
    @privilege(Privilege.ADMIN)
    async def _list(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List all Courses with their associated Channels.
        """
        response: str = "Course Name | Emoji | Channels \n---- | ---- | ----"

        courses: list[CourseDB] = session.query(CourseDB).all()

        if len(courses) == 0:
            raise DMError(f"No courses found")

        for course in courses:
            course_name = course.CourseName
            channels: list[str] = await Course.get_channel_names(
                session, self.client, course
            )
            emoji: str = Course.get_emoji(course, session)

            channels_concat: str = ", ".join(f"`{s}`" for s in channels)
            response += f"\n{course_name} | {emoji} :{emoji}: | {channels_concat}"

        yield DMResponse(response)

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the Course.")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the Course.")
    @opt(
        "i",
        long_opt="instructors",
        description="The course has an additional Channel for Instructors.",
    )
    @opt(
        "f",
        long_opt="feedback",
        description="The course has an ANONYMOUS Feedback-Channel.",
    )
    async def create_empty(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Create a new empty course
        """
        name: str = args.name
        channelgroup_emoji: str = args.emoji
        channels: ChannelGroup | None = None

        cleanup_opterations: list[Callable] = []

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result1 = await self.client.send_response(
                Response.build_message(
                    message,
                    content=f"Course `{name}` already exists. Dou you want to replace it with an empty course?",
                )
            )
            if result1["result"] != "success":
                raise DMError("Could not send message to user")

            resp1, _ = await UserInput.confirm(self.client, result1["id"], timeout=60)
            if not resp1:
                yield DMResponse(
                    "Ok, I will not create a new course. Please choose another name."
                )
                return

        if (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupEmote == channelgroup_emoji)
            .first()
            is not None
        ):
            result2 = await self.client.send_response(
                Response.build_message(
                    message,
                    content=f"A Channelgroup with :{channelgroup_emoji}: already exists. Dou you want to replace it with an empty Channelgroup for your course?",
                )
            )
            if result2["result"] != "success":
                raise DMError("Could not send message to user")

            resp2, _ = await UserInput.confirm(self.client, result2["id"], timeout=60)
            if not resp2:
                res = await self.client.send_response(
                    Response.build_message(
                        message,
                        content=f"Do you want to use the existing Channelgroup with :{channelgroup_emoji}: for your course?",
                    )
                )
                if res["result"] != "success":
                    raise DMError("Could not send message to user")

                rep, _ = await UserInput.confirm(self.client, res["id"], timeout=60)
                if not rep:
                    yield DMResponse(
                        f"Ok, I will not create a new course then. Please choose another emote because :{channelgroup_emoji}: is already in use :botsad:"
                    )
                    return
                else:
                    channels = (
                        session.query(ChannelGroup)
                        .filter(ChannelGroup.ChannelGroupEmote == channelgroup_emoji)
                        .first()
                    )
            else:
                sg: ChannelGroup | None = (
                    session.query(ChannelGroup)
                    .filter(ChannelGroup.ChannelGroupEmote == channelgroup_emoji)
                    .first()
                )
                if sg is not None:
                    Channelgroup._delete_group(session, sg)

        resLan = await self.client.send_response(
            Response.build_message(
                message, content=f"Is your course held in English or German?"
            )
        )
        lan, _ = await UserInput.i8n_german_or_english(
            self.client, resLan["id"], timeout=60
        )

        try:
            # get a corresponding (empty) Channelgroup
            if not channels:
                channelgroup_name: str = "channels_" + name
                channels = Channelgroup._create_and_get_group(
                    session, channelgroup_name, channelgroup_emoji
                )
                cleanup_opterations.append(
                    lambda: Channelgroup._delete_group(session, channels)
                )

            # get a corresponding (empty) Usergroup
            usergroup_name_tut: str = "tutors_" + name

            if (
                session.query(UserGroup)
                .filter(UserGroup.GroupName == usergroup_name_tut)
                .first()
                is not None
            ):
                result3 = await self.client.send_response(
                    Response.build_message(
                        message,
                        content=f"Usergroup for Tutors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?",
                    )
                )
                if result3["result"] != "success":
                    raise DMError("Could not send message to user")

                resp3, _ = await UserInput.confirm(
                    self.client, result3["id"], timeout=60
                )
                if not resp3:
                    raise DMError(
                        "Ok, I will not create a new course then. Please choose another name."
                    )
                else:
                    ugt: UserGroup | None = (
                        session.query(UserGroup)
                        .filter(UserGroup.GroupName == usergroup_name_tut)
                        .first()
                    )
                    if ugt is not None:
                        Usergroup.delete_group(session, ugt)

            tutors = Usergroup.create_and_get_group(session, usergroup_name_tut)
            cleanup_opterations.append(lambda: Usergroup.delete_group(session, tutors))

            # get a corresponding (empty) Usergroup
            usergroup_name_ins: str = "instructors_" + name

            if (
                session.query(UserGroup)
                .filter(UserGroup.GroupName == usergroup_name_ins)
                .first()
                is not None
            ):
                result4 = await self.client.send_response(
                    Response.build_message(
                        message,
                        content=f"Usergroup for Instructors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?",
                    )
                )
                if result4["result"] != "success":
                    raise DMError("Could not send message to user")

                resp4, _ = await UserInput.confirm(
                    self.client, result4["id"], timeout=60
                )
                if not resp4:
                    raise DMError(
                        "Ok, I will not create a new course then. Please choose another name."
                    )
                else:
                    ugi: UserGroup | None = (
                        session.query(UserGroup)
                        .filter(UserGroup.GroupName == usergroup_name_ins)
                        .first()
                    )
                    if ugi is not None:
                        Usergroup.delete_group(session, ugi)

            instructors = Usergroup.create_and_get_group(session, usergroup_name_ins)
            cleanup_opterations.append(
                lambda: Usergroup.delete_group(session, instructors)
            )

            # get a corresponding (empty) Channel for Tutors
            tutors_channel_name: str = name + " - Tutors"
            tutors_channel_desc: list[str] = [f"Internal Channel for {name}-Tutors"]
            if lan == "de":
                tutors_channel_name = name + " - Tutoren"
                tutors_channel_desc = [f"Interner Kanal für {name}-Tutoren"]

            tut_ex = await self.client.get_channel_id_by_name(tutors_channel_name)

            if tut_ex is not None:
                result5 = await self.client.send_response(
                    Response.build_message(
                        message,
                        content=f"Channel for Tutors of this course already exists. Dou you want to replace it with a new empty Channel for your course?",
                    )
                )
                if result5["result"] != "success":
                    raise DMError("Could not send message to user")

                resp5, _ = await UserInput.confirm(
                    self.client, result5["id"], timeout=60
                )
                if not resp5:
                    raise DMError(
                        "Ok, I will not create a new course then. Please choose another name."
                    )
                else:
                    await self.client.delete_channel(tut_ex)

            result_tut_s: dict[str, Any] = await self.client.add_subscriptions(
                channels=[
                    {
                        "name": tutors_channel_name,
                        "description": " ".join(tutors_channel_desc),
                    }
                ],
                principals=[sender.id, self.client.id],
                invite_only=True,
                history_public_to_subscribers=True,
            )

            if result_tut_s["result"] != "success":
                raise DMError(result_tut_s["msg"])

            tutors_channel: ZulipChannel = ZulipChannel(f"#**{tutors_channel_name}**")
            await tutors_channel

            cleanup_opterations.append(
                lambda: self.client.delete_channel(tutors_channel.id)
            )

            # get a corresponding (empty) Channel for Instructors or None
            instructor_channel: ZulipChannel | None = None
            if opts.i:

                instructor_channel_name: str = name + " - Instructors"
                instructor_channel_desc: list[str] = [
                    f"Internal Channel for Instructors of {name}"
                ]
                if lan == "de":
                    instructor_channel_name = name + " - Instructors"
                    instructor_channel_desc = [
                        f"Interner Kanal für {name}-Instructors"
                    ]

                ins_ex = await self.client.get_channel_id_by_name(instructor_channel_name)

                if ins_ex is not None:
                    result6 = await self.client.send_response(
                        Response.build_message(
                            message,
                            content=f"Channel for Instructors of this course already exists. Dou you want to replace it with a new empty Channel for your course?",
                        )
                    )
                    if result6["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp6, _ = await UserInput.confirm(
                        self.client, result6["id"], timeout=60
                    )
                    if not resp6:
                        raise DMError(
                            "Ok, I will not create a new course then. Please choose another name."
                        )
                    else:
                        await self.client.delete_channel(ins_ex)

                result_ins_s: dict[str, Any] = await self.client.add_subscriptions(
                    channels=[
                        {
                            "name": instructor_channel_name,
                            "description": " ".join(instructor_channel_desc),
                        }
                    ],
                    principals=[sender.id, self.client.id],
                    invite_only=True,
                    history_public_to_subscribers=True,
                )

                if result_ins_s["result"] != "success":
                    raise DMError(result_ins_s["msg"])

                instructor_channel = ZulipChannel(
                    f"#**{instructor_channel_name}**"
                )
                await instructor_channel

                cleanup_opterations.append(
                    lambda: self.client.delete_channel(instructor_channel.id)
                )

            # get a corresponding (empty) Channel for anonymous Feedback or None
            feedback_channel: ZulipChannel | None = None
            if opts.f:

                feedback_channel_name: str = name + " - Feedback"
                feedback_channel_desc: list[str] = [
                    f"Anonymous Channel for Feedback to {name}"
                ]
                if lan == "de":
                    feedback_channel_desc = [
                        f"Anonymer Kanal für Feedback zu {name}"
                    ]

                f_ex = await self.client.get_channel_id_by_name(feedback_channel_name)

                if ins_ex is not None:
                        await self.client.delete_channel(f_ex)

                result_fb_s: dict[str, Any] = await self.client.add_subscriptions(
                    channels=[
                        {
                            "name": feedback_channel_name,
                            "description": " ".join(feedback_channel_desc),
                        }
                    ],
                    principals=[sender.id, self.client.id],
                )

                if result_fb_s["result"] != "success":
                    raise DMError(result_fb_s["msg"])

                feedback_channel = ZulipChannel(
                    f"#**{feedback_channel_name}**"
                )
                await feedback_channel

                cleanup_opterations.append(
                    lambda: self.client.delete_channel(feedback_channel.id)
                )


            # create and add a Course to the DB
            course: CourseDB = CourseDB(
                CourseName=name,
                CourseLanguage=lan,
                Channels=channels.ChannelGroupId,
                TutorsUserGroup=tutors.GroupId,
                InstructorsUserGroup=instructors.GroupId,
                TutorChannel=tutors_channel,
                InstructorChannel=instructor_channel,
                FeedbackChannel=feedback_channel,
            )

            session.add(course)
            session.commit()

        except Exception as e:
            session.rollback()
            for cleanup in cleanup_opterations:
                if inspect.iscoroutinefunction(cleanup):
                    await cleanup()
                else:
                    cleanup()
            if isinstance(e, DMError):
                raise e

            raise DMError(
                f"Something went wrong when creating the course `{name}` :botsweat:"
            ) from e

        yield DMResponse(f"Course `{name}` created :bothappypad:")

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the Course")
    @arg(
        "emoji",
        Regex.get_emoji_name,
        description="The emoji to use for the Channelgroup.",
    )
    @opt(
        "c",
        long_opt="channelgroup",
        type=ChannelGroup.ChannelGroupId,
        description="The id of a Channelgroup containing the Channels for this course.",
    )
    @opt(
        "t",
        long_opt="tutors",
        type=UserGroup.GroupName,
        description="The name of a Usergroup containing the tutors for this course.",
    )
    @opt(
        "i",
        long_opt="instructors",
        type=UserGroup.GroupName,
        description="The name of a Usergroup containing the instructors for this course.",
    )
    @opt(
        "tuts",
        long_opt="tutor_channel",
        type=ZulipChannel,
        description="The course has an additional Channel for tutors.",
    )
    @opt(
        "ins",
        long_opt="instructor_channel",
        type=ZulipChannel,
        description="The course has an additional Channel for Instructors.",
    )
    @opt(
        "fb",
        long_opt="feedback",
        type=ZulipChannel,
        description="The course has an ANONYMOUS Feedback-Channel.",
    )
    async def create(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Create a new course with corresponding contents
        """
        name: str = args.name
        channelgroup_emoji: str = args.emoji
        channels: ChannelGroup

        cleanup_opterations: list[Callable] = []

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result1 = await self.client.send_response(
                Response.build_message(
                    message,
                    content=f"Course `{name}` already exists. Dou you want to replace it with the new course?",
                )
            )
            if result1["result"] != "success":
                raise DMError("Could not send message to user")

            resp1, _ = await UserInput.confirm(self.client, result1["id"], timeout=60)
            if not resp1:
                yield DMResponse(
                    "Ok, I will not create a new course. Please choose another name."
                )
                return

        if (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupEmote == channelgroup_emoji)
            .first()
            is not None
        ):
            result2 = await self.client.send_response(
                Response.build_message(
                    message,
                    content=f"A Channelgroup with :{channelgroup_emoji}: already exists. Dou you want to replace it with a new Channelgroup for your course?",
                )
            )
            if result2["result"] != "success":
                raise DMError("Could not send message to user")

            resp2, _ = await UserInput.confirm(self.client, result2["id"], timeout=60)
            if not resp2:
                res3 = await self.client.send_response(
                    Response.build_message(
                        message,
                        content=f"Do you want to use the existing Channelgroup with :{channelgroup_emoji}: for your course?",
                    )
                )
                if res3["result"] != "success":
                    raise DMError("Could not send message to user")

                resp3, _ = await UserInput.confirm(self.client, res3["id"], timeout=60)
                if not resp3:
                    yield DMResponse(
                        f"Ok, I will not create a new course then. Please choose another emote because :{channelgroup_emoji}: is already in use :botsad:"
                    )
                    return
                else:
                    channels = (
                        session.query(ChannelGroup)
                        .filter(ChannelGroup.ChannelGroupEmote == channelgroup_emoji)
                        .first()
                    )
            else:
                sg: ChannelGroup | None = (
                    session.query(ChannelGroup)
                    .filter(ChannelGroup.ChannelGroupEmote == channelgroup_emoji)
                    .first()
                )
                if sg is not None:
                    Channelgroup._delete_group(session, sg)

        resLan = await self.client.send_response(
            Response.build_message(
                message, content=f"Is your course held in English or German?"
            )
        )
        lan, _ = await UserInput.i8n_german_or_english(
            self.client, resLan["id"], timeout=60
        )

        try:
            # get corresponding Channelgroup
            if not channels:
                if opts.s:
                    channels = opts.s
                else:
                    channelgroup_name: str = "channels_" + name
                    channels = Channelgroup._create_and_get_group(
                        session, channelgroup_name, channelgroup_emoji
                    )
                    cleanup_opterations.append(
                        lambda: Channelgroup._delete_group(session, channels)
                    )

            # get corresponding Usergroup
            tutors: UserGroup
            if opts.t:
                tutors = opts.t
            else:
                usergroup_name: str = "tutors_" + name

                if (
                    session.query(UserGroup)
                    .filter(UserGroup.GroupName == usergroup_name)
                    .first()
                    is not None
                ):
                    result4 = await self.client.send_response(
                        Response.build_message(
                            message,
                            content=f"Usergroup for Tutors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?",
                        )
                    )
                    if result4["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp4, _ = await UserInput.confirm(
                        self.client, result4["id"], timeout=60
                    )
                    if not resp4:
                        raise DMError(
                            "Ok, I will not create a new course then. Please choose another name."
                        )
                    else:
                        ugt: UserGroup | None = (
                            session.query(UserGroup)
                            .filter(UserGroup.GroupName == usergroup_name)
                            .first()
                        )
                        if ugt is not None:
                            Usergroup.delete_group(session, ugt)

                tutors = Usergroup.create_and_get_group(session, usergroup_name)

                cleanup_opterations.append(
                    lambda: Usergroup.delete_group(session, tutors)
                )

            # get corresponding Usergroup
            instructors: UserGroup
            if opts.i:
                instructors = opts.i
            else:
                usergroup_name = "instructors_" + name

                if (
                    session.query(UserGroup)
                    .filter(UserGroup.GroupName == usergroup_name)
                    .first()
                    is not None
                ):
                    result5 = await self.client.send_response(
                        Response.build_message(
                            message,
                            content=f"Usergroup for Instructors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?",
                        )
                    )
                    if result5["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp5, _ = await UserInput.confirm(
                        self.client, result5["id"], timeout=60
                    )
                    if not resp5:
                        raise DMError(
                            "Ok, I will not create a new course then. Please choose another name."
                        )
                    else:
                        ugi = (
                            session.query(UserGroup)
                            .filter(UserGroup.GroupName == usergroup_name)
                            .first()
                        )
                        if ugi is not None:
                            Usergroup.delete_group(session, ugi)

                instructors = Usergroup.create_and_get_group(session, usergroup_name)

                cleanup_opterations.append(
                    lambda: Usergroup.delete_group(session, instructors)
                )

            # get corresponding Channel for Tutors
            tutors_channel: ZulipChannel
            if opts.tuts:
                tutors_channel = opts.tuts
            else:
                tutors_channel_name: str = name + " - Tutors"
                tutors_channel_desc: list[str] = [f"Internal Channel for {name}-Tutors"]
                if lan == "de":
                    tutors_channel_name = name + " - Tutoren"
                    tutors_channel_desc = [
                        f"Interner Kanal für {name}-Tutoren"
                    ]

                tut_ex = await self.client.get_channel_id_by_name(tutors_channel_name)

                if tut_ex is not None:
                    result_tut = await self.client.send_response(
                        Response.build_message(
                            message,
                            content=f"Channel for Tutors of this course already exists. Dou you want to replace it with a new empty Channel for your course?",
                        )
                    )
                    if result_tut["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp_tut_s, _ = await UserInput.confirm(
                        self.client, result_tut["id"], timeout=60
                    )
                    if not resp_tut_s:
                        raise DMError(
                            "Ok, I will not create a new course then. Please choose another name."
                        )
                    else:
                        await self.client.delete_channel(tut_ex)

                tutor_ids = Usergroup.get_user_ids_for_group(session, tutors)
                tutor_ids.append(sender.id)
                tutor_ids.append(self.client.id)

                result_tut_s = await self.client.add_subscriptions(
                    channels=[
                        {
                            "name": tutors_channel_name,
                            "description": " ".join(tutors_channel_desc),
                        }
                    ],
                    principals=tutor_ids,
                    invite_only=True,
                    history_public_to_subscribers=True,
                )
                if result_tut_s["result"] != "success":
                    raise DMError(result_tut_s["msg"])

                tutors_channel = ZulipChannel(f"#**{tutors_channel_name}**")
                await tutors_channel

                cleanup_opterations.append(
                    lambda: self.client.delete_channel(tutors_channel.id)
                )

            # get a corresponding Channel for Instructors or None
            instructor_channel: ZulipChannel | None = None
            if opts.ins:
                instructor_channel = opts.ins
            else:
                instructor_channel_name: str = name + " - Instructors"
                instructor_channel_desc: list[str] = [
                    f"Internal Channel for Instructors of {name}"
                ]
                if lan == "de":
                    instructor_channel_name = name + " - Instructors"
                    instructor_channel_desc = [
                        f"Interner Kanal für {name}-Instructors"
                    ]

                ins_ex = await self.client.get_channel_id_by_name(instructor_channel_name)

                if ins_ex is not None:
                    result_ins = await self.client.send_response(
                        Response.build_message(
                            message,
                            content=f"Channel for Instructors of this course already exists. Dou you want to replace it with a new empty Channel for your course?",
                        )
                    )
                    if result_ins["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp_ins, _ = await UserInput.confirm(
                        self.client, result_ins["id"], timeout=60
                    )
                    if not resp_ins:
                        raise DMError(
                            "Ok, I will not create a new course then. Please choose another name."
                        )
                    else:
                        await self.client.delete_channel(ins_ex)

                instructor_ids = Usergroup.get_user_ids_for_group(session, instructors)
                instructor_ids.append(sender.id)
                instructor_ids.append(self.client.id)

                result_ins_s: dict[str, Any] = await self.client.add_subscriptions(
                    channels=[
                        {
                            "name": instructor_channel_name,
                            "description": " ".join(instructor_channel_desc),
                        }
                    ],
                    principals=instructor_ids,
                    invite_only=True,
                    history_public_to_subscribers=True,
                )
                if result_ins_s["result"] != "success":
                    raise DMError(result_ins_s["msg"])

                instructor_channel = ZulipChannel(f"#**{instructor_channel_name}**")
                await instructor_channel

                cleanup_opterations.append(
                    lambda: self.client.delete_channel(instructor_channel.id)
                )
            
            # get a corresponding (empty) Channel for anonymous Feedback or None
            feedback_channel: ZulipChannel | None = None
            if opts.fb:

                feedback_channel_name: str = name + " - Feedback"
                feedback_channel_desc: list[str] = [
                    f"Anonymous Channel for Feedback to {name}"
                ]
                if lan == "de":
                    feedback_channel_desc = [
                        f"Anonymer Kanal für Feedback zu {name}"
                    ]

                f_ex = await self.client.get_channel_id_by_name(feedback_channel_name)

                if f_ex is not None:
                        await self.client.delete_channel(f_ex)

                result_fb_s: dict[str, Any] = await self.client.add_subscriptions(
                    channels=[
                        {
                            "name": feedback_channel_name,
                            "description": " ".join(feedback_channel_desc),
                        }
                    ],
                    principals=[sender.id, self.client.id],
                )

                if result_fb_s["result"] != "success":
                    raise DMError(result_fb_s["msg"])

                feedback_channel = ZulipChannel(
                    f"#**{feedback_channel_name}**"
                )
                await feedback_channel

                cleanup_opterations.append(
                    lambda: self.client.delete_channel(feedback_channel.id)
                )

            # create and add a Course to the DB
            course: CourseDB = CourseDB(
                CourseName=name,
                CourseLanguage=lan,
                Channels=channels.ChannelGroupId,
                TutorsUserGroup=tutors.GroupId,
                InstructorsUserGroup=instructors.GroupId,
                TutorChannel=tutors_channel,
                InstructorChannel=instructor_channel,
                FeedbackChannel=feedback_channel,
            )

            session.add(course)
            session.commit()

        except Exception as e:
            session.rollback()
            for cleanup in cleanup_opterations:
                if inspect.iscoroutinefunction(cleanup):
                    await cleanup()
                else:
                    cleanup()
            if isinstance(e, DMError):
                raise e

            raise DMError(
                f"Something went wrong when creating the course `{name}` :botsweat:"
            ) from e

        yield DMResponse(f"Course `{name}` created :bothappypad:")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        type=CourseDB.CourseName,
        description="The name of the Course to add the Channels to.",
    )
    @opt(
        "a",
        long_opt="all",
        description="Add all standard steams to the course (Allgemein, Organisation, normal Feedback, Ankündigungen, Technik, Memes).",
    )
    @opt("g", long_opt="general", description="Add a general channel.")
    @opt("o", long_opt="orga", description="Add a Channel for Organization.")
    @opt("fn", long_opt="feedbackbnorm", description="Add a normal  Channel for Feedback.")
    @opt("fa", long_opt="feedbackanon", description="Add an anonymous Channel for Feedback.")
    @opt("n", long_opt="announcements", description="Add a Channel for Announcements.")
    @opt("m", long_opt="memes", description="Add a Channel for Memes.")
    @opt("t", long_opt="tech", description="Add a Channel for Tech-Support.")
    async def add_default_channels(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add standard Channels to a given course. If Channels with the same names already exist, they will be transferred and not replaced with new ones.
        """
        course: CourseDB = args.course
        lan: Literal["en", "de"] = str(course.CourseLanguage)
        channels_id: str = str(course.Channels)
        stremgroup: ChannelGroup | None = (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupId == channels_id)
            .first()
        )

        if stremgroup is None:
            raise DMError(f"Could not find Channelgroup for Course `{course.CourseName}`.")

        if opts.a:
            await Course.add_standard_channels(
                client=self.client, 
                session=session, 
                name=str(course.CourseName), 
                sg=stremgroup, 
                lan=lan,
                principals=[sender.id, self.client.id]
            )

        else:
            if opts.fn and opts.fa:
                raise DMError("You can only add one (normal OR anonymous) feedback channel at a time.")
            
            await Course.add_standard_channels(
                client=self.client,
                session=session,
                name=str(course.CourseName),
                sg=stremgroup,
                lan=lan,
                principals=[sender.id, self.client.id],
                g=opts.g,
                o=opts.o,
                fn=opts.fn,
                fa=opts.fa,
                n=opts.n,
                m=opts.m,
                t=opts.t,
            )

        yield DMResponse(f"Standard Channels added to Course `{course.CourseName}`.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        type=CourseDB.CourseName,
        description="The name of the Course to delete.",
    )
    @opt(
        "a",
        long_opt="all",
        description="Delete the whole course (Channelgroup, Usergroups, Channels).",
    )
    @opt(
        "c",
        long_opt="channelgroup",
        description="Delete also Channelgroup",
    )
    @opt(
        "t",
        long_opt="tutors",
        description="Delete also Usergroup of Tutors.",
    )
    @opt(
        "i",
        long_opt="instructors",
        description="Delete also Usergroup of Instructors.",
    )
    @opt(
        "tuts",
        long_opt="tutorial_channel",
        description="Delete also Channel for Tutors.",
    )
    @opt(
        "ins",
        long_opt="instructor_channel",
        description="Delete also Channel for Instructors.",
    )
    async def delete(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:

        course: CourseDB = args.course
        c_name = str(course.CourseName)
        channels_id = str(course.Channels)
        tut_ug_id = int(course.TutorsUserGroup)
        ins_ug_id = int(course.InstructorsUserGroup)

        tut_s: ZulipChannel = await course.TutorChannel

        ins_s: ZulipChannel = await course.InstructorChannel

        try:
            session.query(CourseDB).filter(
                CourseDB.CourseId == course.CourseId
            ).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not delete Course `{c_name}`.") from e

        if opts.c or opts.a:
            sg: ChannelGroup | None = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupId == channels_id)
                .first()
            )
            
            if sg is not None:
                strm: list[str] = await Channelgroup._get_channel_names(
                    session, self.client, [sg]
                )
                await Channelgroup._remove_channels(session, self.client, sg, strm)

                Channelgroup._delete_group(session, sg)

                for s in strm:
                    sid = await self.client.get_channel_id_by_name(s)
                    if sid is not None:
                        await self.client.delete_channel(sid)

        if opts.t or opts.a:
            ugt: UserGroup | None = session.query(UserGroup).filter(UserGroup.GroupId == tut_ug_id).first()

            if ugt is not None:
                Usergroup.delete_group(session, ugt)

        if opts.i or opts.a:
            ugi: UserGroup | None = session.query(UserGroup).filter(UserGroup.GroupId == ins_ug_id).first()

            if ugi is not None:
                Usergroup.delete_group(session, ugi)

        if opts.tuts or opts.a:
            await self.client.delete_channel(tut_s.id)

        if (opts.ins or opts.a) and ins_s is not None:
            await self.client.delete_channel(ins_s.id)

        yield DMResponse(f"Course `{c_name}` deleted.")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        type=CourseDB.CourseName,
        description="The name of the Course to delete.",
    )
    @opt(
        "c",
        long_opt="channelgroup",
        type=ChannelGroup.ChannelGroupId,
        description="The id of an existing Channelgroup containing the Channels for this course.",
    )
    @opt(
        "t",
        long_opt="tutors",
        type=UserGroup.GroupName,
        description="The name of an existing Usergroup containing the tutors for this course.",
    )
    @opt(
        "tuts",
        long_opt="tutorial_channel",
        type=ZulipChannel,
        description="The name of an existing Channel for Instructors.",
    )
    @opt(
        "ins",
        long_opt="instructor_channel",
        type=ZulipChannel,
        description="The name of an existing Channel for Instructors.",
    )
    async def update(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Update a course with corresponding contents
        """
        course: CourseDB = args.course

        if opts.s:
            channels: ChannelGroup = opts.s
            Course._update_channelgroup(course, session, channels)

        if opts.t:
            tutors: UserGroup = opts.t
            Course._update_tutorgroup(course, session, tutors)

        if opts.tuts:
            tutchannel: ZulipChannel = opts.tuts
            await Course._update_tutorchannel(course, session, self.client, tutchannel)

        if opts.ins:
            inschannel: ZulipChannel = opts.ins
            await Course._update_instructorchannel(
                course, session, self.client, inschannel
            )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        type=CourseDB.CourseName,
        description="The name of the Course to delete.",
    )
    @opt("c", long_opt="channels", description="Remove the Channels from the Course.")
    @opt("t", long_opt="tutors", description="Remove the tutors from the Course")
    async def clear(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Clear a course (Channels/Tutors), but keep the underlying components (Channelgroup/UserGroup).
        """
        course: CourseDB = args.course

        if opts.s:
            sg: ChannelGroup | None = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupId == course.Channels)
                .first()
            )
            if sg is not None:
                channels: list[str] = await Channelgroup._get_channel_names(
                    session, self.client, [sg]
                )
                await Channelgroup._remove_channels(session, self.client, sg, channels)

        if opts.t:
            tutors: UserGroup | None = (
                session.query(UserGroup)
                .filter(UserGroup.GroupId == course.TutorsUserGroup)
                .first()
            )
            if tutors is not None:
                users: list[ZulipUser] = Usergroup.get_users_for_group(session, tutors)
                for user in users:
                    Usergroup.remove_user_from_group(session, user, tutors)

        yield DMResponse(f"Course `{course.CourseName}` cleared.")

    @command
    @privilege(Privilege.ADMIN)
    async def wizard(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Guides you through the process of creating a new course.
        """
        courseName: str | None = None
        courseEmoji: str | None = None
        courseLan: str | None = None
        courseChannels: ChannelGroup | None = None
        courseTutors: UserGroup | None = None
        courseInstructors: UserGroup | None = None
        courseTutorChannel: ZulipChannel | None = None
        courseInstructorChannel: ZulipChannel | None = None

        cleanup_opterations: list[Callable] = []

        async def or_exit(coro: Coroutine[None, None, T]) -> T:
            task = asyncio.create_task(coro)
            done, _ = await asyncio.wait(
                [task, exit_task], return_when=asyncio.FIRST_COMPLETED
            )
            if exit_task in done:
                raise DMError(
                    "You have exited the wizard. Have a nice day :bothappypad:"
                )
            return done.pop().result()

        def exit_and_inform_on_error(client_response: dict[str, Any]) -> dict[str, Any]:
            if client_response["result"] != "success":
                logging.error(f"Could not send message to user: {client_response}")
                raise DMError(
                    "Something went wrong. I am sorry :botsweat:. I already informed my creator about this issue."
                )
            return client_response

        async def dm(msg: str) -> dict[str, Any]:
            result = exit_and_inform_on_error(
                await self.client.send_response(
                    Response.build_message(message, content=msg)
                )
            )
            cleanup_opterations.append(
                lambda m=result: self.client.delete_message(m["id"])
            )
            return result

        async def short_text_input(msg: str, timeout: int = 60) -> str:
            server_response = await dm(msg)
            user_response, _ = await or_exit(
                UserInput.short_text_response(
                    self.client, server_response["id"], timeout=timeout
                )
            )
            return user_response

        async def confirm_input(msg: str, timeput: int = 60) -> bool:
            server_response = await dm(msg)
            user_response, _ = await or_exit(
                UserInput.confirm(self.client, server_response["id"], timeout=timeput)
            )
            return user_response

        responses = [
            await dm("Welcome to the Course Creation Wizard :bothappypad:"),
            await dm(
                "You can always exit the wizard by reacting with :cross_mark: to this message. You can answer my questions by replying to me with the response or reacting to my questions. I will do my best to guide you through the process of configuring your course."
            ),
            await dm(
                cleandoc(
                    """
                                                                 Let's start by choosing a name for your new course :bothappy:
                                                                 
                                                                 ```spoiler What is a short name of a course?
                                                                 The short name is a unique identifier for the course without spaces or special characters.
                                                                 This name will be used to associate Channels with the course. For example, if the course is called "Introduction to Computer Science", the short name could be `ICS` and the Announcements Channel would have the name `ICS - Announcements`.
                                                                 ```
                                                                 """
                )
            ),
        ]

        try:
            # wait for user client to process the messages so that the reactions can be added
            await asyncio.sleep(0.2)
            exit_and_inform_on_error(
                await self.client.add_reaction(
                    {"message_id": responses[1]["id"], "emoji_name": "cross_mark"}
                )
            )

            exit_task = asyncio.create_task(
                UserInput.specific_reaction(
                    responses[1]["id"], "cross_mark", timeout=120
                )
            )

            while True:

                result = await short_text_input("What is the short name of the course?")

                if result is None:
                    await dm("Please provide a valid short name for the course.")
                else:
                    c: CourseDB | None = (
                        session.query(CourseDB)
                        .filter(CourseDB.CourseName == result)
                        .one_or_none()
                    )
                    sgName: str = "channels_" + result
                    sg: ChannelGroup | None = (
                        session.query(ChannelGroup)
                        .filter(ChannelGroup.ChannelGroupId == sgName)
                        .one_or_none()
                    )
                    if c is None and sg is None:
                        courseName = result
                        break
                    else:
                        await dm(
                            f"A Course or Channelgroup with the name `{result}` already exists. Please choose another name."
                        )

            promptLan = await dm(
                f"Amazing :bothappypad:\nIs your course held in English or German?"
            )
            courseLan, _ = await or_exit(
                UserInput.i8n_german_or_english(
                    self.client, promptLan["id"], timeout=60
                )
            )

            # TODO: Create channelgroup
            user_response = await confirm_input(
                cleandoc(
                    """
                                                         Now we will add a Channelgroup for your course.
                                                         We can do this by using an already existing Channelgroup, creating a new Channelgroup with already existing Channels or creating a completely new empty Channelgroup.
                                                         In every case, you can update the Channelgroup later.

                                                         Do you want to use an existing Channelgroup for your course?
                                                         """
                )
            )

            if user_response:
                # use existing channelgroup
                while True:
                    user_response = await short_text_input(
                        "Please provide the name of the Channelgroup."
                    )
                    if user_response is None:
                        continue

                    sg = (
                        session.query(ChannelGroup)
                        .filter(ChannelGroup.ChannelGroupId == user_response)
                        .one_or_none()
                    )
                    if sg is None:
                        sgs = session.query(ChannelGroup.ChannelGroupId).all()
                        closest = difflib.get_close_matches(user_response, sgs, n=1)

                        if closest:
                            await dm(
                                f"A Channelgroup with the name `{user_response}` does not exist. Did you mean `{closest[0]}`?"
                            )
                        else:
                            await dm(
                                f"A Channelgroup with the name `{user_response}` does not exist."
                            )

                        continue

                    courseChannels = sg
                    break
            else:
                await dm(
                    "Great, so let's create a new Channelgroup by choosing an emoji for the course :bothappy:"
                )

                while True:

                    user_response = await short_text_input(
                        "What is the emoji representing the course?"
                    )

                    if user_response is None:
                        continue
                    else:
                        emote = Regex.get_emoji_name(user_response)
                        if emote is None:
                            await dm("Please provide a valid emoji.")
                            continue

                        sg = (
                            session.query(ChannelGroup)
                            .filter(ChannelGroup.ChannelGroupEmote == emote)
                            .one_or_none()
                        )
                        if sg is None:
                            courseEmoji = emote
                            break
                        else:
                            await dm(
                                f"A course with the emote :{emote}: already exists. Please choose another emoji."
                            )

                user_response = await confirm_input(
                    f"Do you want to create the Channelgroup {courseEmoji} from a list of Channel-Name-Regexes?"
                )
                if user_response:
                    # enter list of names
                    while True:

                        user_response = await short_text_input(
                            "Please enter a list of Channels in the format 'ChannelNameRegex1,ChannelNameRegex2, ..., ChannelNameRegexN'"
                        )
                        if user_response is None:
                            continue
                        else:
                            # parse Channel Names
                            channelgroup_name: str = "channels_" + courseName
                            courseChannels: ChannelGroup = (
                                Channelgroup._create_and_get_group(
                                    session, channelgroup_name, courseEmoji
                                )
                            )
                            cleanup_opterations.append(
                                lambda s=courseChannels: Channelgroup._delete_group(
                                    session, s
                                )
                            )

                            channels = user_response.split(",")
                            await Channelgroup.add_channels(
                                self.client, session, sender, courseChannels, channels
                            )
                            break

                else:
                    # create empty Channelgroup
                    channelgroup_name = "channels_" + courseName
                    courseChannels = Channelgroup._create_and_get_group(
                        session, channelgroup_name, courseEmoji
                    )
                    cleanup_opterations.append(
                        lambda s=courseChannels: Channelgroup._delete_group(session, s)
                    )

            # add default Channels
            resultG = await confirm_input(
                "Now you can add the default Channels to your course.\nDo you want to add a general Channel to your course?"
            )
            resultO = await confirm_input(
                "Do you want to add a Organization-Channel to your course?"
            )
            resultM = await confirm_input(
                "Do you want to add a Memes-Channel to your course?"
            )
            resultT = await confirm_input(
                "Do you want to add a Channel for Tech-Support to your course?"
            )
            resultF = await confirm_input(
                "Do you want the Feedback-Channel of your course to allow anonymous Feedback?"
            )

            # wizard adds Feedback and Announcement per default to improve the communication between instructors and students (they have to be removed manually)
            await Course.add_standard_channels(
                client=self.client,
                session=session,
                name=courseName,
                sg=courseChannels,
                lan=courseLan,
                principals=[self.client.id, sender.id],
                g=resultG,
                o=resultO,
                fn=not resultF,
                fa=resultF,
                n=True,
                m=resultM,
                t=resultT,
            )

            # TODO create Usergroup for Tutors
            result1t = await confirm_input(
                cleandoc(
                    """
                              Now we will add a Usergroup for the Tutors of your course.
                              We can do this by using an already existing Usergroup, creating a new Usergroup with certain people or creating a completely new empty Usergroup.
                              In every case, you can update the Usergroup later.

                              Do you want to use an existing Usergroup for your course?
                              """
                )
            )

            if result1t:
                # use existing usergroup
                while True:
                    result1at = await short_text_input(
                        "Please provide the name of the Usergroup."
                    )
                    if result1at is None:
                        continue

                    ug: UserGroup | None = (
                        session.query(UserGroup)
                        .filter(UserGroup.GroupName == result1at)
                        .one_or_none()
                    )
                    if ug is None:
                        await dm(
                            f"A Usergroup with the name `{result1at}` does not exist."
                        )
                        continue

                    courseTutors = ug
                    break
            else:
                await dm("Great, so let's create a new Usergroup for your Tutors")

                while True:
                    usergroup_name_tut: str = "tutors_" + courseName + "_1"
                    vers = 1

                    ug = (
                        session.query(UserGroup)
                        .filter(UserGroup.GroupName == usergroup_name_tut)
                        .one_or_none()
                    )
                    if ug is None:
                        courseTutors = Usergroup.create_and_get_group(
                            session, usergroup_name_tut
                        )
                        cleanup_opterations.append(
                            lambda: Usergroup.delete_group(session, courseTutors)
                        )
                        break
                    else:
                        vers += 1
                        usergroup_name_tut = usergroup_name_tut[:-1] + vers

                result2bt = await confirm_input(
                    f"Do you want to create the Usergroup from a list of Tutor-Names?"
                )
                if result2bt:
                    # enter list of names
                    while True:
                        result2ct = await short_text_input(
                            f"Please enter a list of Names in the format 'Name1,Name2, ..., NameN'"
                        )
                        if result2ct is None:
                            continue
                        else:
                            # parse Channel Names
                            names = result2ct.split(",")

                            for name in names:
                                real_name = Regex.get_user_name(name)
                                if real_name is None:
                                    await dm(
                                        f"Could not find a user with the name {name}."
                                    )
                                    continue
                                try:
                                    user = ZulipUser(real_name)
                                    await user
                                    Usergroup.add_user_to_group(
                                        session, user, courseTutors
                                    )
                                except Exception:
                                    await dm(
                                        f"Could not add a user with the name {name}."
                                    )
                                    continue
                            break

            # TODO create Usergroup for Instructors
            await dm(
                "Now we will add a Usergroup for the Instructors of your course. \nSame procedure as for the Tutors."
            )

            result1i = await confirm_input(
                "Do you want to use an existing Usergroup for your course?"
            )
            if result1i:
                # use existing usergroup
                while True:
                    result1ai = await short_text_input(
                        "Please provide the name of the Usergroup."
                    )
                    if result1ai is None:
                        continue

                    ug = (
                        session.query(UserGroup)
                        .filter(UserGroup.GroupName == result1ai)
                        .one_or_none()
                    )
                    if ug is None:
                        await dm(
                            f"A Usergroup with the name `{result1i}` does not exist."
                        )
                        continue

                    courseInstructors = ug
                    break
            else:

                await dm("Great, so let's create a new Usergroup for your Instructors.")

                while True:
                    usergroup_name_ins: str = "instructors_" + courseName + "_1"
                    vers = 1

                    ug = (
                        session.query(UserGroup)
                        .filter(UserGroup.GroupName == usergroup_name_ins)
                        .one_or_none()
                    )
                    if ug is None:
                        courseInstructors = Usergroup.create_and_get_group(
                            session, usergroup_name_ins
                        )
                        cleanup_opterations.append(
                            lambda: Usergroup.delete_group(session, courseInstructors)
                        )
                        break
                    else:
                        vers += 1
                        usergroup_name_ins = usergroup_name_ins[:-1] + vers

                result2bi = await confirm_input(
                    f"Do you want to create the Usergroup from a list of Instructor-Names?"
                )
                if result2bi:
                    # enter list of names
                    while True:
                        result2ci = await short_text_input(
                            f"Please enter a list of Names in the format 'Name1,Name2, ..., NameN'"
                        )
                        if result2ci is None:
                            continue
                        else:
                            # parse Channel Names
                            names = result2ci.split(",")

                            for name in names:
                                real_name = Regex.get_user_name(name)
                                if real_name is None:
                                    await dm(
                                        f"Could not find a user with the name {name}."
                                    )
                                    continue
                                try:
                                    user = ZulipUser(real_name)
                                    await user
                                    Usergroup.add_user_to_group(
                                        session, user, courseTutors
                                    )
                                except Exception:
                                    await dm(
                                        f"Could not add a user with the name {name}."
                                    )
                                    continue
                            break

            Usergroup.add_user_to_group(session, sender, courseInstructors)

            # get a corresponding Channel for Tutors
            tutors_channel_name = courseName + " - Tutors"
            tutors_channel_desc = [f"Internal Channel for {courseName}-Tutors"]
            if courseLan == "de":
                tutors_channel_name = courseName + " - Tutoren"
                tutors_channel_desc = [f"Interner Kanal für {courseName}-Tutoren"]

            tut_ex = await self.client.get_channel_id_by_name(tutors_channel_name)

            if tut_ex is not None:
                resultts = await confirm_input(
                    f"Channel for Tutors of this course already exists. Dou you want to replace it with a new Channel for your course?"
                )
                if not result:
                    while True:
                        res = await short_text_input(
                            "Please choose another name for the Tutor-Channel."
                        )
                        if res is None:
                            await dm("Please provide a name for the Channel.")
                        else:
                            tut_ex = await self.client.get_channel_id_by_name(res)
                            if tut_ex is None:
                                tutors_channel_name = res
                                break
                            else:
                                await dm(
                                    f"A Channel with the name {res} already exists."
                                )

                else:
                    await self.client.delete_channel(tut_ex)

            tutor_ids = Usergroup.get_user_ids_for_group(session, courseTutors)
            tutor_ids.append(sender.id)
            tutor_ids.append(self.client.id)

            exit_and_inform_on_error(
                await self.client.add_subscriptions(
                    channels=[
                        {
                            "name": tutors_channel_name,
                            "description": " ".join(tutors_channel_desc),
                        }
                    ],
                    principals=tutor_ids,
                    invite_only=True,
                    history_public_to_subscribers=True,
                )
            )

            courseTutorChannel = ZulipChannel(f"#**{tutors_channel_name}**")
            await courseTutorChannel

            cleanup_opterations.append(
                lambda id=courseTutorChannel.id: self.client.delete_channel(id)
            )

            # get a corresponding Channel for Instructors or None
            resultis = await confirm_input(
                "Do you want a Instructor-Channel for your course?"
            )
            if resultis is not None:

                instructor_channel_name = courseName + " - Instructors"
                instructor_channel_desc = [
                    f"Internal Channel for Instructors of {courseName}"
                ]
                if courseLan == "de":
                    instructor_channel_name = courseName + " - Instructors"
                    instructor_channel_desc = [
                        f"Interner Kanal für {courseName}-Instructors"
                    ]

                ins_ex = await self.client.get_channel_id_by_name(instructor_channel_name)

                if ins_ex is not None:
                    result = await confirm_input(
                        f"Channel for Instructor of this course already exists. Dou you want to replace it with a new Channel for your course?"
                    )
                    if not result:
                        while True:
                            res = await short_text_input(
                                "Please choose another name for the Instructor-Channel."
                            )
                            if res is None:
                                await dm("Please provide a name for the Channel.")

                            else:
                                ins_ex = await self.client.get_channel_id_by_name(res)
                                if ins_ex is None:
                                    instructor_channel_name = res
                                    break
                                else:
                                    await dm(
                                        f"A Channel with the name {res} already exists."
                                    )

                    else:
                        await self.client.delete_channel(tut_ex)

                instructor_ids = Usergroup.get_user_ids_for_group(
                    session, courseInstructors
                )
                instructor_ids.append(sender.id)
                instructor_ids.append(self.client.id)

                exit_and_inform_on_error(
                    await self.client.add_subscriptions(
                        channels=[
                            {
                                "name": instructor_channel_name,
                                "description": " ".join(instructor_channel_desc),
                            }
                        ],
                        principals=instructor_ids,
                        invite_only=True,
                        history_public_to_subscribers=True,
                    )
                )

                courseInstructorChannel = ZulipChannel(f"#**{instructor_channel_name}**")
                await courseInstructorChannel

                cleanup_opterations.append(
                    lambda ID=courseInstructorChannel.id: self.client.delete_channel(ID)
                )

                courseFeedbackChannel: ZulipChannel | None = None
                if resultF:
                    courseFeedbackChannel = ZulipChannel(
                        f"#**{courseName} - Feedback**"
                    )
                    await courseFeedbackChannel


                # create and add a Course to the DB
                course: CourseDB = CourseDB(
                    CourseName=courseName,
                    CourseLanguage=courseLan,
                    Channels=courseChannels.ChannelGroupId,
                    TutorsUserGroup=courseTutors.GroupId,
                    InstructorsUserGroup=courseInstructors.GroupId,
                    TutorChannel=courseTutorChannel,
                    InstructorChannel=courseInstructorChannel,
                    FeedbackChannel=courseFeedbackChannel
                )

                session.add(course)
                session.commit()

        except Exception as e:
            logging.exception(e)
            session.rollback()
            for cleanup in cleanup_opterations:
                result = cleanup()
                if result and inspect.isawaitable(result):
                    await result
                    # avoid rate limiting
                    await asyncio.sleep(0.2)

            if isinstance(e, DMError):
                raise e

            if courseName is None:
                raise DMError(
                    f"Something went wrong when creating your course :botsweat:"
                ) from e
            else:
                raise DMError(
                    f"Something went wrong when creating the course `{courseName}` :botsweat:"
                ) from e

        yield DMResponse(f"Course `{courseName}` created :bothappypad:")

    # ========================================================================================================================
    #       CLASS METHODS
    # ========================================================================================================================
    @staticmethod
    async def add_standard_channels(
        client: AsyncClient,
        session: Session,
        name: str,
        sg: ChannelGroup,
        lan: Literal["en", "de"],
        principals: list[int] | None,
        g: bool = True,
        o: bool = True,
        fn: bool = True,
        fa: bool = True,
        n: bool = True,
        m: bool = True,
        t: bool = True,
    ) -> None:

        if principals is None:
            principals = [client.id]
        else:
            principals.append(client.id)

        channels = []
        for opt, suffix_en, suffix_de, desc_en, desc_de in [
            (
                g,
                "General",
                "Allgemein",
                f"Welcome to the general Channel of {name}",
                f"Willkommen im allgemeinen Zulip Kanal von dem Kurs {name}",
            ),
            (
                o,
                "Organization",
                "Organisation",
                f"Welcome to the organizational Channel of {name}",
                f"Willkommen im Orga-Zulip Kanal von dem Kurs {name}",
            ),
            (
                fn,
                "Feedback",
                "Feedback",
                f"Welcome to the Channel for Feedback to {name}",
                f"Willkommen im Feedback Zulip Kanal von dem Kurs {name}",
            ),
            (
                fa,
                "Feedback",
                "Feedback",
                f"Welcome to the anonymous Channel for Feedback to {name}",
                f"Willkommen im anonymen Feedback Zulip Kanal von dem Kurs {name}",
            ),
            (
                n,
                "Announcements",
                "Ankündigungen",
                f"Welcome to the Channel for Announcements for {name}",
                f"Willkommen im Zulip Kanal für Ankündigungen von {name}",
            ),
            (
                t,
                "TechSupport",
                "Technik",
                f"Welcome to the Channel for Tech-Support in {name}",
                f"Willkommen im Technik Zulip Kanal von {name}",
            ),
            (
                m,
                "Memes",
                "Memes",
                f"Welcome to the Channel for top-quality Memes of {name}",
                f"Willkommen im Memes Zulip Kanal von {name}",
            ),
        ]:
            if not opt:
                continue

            suffix = suffix_en if lan == "en" else suffix_de
            desc = desc_en if lan == "en" else desc_de
            full_name = name + " - " + suffix

            channels.append({"name": full_name, "description": desc})

        try:
            result: dict[str, Any] = await client.add_subscriptions(
                channels=channels, principals=principals
            )

            if result["result"] != "success":
                raise DMError("Could not add standard channels to the course.")

            to_add = [ZulipChannel(f"#**{s['name']}**") for s in channels]

            for s in to_add:
                await s

            Channelgroup._add_zulip_channels(session, to_add, sg)

            if fa:
                fb = next(s for s in to_add if f"{name} - Feedback" in s.name)
                session.query(CourseDB).filter(CourseDB.CourseName == name).update({'FeedbackChannel': fb})
                session.commit()


        except Exception as e:
            session.rollback()

            for s in channels:
                sid = await client.get_channel_id_by_name(s["name"])
                if sid is not None:
                    await client.delete_channel(sid)

            raise DMError(
                f"Something went wrong when creating the default channels :botsad:"
            ) from e

    # ========================================================================================================================
    #       HELPER METHODS
    # ========================================================================================================================

    @staticmethod
    def get_course_by_id(id: int, session: Session) -> CourseDB:
        result: CourseDB | None = None
        result = session.query(CourseDB).filter(CourseDB.CourseId == id).one_or_none()

        if result:
            return result

        raise DMError(
            f"Uuups, it looks like i could not find any Course associated with `{id}` :botsceptical:"
        )

    @staticmethod
    def get_course_by_name(name: str, session: Session) -> CourseDB:
        result: CourseDB | None = None
        result = (
            session.query(CourseDB).filter(CourseDB.CourseName == name).one_or_none()
        )

        if result:
            return result

        raise DMError(
            f"Uuups, it looks like i could not find any Course associated with `{name}` :botsceptical:"
        )

    @staticmethod
    def get_channelgroup(course: CourseDB, session: Session) -> ChannelGroup:
        """
        Get the ChannelGroup of a given Course.
        """
        ID = int(course.Channels)
        return session.query(ChannelGroup).filter(ChannelGroup.ChannelGroupId == ID).one()

    @staticmethod
    def get_emoji(course: CourseDB, session: Session) -> str:
        """
        Get the Emoji of the ChannelGroup associated with a given Course.
        """
        sg: ChannelGroup = Course.get_channelgroup(course, session)
        return str(sg.ChannelGroupEmote)

    @staticmethod
    def get_tutorgroup(course: CourseDB, session: Session) -> UserGroup:
        """
        Get the Tutor-UserGroup of a given Course.
        """
        ID = int(course.TutorsUserGroup)
        return session.query(UserGroup).filter(UserGroup.GroupId == ID).one()

    @staticmethod
    def get_tutors(course: CourseDB, session: Session) -> list[ZulipUser]:
        """
        Get the Tutors of a Course a list of ZulipUsers.
        """
        ug: UserGroup = Course.get_tutorgroup(course, session)
        return Usergroup.get_users_for_group(session, ug)

    @staticmethod
    def get_instructorgroup(course: CourseDB, session: Session) -> UserGroup:
        """
        Get the Tutor-UserGroup of a given Course.
        """
        ID = int(course.InstructorsUserGroup)
        return session.query(UserGroup).filter(UserGroup.GroupId == ID).one()

    @staticmethod
    def get_instructors(course: CourseDB, session: Session) -> list[ZulipUser]:
        """
        Get the Tutors of a Course a list of ZulipUsers.
        """
        ug: UserGroup = Course.get_instructorgroup(course, session)
        return Usergroup.get_users_for_group(session, ug)

    @staticmethod
    def get_channels(course: CourseDB, session: Session) -> list[ZulipChannel]:
        """
        Get the Channels of a Course as list of ZulipChannels.
        """
        sg: ChannelGroup = Course.get_channelgroup(course, session)
        return Channelgroup._get_channels(session, sg)

    @staticmethod
    async def get_channel_names(
        session: Session, client: AsyncClient, course: CourseDB
    ) -> list[str]:
        """
        Get the Channel Names of a Course as list of strings.
        """
        sg: ChannelGroup = Course.get_channelgroup(course, session)
        return await Channelgroup._get_channel_names(session, client, [sg])

    @staticmethod
    def _update_channelgroup(
        course: CourseDB, session: Session, group: ChannelGroup
    ) -> None:
        """
        Set the ChannelGroup of a given Course.
        """
        oldSG: ChannelGroup = Course.get_channelgroup(course, session)
        if oldSG == group:
            raise DMError("The given Channelgroup is already set for this course.")

        stmt = (
            update(CourseDB)
            .where(CourseDB.CourseId == course.CourseId)
            .values(Channels=group)
        )
        try:
            session.execute(stmt)
            session.query(ChannelGroup).filter(
                ChannelGroup.ChannelGroupId == oldSG.ChannelGroupId
            ).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Channelgroup :botsad:")

    @staticmethod
    def _update_tutorgroup(
        course: CourseDB, session: Session, group: UserGroup
    ) -> None:
        """
        Set the Tutor-UserGroup of a given Course.
        """
        oldTG: UserGroup = Course.get_tutorgroup(course, session)
        if oldTG == group:
            raise DMError(
                "The given Usergroup is already set as Tutorgroup for this course."
            )

        stmt = (
            update(CourseDB)
            .where(CourseDB.CourseId == course.CourseId)
            .values(TutorsUserGroup=group.GroupId)
        )
        try:
            session.execute(stmt)
            session.query(UserGroup).filter(UserGroup.GroupId == oldTG.GroupId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Tutors :botsad:")

    @staticmethod
    def _update_instructorgroup(
        course: CourseDB, session: Session, group: UserGroup
    ) -> None:
        """
        Set the Tutor-UserGroup of a given Course.
        """
        oldIG: UserGroup = Course.get_instructorgroup(course, session)
        if oldIG == group:
            raise DMError(
                "The given Usergroup is already set as Instructorgroup for this course."
            )

        stmt = (
            update(CourseDB)
            .where(CourseDB.CourseId == course.CourseId)
            .values(InstructorsUserGroup=group)
        )
        try:
            session.execute(stmt)
            session.query(UserGroup).filter(UserGroup.GroupId == oldIG.GroupId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Instructors :botsad:")

    @staticmethod
    async def _update_tutorchannel(
        course: CourseDB, session: Session, client: AsyncClient, channel: ZulipChannel
    ) -> None:
        """
        Set the Tutor-Channel of a given Course.
        """
        oldTS = course.TutorChannel
        if oldTS == channel:
            raise DMError(
                "The given Channel is already set as Tutor-Channel for this course."
            )

        stmt = (
            update(CourseDB)
            .where(CourseDB.CourseId == course.CourseId)
            .values(TutorChannel=channel)
        )
        try:
            session.execute(stmt)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Tutor-Channel :botsad:")

        await client.delete_channel(oldTS.id)

    @staticmethod
    async def _update_instructorchannel(
        course: CourseDB, session: Session, client: AsyncClient, channel: ZulipChannel
    ) -> None:
        """
        Set the Instructor-Channel of a given Course.
        """
        oldIS: ZulipChannel = course.InstructorChannel
        if oldIS == channel:
            raise DMError(
                "The given Channel is already set as Instructor-Channel for this course."
            )

        stmt = (
            update(CourseDB)
            .where(CourseDB.CourseId == course.CourseId)
            .values(InstructorChannel=channel)
        )
        try:
            session.execute(stmt)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Instructor-Channel :botsad:")

        await client.delete_channel(oldIS.id)
