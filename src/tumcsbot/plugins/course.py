#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

import asyncio
import difflib
from inspect import cleandoc
import inspect
import logging
from typing import Coroutine, Literal, cast, Any, Callable, AsyncGenerator, TypeVar

import sqlalchemy
from sqlalchemy import (
    Column,
    String,
    Integer,
    ForeignKey,
    update,
)
from sqlalchemy.orm import relationship
from tumcsbot.lib.regex import Regex

from tumcsbot.lib.response import Response
from tumcsbot.lib.client import AsyncClient
from tumcsbot.plugin import Plugin, PluginCommand
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import TableBase, Session
from tumcsbot.plugin_decorators import command, privilege, arg, opt
from tumcsbot.plugins.usergroup import UserGroup, Usergroup
from tumcsbot.plugins.userinput import UserInput
from tumcsbot.plugins.channelgroup import ChannelGroup, Channelgroup, ChannelGroupMember
from tumcsbot.lib.types import (
    DMError,
    DMResponse,
    Privilege,
    response_type,
    ZulipUser,
    ZulipChannel,
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
        ForeignKey("ChannelGroups.ChannelGroupId"),
        nullable=False,
    )

    # todo: fix schema ondelete
    TutorsUserGroup = Column(Integer, ForeignKey("UserGroups.GroupId"), nullable=False)

    InstructorsUserGroup = Column(
        Integer, ForeignKey("UserGroups.GroupId"), nullable=False
    )

    TutorChannel = Column(ZulipChannel, nullable=False)  # type: ignore
    InstructorChannel = Column(ZulipChannel, nullable=True)  # type: ignore
    FeedbackChannel = Column(ZulipChannel, nullable=True)  # type: ignore # not Null if anonymous feedback enabled

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


class Course(PluginCommand, Plugin):
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
        _sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        List all Courses with their associated Channels.
        """
        response: str = "Course Name | Emoji | Channels \n---- | ---- | ----"

        courses: list[CourseDB] = session.query(CourseDB).all()

        if len(courses) == 0:
            raise DMError("No courses found")

        for course in courses:
            course_name = course.CourseName
            emoji: str = Course.get_emoji(course, session)
            num_channels = len(
                list(
                    session.query(ChannelGroupMember)
                    .filter(ChannelGroupMember.ChannelGroupId == course.Channels)
                    .all()
                )
            )

            response += f"\n{course_name} | {emoji} :{emoji}: | {num_channels} Channels"

        yield DMResponse(response)

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course.",
    )
    async def overview(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        For a given course show all its details: \n
        - Name and Channels of Channelgroup
        - Name and Users of Tutor-Usergroup
        - Name and Users of Instructor-Usergroup
        - Name of Tutor-Channel
        - Name of Instructor-Channel
        - Anonymous Feedback enabled
        """
        msg = await Course._build_info_message(args.course, session)
        yield DMResponse(msg)

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the Course.")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the Course.")
    @opt(
        "i",
        long_opt="instructors",
        description="The Course has an additional Channel for Instructors.",
    )
    @opt(
        "f",
        long_opt="feedback",
        description="The Course has an ANONYMOUS Feedback-Channel.",
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
        Create a new empty Course
        """
        name: str = args.name
        channelgroup_emoji: str = args.emoji
        channels: ChannelGroup | None = None

        cleanup_opterations: list[
            Callable[[], None]
            | Callable[[], Coroutine[Any, Any, dict[str, Any]]]
            | Callable[[], Coroutine[Any, Any, None]]
        ] = []

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result1 = await self.client.send_response(
                Response.build_message(
                    message,
                    content=f"Course `{name}` already exists. Dou you want to replace it with an empty Course?",
                )
            )
            if result1["result"] != "success":
                raise DMError("Could not send message to user")

            resp1, _ = await UserInput.confirm(self.client, result1["id"], timeout=60)
            if not resp1:
                yield DMResponse(
                    "Ok, I will not create a new Course. Please choose another name."
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
                    content=f"A Channelgroup with :{channelgroup_emoji}: already exists. Dou you want to replace it with an empty Channelgroup for your Course?",
                )
            )
            if result2["result"] != "success":
                raise DMError("Could not send message to user")

            resp2, _ = await UserInput.confirm(self.client, result2["id"], timeout=60)
            if not resp2:
                res = await self.client.send_response(
                    Response.build_message(
                        message,
                        content=f"Do you want to use the existing Channelgroup with :{channelgroup_emoji}: for your Course?",
                    )
                )
                if res["result"] != "success":
                    raise DMError("Could not send message to user")

                rep, _ = await UserInput.confirm(self.client, res["id"], timeout=60)
                if not rep:
                    yield DMResponse(
                        f"Ok, I will not create a new Course then. Please choose another emote because :{channelgroup_emoji}: is already in use :botsad:"
                    )
                    return
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
                    await Channelgroup.delete_group_h(session, sg, self.client)

        resLan = await self.client.send_response(
            Response.build_message(
                message, content="Is your Course held in English or German?"
            )
        )
        lan, _ = await UserInput.i8n_german_or_english(
            self.client, resLan["id"], timeout=60
        )

        try:
            # get a corresponding (empty) Channelgroup
            if not channels:

                c_g_same_name: ChannelGroup | None = (
                    session.query(ChannelGroup)
                    .filter(ChannelGroup.ChannelGroupId == name)
                    .first()
                )
                if c_g_same_name is not None:
                    await Channelgroup.delete_group_h(
                        session, c_g_same_name, self.client
                    )

                channels = await Channelgroup.create_and_get_group(
                    session, name, channelgroup_emoji, self.client
                )
                cleanup_opterations.append(
                    lambda: Channelgroup.delete_group_h(session, channels, self.client)
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
                        content="Usergroup for Tutors of this Course already exists. Dou you want to replace it with a new empty Usergroup for your Course?",
                    )
                )
                if result3["result"] != "success":
                    raise DMError("Could not send message to user")

                resp3, _ = await UserInput.confirm(
                    self.client, result3["id"], timeout=60
                )
                if not resp3:
                    raise DMError(
                        f"Ok, I will not create a new empty Course then. You can use the command `course create -t {usergroup_name_tut}` to use the existing Usergroup for Tutors."
                    )

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
                        content="Usergroup for Instructors of this Course already exists. Dou you want to replace it with a new empty Usergroup for your Course?",
                    )
                )
                if result4["result"] != "success":
                    raise DMError("Could not send message to user")

                resp4, _ = await UserInput.confirm(
                    self.client, result4["id"], timeout=60
                )
                if not resp4:
                    raise DMError(
                        f"Ok, I will not create a new empty Course then. You can use the command `course create -i {usergroup_name_ins}` to use the existing Usergroup for Instructors."
                    )

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
                        content="Channel for Tutors of this Course already exists. Dou you want to replace it with a new empty Channel for your Course?",
                    )
                )
                if result5["result"] != "success":
                    raise DMError("Could not send message to user")

                resp5, _ = await UserInput.confirm(
                    self.client, result5["id"], timeout=60
                )
                if not resp5:
                    raise DMError(
                        f'Ok, I will not create a new empty Course then. You can use the command `course create -tuts "{tutors_channel_name}"` to use the existing Channel for Tutors.'
                    )

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
                    instructor_channel_desc = [f"Interner Kanal für {name}-Instructors"]

                ins_ex = await self.client.get_channel_id_by_name(
                    instructor_channel_name
                )

                if ins_ex is not None:
                    result6 = await self.client.send_response(
                        Response.build_message(
                            message,
                            content="Channel for Instructors of this Course already exists. Dou you want to replace it with a new empty Channel for your Course?",
                        )
                    )
                    if result6["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp6, _ = await UserInput.confirm(
                        self.client, result6["id"], timeout=60
                    )
                    if not resp6:
                        raise DMError(
                            f'Ok, I will not create a new empty Course then. You can use the command `course create -ins "{instructor_channel_name}"` to use the existing Channel for Instructors.'
                        )

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

                instructor_channel = ZulipChannel(f"#**{instructor_channel_name}**")
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
                    feedback_channel_desc = [f"Anonymer Kanal für Feedback zu {name}"]

                f_ex = await self.client.get_channel_id_by_name(feedback_channel_name)
                if f_ex is None:
                    raise DMError(
                        "Uuups, I cannot get the Channel id for the Feedback-Channel"
                    )

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

                feedback_channel = ZulipChannel(f"#**{feedback_channel_name}**")
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
                f"Something went wrong when creating the Course `{name}` :botsweat:"
            ) from e

        yield DMResponse(f"Course `{name}` created :bothappy:")

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the Course")
    @opt(
        "c",
        long_opt="channelgroup",
        ty=ChannelGroup.ChannelGroupId,
        description="The id of a Channelgroup containing the Channels for this Course.",
    )
    @opt(
        "t",
        long_opt="tutors",
        ty=UserGroup.GroupName,
        description="The name of a Usergroup containing the Tutors for this Course.",
    )
    @opt(
        "i",
        long_opt="instructors",
        ty=UserGroup.GroupName,
        description="The name of a Usergroup containing the Instructors for this Course.",
    )
    @opt(
        "tuts",
        long_opt="tutor_channel",
        ty=ZulipChannel,
        description="The name of the additional Channel for Tutors.",
    )
    @opt(
        "ins",
        long_opt="instructor_channel",
        ty=ZulipChannel,
        description="The name of the additional Channel for Instructors.",
    )
    @opt(
        "fb",
        long_opt="feedback",
        description="The Course has an ANONYMOUS Feedback-Channel.",
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
        Create a new Course with corresponding contents
        """
        name: str = args.name

        cleanup_opterations: list[
            Callable[[], None]
            | Callable[[], Coroutine[Any, Any, dict[str, Any]]]
            | Callable[[], Coroutine[Any, Any, None]]
        ] = []

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result1 = await self.client.send_response(
                Response.build_message(
                    message,
                    content=f"Course `{name}` already exists. Dou you want to replace it with the new Course?",
                )
            )
            if result1["result"] != "success":
                raise DMError("Could not send message to user")

            resp1, _ = await UserInput.confirm(self.client, result1["id"], timeout=60)
            if not resp1:
                yield DMResponse(
                    "Ok, I will not create a new Course. Please choose another name."
                )
                return

        resLan = await self.client.send_response(
            Response.build_message(
                message, content="Is your course held in English or German?"
            )
        )
        lan, _ = await UserInput.i8n_german_or_english(
            self.client, resLan["id"], timeout=60
        )

        try:
            # get corresponding Channelgroup
            channels: ChannelGroup | None = None
            if opts.c:
                channels = opts.c
            else:
                channelgroup_emoji: str
                resultem1 = await self.client.send_response(
                    Response.build_message(
                        message,
                        content="Please provide the emoji for the Channelgroup.",
                    )
                )
                if resultem1["result"] != "success":
                    raise DMError("Could not send message to user")

                respem1, _ = await UserInput.short_text_response(
                    self.client, resultem1["id"], timeout=120
                )
                if not respem1:
                    raise DMError(
                        "You need to provide an emoji for the Channelgroup :botsad:"
                    )

                channelgroup_emoji = respem1

                existing_group: ChannelGroup | None = (
                    session.query(ChannelGroup)
                    .filter(ChannelGroup.ChannelGroupEmote == channelgroup_emoji)
                    .first()
                )

                if existing_group is not None:  # emoji already in use
                    result2 = await self.client.send_response(
                        Response.build_message(
                            message,
                            content=f"A Channelgroup with :{channelgroup_emoji}: already exists. Dou you want to replace it with a new Channelgroup for your Course?",
                        )
                    )
                    if result2["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp2, _ = await UserInput.confirm(
                        self.client, result2["id"], timeout=60
                    )
                    if not resp2:  # use existing channelgroup or stop
                        res3 = await self.client.send_response(
                            Response.build_message(
                                message,
                                content=f"Do you want to use the existing Channelgroup with :{channelgroup_emoji}: for your course?",
                            )
                        )
                        if res3["result"] != "success":
                            raise DMError("Could not send message to user")

                        resp3, _ = await UserInput.confirm(
                            self.client, res3["id"], timeout=60
                        )
                        if not resp3:
                            yield DMResponse(
                                f"Ok, I will not create a new Course then. Please choose another emote because :{channelgroup_emoji}: is already in use :botsad:"
                            )
                            return

                        channels = existing_group
                    else:  # replace existing channelgroup
                        await Channelgroup.delete_group_h(
                            session, existing_group, self.client
                        )

                if channels is None:

                    c_g_same_name: ChannelGroup | None = (
                        session.query(ChannelGroup)
                        .filter(ChannelGroup.ChannelGroupId == name)
                        .first()
                    )
                    if c_g_same_name is not None:
                        await Channelgroup.delete_group_h(
                            session, c_g_same_name, self.client
                        )

                    channels = await Channelgroup.create_and_get_group(
                        session, name, channelgroup_emoji, self.client
                    )

                    if channels is None:
                        raise DMError("Could not create channelgroup")
                    cleanup_opterations.append(
                        lambda: Channelgroup.delete_group_h(
                            session, channels, self.client
                        )
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
                            content="Usergroup for Tutors of this Course already exists. Dou you want to replace it with a new empty Usergroup for your Course?",
                        )
                    )
                    if result4["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp4, _ = await UserInput.confirm(
                        self.client, result4["id"], timeout=60
                    )
                    if not resp4:
                        raise DMError(
                            "Ok, I will not create a new Course then. Please choose another name."
                        )

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
                            content="Usergroup for Instructors of this Course already exists. Dou you want to replace it with a new empty Usergroup for your Course?",
                        )
                    )
                    if result5["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp5, _ = await UserInput.confirm(
                        self.client, result5["id"], timeout=60
                    )
                    if not resp5:
                        raise DMError(
                            "Ok, I will not create a new Course then. Please choose another name."
                        )

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
                    tutors_channel_desc = [f"Interner Kanal für {name}-Tutoren"]

                tut_ex = await self.client.get_channel_id_by_name(tutors_channel_name)

                if tut_ex is not None:
                    result_tut = await self.client.send_response(
                        Response.build_message(
                            message,
                            content="Channel for Tutors of this Course already exists. Dou you want to replace it with a new empty Channel for your Course?",
                        )
                    )
                    if result_tut["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp_tut_s, _ = await UserInput.confirm(
                        self.client, result_tut["id"], timeout=60
                    )
                    if not resp_tut_s:
                        raise DMError(
                            "Ok, I will not create a new Course then. Please choose another name."
                        )

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
                    instructor_channel_desc = [f"Interner Kanal für {name}-Instructors"]

                ins_ex = await self.client.get_channel_id_by_name(
                    instructor_channel_name
                )

                if ins_ex is not None:
                    result_ins = await self.client.send_response(
                        Response.build_message(
                            message,
                            content="Channel for Instructors of this Course already exists. Dou you want to replace it with a new empty Channel for your Course?",
                        )
                    )
                    if result_ins["result"] != "success":
                        raise DMError("Could not send message to user")

                    resp_ins, _ = await UserInput.confirm(
                        self.client, result_ins["id"], timeout=60
                    )
                    if not resp_ins:
                        raise DMError(
                            "Ok, I will not create a new Course then. Please choose another name."
                        )

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
                    feedback_channel_desc = [f"Anonymer Kanal für Feedback zu {name}"]

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

                feedback_channel = ZulipChannel(f"#**{feedback_channel_name}**")
                await feedback_channel

                cleanup_opterations.append(
                    lambda: self.client.delete_channel(feedback_channel.id)
                )

            channels = cast(ChannelGroup, channels)

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
                f"Something went wrong when creating the Course `{name}` :botsweat: : {str(e)}"
            ) from e

        yield DMResponse(f"Course `{name}` created :bothappy:")

    @command
    @privilege(Privilege.ADMIN)
    async def wizard(
        self,
        sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Guides you through the process of creating a new Course.
        """
        courseName: str | None = None
        courseEmoji: str | None = None
        courseLan: Literal["en", "de"] | None = None
        courseChannels: ChannelGroup | None = None
        courseTutors: UserGroup | None = None
        courseInstructors: UserGroup | None = None
        courseTutorChannel: ZulipChannel | None = None
        courseInstructorChannel: ZulipChannel | None = None

        cleanup_opterations: list[
            Callable[[], None]
            | Callable[[], Coroutine[Any, Any, dict[str, Any]]]
            | Callable[[], Coroutine[Any, Any, None]]
        ] = []

        async def or_exit(coro: Coroutine[None, None, T]) -> T:
            task = asyncio.create_task(coro)
            done, _ = await asyncio.wait(
                [task, exit_task], return_when=asyncio.FIRST_COMPLETED
            )
            if exit_task in done:
                raise DMError("You have exited the wizard. Have a nice day :bothappy:")
            return cast(T, done.pop().result())

        def exit_and_inform_on_error(client_response: dict[str, Any]) -> dict[str, Any]:
            if client_response["result"] != "success":
                logging.error("Could not send message to user: %s", client_response)
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

        async def short_text_input(msg: str, timeout: int = 60) -> str | None:
            server_response = await dm(msg)
            user_response, _ = await or_exit(
                UserInput.short_text_response(
                    self.client, server_response["id"], timeout=timeout
                )
            )
            return user_response

        async def confirm_input(msg: str, timeout: int = 60) -> bool:
            server_response = await dm(msg)
            user_response, _ = await or_exit(
                UserInput.confirm(self.client, server_response["id"], timeout=timeout)
            )
            return user_response

        responses = [
            await dm("Welcome to the Course Creation Wizard :bothappy:"),
            await dm(
                "You can always exit the wizard by reacting with :cross_mark: to this message. You can answer my questions by replying to me with the response or reacting to my questions. I will do my best to guide you through the process of configuring your Course."
            ),
            await dm(
                cleandoc(
                    """
                                                                 Let's start by choosing a name for your new Course :bothappy:
                                                                 
                                                                 ```spoiler What is a short name of a Course?
                                                                 The short name is a unique identifier for the Course without spaces or special characters.
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

                result = await short_text_input("What is the short name of the Course?")

                if result is None:
                    await dm("Please provide a valid short name for the Course.")
                else:
                    c: CourseDB | None = (
                        session.query(CourseDB)
                        .filter(CourseDB.CourseName == result)
                        .one_or_none()
                    )
                    if c is None:
                        courseName = result
                        cg = (
                            session.query(ChannelGroup)
                            .filter(
                                ChannelGroup.ChannelGroupId.like("%" + courseName + "%")
                            )
                            .all()
                        )
                        if cg:
                            if await confirm_input(
                                cleandoc(
                                    f"""
                                    At least one Channelgroup with the name `{courseName}` already exists.
                                    Existing channel groups are:
                                    {'\n'.join([' - ' + str(c.ChannelGroupId) for c in cg])}
                                    
                                    Should I delete them? (This is safe to to, if this channel group belongs to an old course)
                                    """
                                )
                            ):
                                for existing_channelgroup in cg:
                                    await Channelgroup.delete_group_h(
                                        session,
                                        session.query(ChannelGroup)
                                        .filter(
                                            ChannelGroup.ChannelGroupId
                                            == existing_channelgroup.ChannelGroupId
                                        )
                                        .one(),
                                        self.client,
                                    )
                            else:
                                continue
                        break

                    await dm(
                        f"A Course with the name `{result}` already exists. Please choose another name."
                    )

            promptLan = await dm(
                "Amazing :bothappypad:\nIs your course held in English or German?"
            )
            courseLan, _ = await or_exit(
                UserInput.i8n_german_or_english(
                    self.client, promptLan["id"], timeout=60
                )
            )

            # find any channels that contain the course name but are not part of the course, so they can be deleted
            response_channels = await self.client.get_channels()

            def is_similar_channel(c: dict[str, Any]) -> bool:
                name: str = c["name"].lower()
                uel = "übungsleitung" if courseLan == "de" else "instructors"
                tut = "tutoren" if courseLan == "de" else "tutors"
                return (
                    name.startswith(courseName.lower())
                    and not name.endswith(uel)
                    and not name.endswith(tut)
                )

            similar_chans: list[ZulipChannel] = [
                ZulipChannel(ID=c["stream_id"], name=c["name"])
                for c in response_channels["streams"]
                if is_similar_channel(c)
            ]

            if similar_chans:

                if not await confirm_input(
                    "I found the following channels that containing the course name. I am going to delete them, so please make sure that these channels are no longer needed. We will later create the neccecary channels for the course.\n"
                    + "\n".join(" - " + c.mention for c in similar_chans)
                    + "\n\nAre you sure these channels can be deleted?"
                ):
                    raise DMError(
                        "I cannot create the course without deleting the channels. Please contact an administrator to help you, if it is absolutely necessary to keep one of the channels."
                    )

                for c in similar_chans:
                    await self.client.delete_channel(c.id)

            chgs: list[str] = [
                str(cg_id) for cg_id in session.query(ChannelGroup.ChannelGroupId).all()
            ]
            closest = difflib.get_close_matches(courseName, chgs)
            if closest:
                server_response = await dm(
                    "It looks like there is already at least one Channelgroup with the name of your Course :botsceptical:. Should i delete them?"
                )
                user_response, _ = await or_exit(
                    UserInput.choose(
                        self.client,
                        server_response["id"],
                        ["trashcan", "floppy_disc"],
                        timeout=60,
                    )
                )
                if user_response == "trashcan":
                    for existing_channelgroup_name in closest:
                        await Channelgroup.delete_group_h(
                            session,
                            session.query(ChannelGroup)
                            .filter(
                                ChannelGroup.ChannelGroupId.like(
                                    existing_channelgroup_name
                                )
                            )
                            .one(),
                            self.client,
                        )

            await dm(
                cleandoc(
                    """Great, so let's create a new Channelgroup by choosing an emoji for the Course :bothappy:
                    ```spoiler What is a Channelgroup?
                    A Channelgroup is a collection of Channels that belong to a Course. They enable a better organization of the Channels and an easy way to (un)subscribe to all of them.
                    Every Channelgroup is represented by a unique emoji. Students can react with this emoji to special "claimed" messages to subscribe to all of the Channels in the Channelgroup instantly.
                    Use `help channelgroup` to find out more.
                    ```
                    """
                )
            )

            while True:

                sg_emoji = await short_text_input(
                    "What is the emoji representing the Course?"
                )

                if sg_emoji is None:
                    continue

                emote = Regex.get_emoji_name(sg_emoji)
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

                await dm(
                    f"A Course with the emote :{emote}: already exists. Please choose another emoji."
                )

            # create empty Channelgroup
            courseChannels = await Channelgroup.create_and_get_group(
                session, courseName, courseEmoji, self.client
            )
            cleanup_opterations.append(
                lambda s=courseChannels: Channelgroup.delete_group_h(
                    session, s, self.client
                )
            )

            # add default Channels
            resultG = await confirm_input(
                "Now you can add the default Channels to your course. Every course will have an Announcement-Channel, a Feedback-Channel and a channel for tutors either way. However, are might be a view other channels that could be benefitial for your course.\nDo you want to add a general Channel to your Course?"
            )
            resultO = await confirm_input(
                "Do you want to add a Organization-Channel (in addition to the Announcement-Channel) to your Course?"
            )
            resultM = await confirm_input(
                "Do you want to add a Memes-Channel to your Course?"
            )
            resultT = await confirm_input(
                "Do you want to add a Channel for Tech-Support to your Course?"
            )
            resultF = await confirm_input(
                cleandoc(
                    """Do you want the Feedback-Channel of your Course to allow anonymous Feedback?
                ```spoiler What is anonymous Feedback?
                Anonymous Feedback allows students to send messages to the Feedback-Channel via the bot, so their name is not shown in the message.
                Students will be informed, that the message is anonymous for fellow students and the instructors, but is still visible to the bot and the administrators if the message is inappropriate or harmful.
                This feature is intended to allow students to give feedback without the fear of being judged by their peers but prohibits the use of this feature for harmful or inappropriate messages.
                ```
                """
                )
            )

            if courseChannels is None:
                raise DMError("Could not create Channelgroup for Course.")

            if courseLan is None:
                raise DMError("Could not determine the language of the Course.")

            # wizard adds Feedback and Announcement per default to improve the communication between instructors and students (they have to be removed manually)
            stand_chan_ids: list[int] = await Course.add_standard_channels(
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

            for chan_id in stand_chan_ids:
                cleanup_opterations.append(
                    lambda id=chan_id: self.client.delete_channel(id)
                )

            async def wizard_create_usergroup(
                group_type: str, group_type_de: str, create_channel: bool
            ) -> tuple[UserGroup, ZulipChannel | None]:
                usergroup_name: str = f"{group_type}_{courseName}"

                ug = (
                    session.query(UserGroup)
                    .filter(UserGroup.GroupName == usergroup_name)
                    .all()
                )
                if ug:
                    Usergroup.delete_group(session, ug[0])

                ugdb = Usergroup.create_and_get_group(session, usergroup_name)
                cleanup_opterations.append(
                    lambda ug=ugdb: Usergroup.delete_group(session, ug)
                )

                if await confirm_input(
                    f"Do you already have a list of {group_type.capitalize()} (except from you) for your Course? If not, you can add them later with the command `course add_{group_type} {courseName} <{group_type.capitalize()}...>`. Note, that you do not have to add yourself to the {group_type.capitalize()} group."
                ):
                    # enter list of names
                    while True:
                        result2ct = await short_text_input(
                            "Please enter a list of Names in the format 'Name1,Name2, ..., NameN' The names should be in the format that Zulip uses to mention users (e.g. @name)."
                        )
                        if result2ct is None:
                            continue

                        # parse Channel Names
                        names = result2ct.split(",")

                        for name in names:
                            real_name = Regex.get_user_name(name)
                            if real_name is None:
                                await dm(f"Could not find a user with the name {name}.")
                                continue
                            try:
                                user = ZulipUser(cast(str, real_name))
                                await user
                                Usergroup.add_user_to_group(session, user, ugdb)
                            except Exception:
                                await dm(f"Could not add a user with the name {name}.")
                                continue
                        break

                Usergroup.add_user_to_group(session, sender, ugdb)

                if not create_channel:
                    return ugdb, None

                # get a corresponding Channel
                channel_name = f"{courseName} - {group_type.capitalize()}"
                channel_desc = (
                    f"Internal Channel for {courseName}-{group_type.capitalize()}"
                )
                if courseLan == "de":
                    channel_name = f"{courseName} - {group_type_de.capitalize()}"
                    channel_desc = (
                        f"Interner Kanal für {courseName}-{group_type_de.capitalize()}"
                    )

                chan_ex = await self.client.get_channel_id_by_name(channel_name)
                while chan_ex is not None:
                    new_name = await short_text_input(
                        f"A Channel with the name {channel_name} already exists. I will rename the Channel for you. Please choose the name for the *old* Channel. What should the *old* Channel be renamed to?"
                    )
                    if new_name is not None:
                        old_new_name = await self.client.get_channel_id_by_name(
                            new_name
                        )
                        if old_new_name is not None:
                            await dm(
                                f"A Channel with the name {new_name} already exists."
                            )
                            continue
                        await self.client.update_channel(
                            {
                                "stream_id": chan_ex,
                                "name": new_name,
                            }
                        )

                    chan_ex = await self.client.get_channel_id_by_name(channel_name)

                user_ids = Usergroup.get_user_ids_for_group(session, ugdb)
                user_ids.append(self.client.id)

                exit_and_inform_on_error(
                    await self.client.add_subscriptions(
                        channels=[
                            {
                                "name": channel_name,
                                "description": channel_desc,
                            }
                        ],
                        principals=user_ids,
                        invite_only=True,
                        history_public_to_subscribers=True,
                    )
                )

                chan = ZulipChannel(f"#**{channel_name}**")
                await chan
                cleanup_opterations.append(
                    lambda chan=chan: self.client.delete_channel(chan.id)
                )

                return ugdb, chan

            courseTutors, courseTutorChannel = await wizard_create_usergroup(
                "tutors", "tutoren", True
            )

            resultis = await confirm_input(
                "Do you want a Instructor-Channel for your Course?"
            )

            if not resultis:
                await dm(
                    "Ok, however, it is still necessary to add the Instructors to the Course, even if there is no Channel for them."
                )

            courseInstructors, courseInstructorChannel = await wizard_create_usergroup(
                "instructors", "übungsleiter", resultis
            )

            courseFeedbackChannel: ZulipChannel | None = None
            if resultF:
                courseFeedbackChannel = ZulipChannel(f"#**{courseName} - Feedback**")
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
                FeedbackChannel=courseFeedbackChannel,
            )

            session.add(course)
            session.commit()

            if courseLan == "en":
                welcome_message = f"Welcome to the Course {courseName}.\n\nPlease subscribe to the Channel-Group of this course to stay up to date with all the Channels of this Course. You can do this by reacting to this message with the emoji :{courseEmoji}: if you have not subscribed already via the #**Kanal-Gruppen** Channel."
            else:
                welcome_message = f"Willkommen im Kurs {courseName}.\n\nBitte abonniere die Kanal-Gruppe dieses Kurses, um über alle Kanäle dieses Kurses auf dem Laufenden zu bleiben. Du kannst dies tun, indem du auf diese Nachricht mit dem Emoji :{courseEmoji}: reagierst, wenn du nicht bereits über den #**Kanal-Gruppen**-Kanal abonniert hast."

            rspns = Response.build_message(
                None,
                content=welcome_message,
                msg_type="channel",
                subject=(
                    "Welcome to the Course"
                    if courseLan == "en"
                    else "Willkommen im Kurs"
                ),
                to=stand_chan_ids[0],
            )

            response = await self.client.send_response(rspns)

            if response["result"] != "success":
                raise DMError("Could not send welcome message to the Course Channel.")

            cleanup_opterations.append(
                lambda: self.client.delete_message(response["id"])
            )

            responseEmote = await self.client.send_response(
                Response.build_reaction_from_id(response["id"], courseEmoji)
            )

            if responseEmote["result"] != "success":
                raise DMError("Could not add reaction to the welcome message.")

            async for m in self.invoke_other_cmd(
                Channelgroup.claim_message,  # type: ignore
                sender,
                session,
                message_id=response["id"],
                group_id=courseChannels.ChannelGroupId,
            ):
                yield m

        except Exception as e:
            logging.exception(e)
            session.rollback()
            cleanup_result: (
                None | Coroutine[Any, Any, dict[str, Any]] | Coroutine[Any, Any, None]
            )
            op: (
                Callable[[], None]
                | Callable[[], Coroutine[Any, Any, dict[str, Any]]]
                | Callable[[], Coroutine[Any, Any, None]]
            )
            for op in cleanup_opterations:
                # TODO: too few arguments
                cleanup_result = op()

                if cleanup_result and inspect.isawaitable(cleanup_result):
                    await cleanup_result
                    # avoid rate limiting
                    await asyncio.sleep(0.2)

            if isinstance(e, DMError):
                raise e

            if courseName is None:
                raise DMError(
                    "Something went wrong when creating your Course :botsweat:"
                ) from e

            raise DMError(
                f"Something went wrong when creating the Course `{courseName}` :botsweat:"
            ) from e

        yield DMResponse(f"Course `{courseName}` created :bothappy:")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to add the Channels to.",
    )
    @opt(
        "a",
        long_opt="all",
        description="Add all standard Channels to the Course (General, Organization, normal Feedback, Announcements, TechSupport, Memes).",
    )
    @opt("g", long_opt="general", description="Add a general Channel.")
    @opt("o", long_opt="orga", description="Add a Channel for Organization.")
    @opt(
        "fn",
        long_opt="feedbackbnorm",
        description="Add a normal  Channel for Feedback.",
    )
    @opt(
        "fa",
        long_opt="feedbackanon",
        description="Add an anonymous Channel for Feedback.",
    )
    @opt("n", long_opt="announcements", description="Add a Channel for Announcements.")
    @opt("m", long_opt="memes", description="Add a Channel for Memes.")
    @opt("t", long_opt="tech", description="Add a Channel for Tech-Support.")
    async def add_default_channels(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add standard Channels to a given Course. If Channels with the same names already exist, they will be transferred and not replaced with new ones.
        """
        course: CourseDB = args.course
        lan: Literal["en", "de"] = cast(Literal["en", "de"], str(course.CourseLanguage))
        channels_id: str = str(course.Channels)
        stremgroup: ChannelGroup | None = (
            session.query(ChannelGroup)
            .filter(ChannelGroup.ChannelGroupId == channels_id)
            .first()
        )

        if stremgroup is None:
            raise DMError(
                f"Could not find Channelgroup for Course `{course.CourseName}`."
            )

        if opts.a:
            await Course.add_standard_channels(
                client=self.client,
                session=session,
                name=str(course.CourseName),
                sg=stremgroup,
                lan=lan,
                principals=[sender.id, self.client.id],
            )

        else:
            if opts.fn and opts.fa:
                raise DMError(
                    "You can only add one (normal OR anonymous) Feedback Channel at a time."
                )

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

        yield DMResponse(
            f"Standard Channels added to Course `{course.CourseName}` :bothappy:"
        )


    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to add the Channel to.",
    )
    @arg(
        "channels",
        ty=str,
        description="The name of the new Channel.",
    )
    async def create_channel(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Take a channel with the given name with an additional course prefix (or create it if it does not exist yet) and then add it to the Course's Channelgroup.
        """
        course: CourseDB = args.course
        chan_group: ChannelGroup = Course.get_channelgroup(course, session)
        channel_name: str = str(course.CourseName) + " - " + args.channel

        ex: int | None = await self.client.get_channel_id_by_name(channel_name)

        if ex is None:
            result_channel = await self.client.add_subscriptions(
                channels=[
                    {
                        "name": channel_name,
                        "description": "",
                    }
                ],
                principals=[sender.id, self.client.id],
            )

            if result_channel["result"] != "success":
                raise DMError(result_channel["msg"])

        channel: ZulipChannel = ZulipChannel(f"#**{channel_name}**")
        await channel

        Channelgroup.add_zulip_channels(session, [channel], chan_group)
        yield DMResponse(
            f"Added Channel {channel.mention} to the Course `{course.CourseName}` :bothappy:"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to add the Tutor to.",
    )
    @arg(
        "tutors",
        ty=ZulipUser,
        description="A list of names of the new Tutors.",
        greedy=True,
    )
    async def add_tutors(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add a list of Zulip-Users to the Course's Tutors.
        """
        course: CourseDB = args.course
        u_group: ChannelGroup = Course.get_tutorgroup(course, session)
        tutors: list[ZulipUser] = await Usergroup.get_users_for_group(session, u_group)
        t_chan: ZulipChannel = cast(ZulipChannel, course.TutorChannel)
        await t_chan

        new_tutors: list[ZulipUser] = args.tutors
        to_add: list[int] = []

        for tutor in new_tutors:
            if tutor not in tutors:
                Usergroup.add_user_to_group(session, tutor, u_group)
                to_add.append(tutor.id)

        resp = await self.client.add_subscriptions(
            channels=[{"name": t_chan.name}],
            principals=to_add,
        )

        if resp["result"] != "success":
            raise DMError("Could not add Tutors to the Tutor-Channel.")

        yield DMResponse(
            f"Added {', '.join([t.mention_silent for t in new_tutors])} to the Tutors of the Course `{course.CourseName}` :bothappy:"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to add the Instructor to.",
    )
    @arg(
        "instructors",
        ty=ZulipUser,
        description="A list of names of the new Instructors.",
        greedy=True,
    )
    async def add_instructors(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add a list of Zulip-Users to the Course's Instructors.
        """
        course: CourseDB = args.course
        u_group: ChannelGroup = Course.get_instructorgroup(course, session)
        insts: list[ZulipUser] = await Usergroup.get_users_for_group(session, u_group)

        new_insts: list[ZulipUser] = args.instructors
        to_add: list[int] = []

        for ins in new_insts:
            if ins not in insts:
                Usergroup.add_user_to_group(session, ins, u_group)
                to_add.append(ins.id)

        if course.InstructorChannel is not None:
            ins_chan: ZulipChannel = cast(ZulipChannel, course.InstructorChannel)
            await ins_chan

            resp = await self.client.add_subscriptions(
                channels=[{"name": ins_chan.name}],
                principals=to_add,
            )

            if resp["result"] != "success":
                raise DMError("Could not add Instructors to the Instructor-Channel")

        yield DMResponse(
            f"Added {', '.join([i.mention_silent for i in new_insts])} to the Instructors of the Course `{course.CourseName}` :bothappy:"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to delete.",
    )
    @opt(
        "c",
        long_opt="channelgroup",
        ty=ChannelGroup.ChannelGroupId,
        description="The id of an existing Channelgroup containing the Channels for this Course.",
    )
    @opt(
        "t",
        long_opt="tutors",
        ty=UserGroup.GroupName,
        description="The name of an existing Usergroup containing the Tutors for this Course.",
    )
    @opt(
        "tuts",
        long_opt="tutorial_channel",
        ty=ZulipChannel,
        description="The name of an existing Channel for Instructors.",
    )
    @opt(
        "ins",
        long_opt="instructor_channel",
        ty=ZulipChannel,
        description="The name of an existing Channel for Instructors.",
    )
    async def update(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Update a course with corresponding contents
        """
        course: CourseDB = args.course

        if opts.c:
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

        yield DMResponse(f"Course `{course.CourseName}` updated :bothappy:")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to delete.",
    )
    @opt(
        "c",
        long_opt="channels",
        description="Remove the Channels from the Course, but keep the Channelgroup.",
    )
    @opt(
        "t",
        long_opt="tutors",
        description="Remove the Tutors from the Course but keep the UserGroup.",
    )
    async def clear(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Clear a Course (Channels/Tutors), but keep the underlying components (Channelgroup/UserGroup).
        """
        course: CourseDB = args.course

        if opts.c:
            sg: ChannelGroup | None = (
                session.query(ChannelGroup)
                .filter(ChannelGroup.ChannelGroupId == course.Channels)
                .first()
            )
            if sg is not None:
                channels: list[ZulipChannel] = await Channelgroup.get_channels(
                    session, sg
                )
                await Channelgroup.remove_zulip_channels(session, channels, sg)

                for s in channels:
                    await self.client.delete_channel(s.id)

        if opts.t:
            tutors: UserGroup | None = (
                session.query(UserGroup)
                .filter(UserGroup.GroupId == course.TutorsUserGroup)
                .first()
            )
            if tutors is not None:
                users: list[ZulipUser] = await Usergroup.get_users_for_group(
                    session, tutors
                )
                for user in users:
                    Usergroup.remove_user_from_group(session, user, tutors)

        yield DMResponse(f"Course `{course.CourseName}` cleared :bothappy:")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to delete.",
    )
    @opt(
        "a",
        long_opt="all",
        description="Delete the whole Course (Channelgroup, Usergroups, Channels).",
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
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:

        course: CourseDB = args.course
        c_name = str(course.CourseName)
        channels_id = str(course.Channels)
        tut_ug_id = int(course.TutorsUserGroup)
        ins_ug_id = int(course.InstructorsUserGroup)

        tut_s: ZulipChannel = cast(ZulipChannel, course.TutorChannel)
        await tut_s

        ins_s: ZulipChannel | None = None
        if course.InstructorChannel is not None:
            ins_s = cast(ZulipChannel, course.InstructorChannel)
            await ins_s

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
                strm: list[ZulipChannel] = await Channelgroup.get_channels(session, sg)
                await Channelgroup.remove_zulip_channels(session, strm, sg)

                await Channelgroup.delete_group_h(session, sg, self.client)

                failed: list[str] = []
                for s in strm:
                    resp = await self.client.delete_channel(s.id)
                    if resp["result"] != "success":
                        failed.append(s.name)

                yield DMResponse(f"Channels {', '.join(failed)} could not be deleted.")

        if opts.t or opts.a:
            ugt: UserGroup | None = (
                session.query(UserGroup).filter(UserGroup.GroupId == tut_ug_id).first()
            )

            if ugt is not None:
                Usergroup.delete_group(session, ugt)

        if opts.i or opts.a:
            ugi: UserGroup | None = (
                session.query(UserGroup).filter(UserGroup.GroupId == ins_ug_id).first()
            )

            if ugi is not None:
                Usergroup.delete_group(session, ugi)

        if opts.tuts or opts.a:
            await self.client.delete_channel(tut_s.id)

        if (opts.ins or opts.a) and ins_s is not None:
            await self.client.delete_channel(ins_s.id)

        yield DMResponse(f"Course `{c_name}` deleted :bothappy:")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to mute.",
    )
    async def mute(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Mute all the channels of a Course, for example during the time of an exam. Thus only moderators can send messages to the Channels.
        """
        course: CourseDB = args.course
        channels: list[ZulipChannel] = await Course.get_channels(
            course=course, session=session
        )
        failed_channels: list[str] = []

        for channel in channels:
            request = {
                "stream_id": channel.id,
                "stream_post_policy": 4,
            }
            response = await self.client.update_channel(request)

            if response["result"] != "success":
                failed_channels.append(channel.name)

        if failed_channels:
            raise DMError(
                f"Failed to mute the following Channels: {', '.join(failed_channels)}"
            )

        yield DMResponse(
            f"The Channels of your Course `{course.CourseName}` are now muted :bothappy:"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "course",
        ty=CourseDB.CourseName,
        description="The name of the Course to unmute.",
    )
    async def unmute(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Unmute all the Channels of a Course, for example during the time of an exam. Thus all users can send messages to the Channels.
        """
        course: CourseDB = args.course
        channels: list[ZulipChannel] = await Course.get_channels(
            course=course, session=session
        )
        failed_channels: list[str] = []

        for channel in channels:
            request = {
                "stream_id": channel.id,
                "stream_post_policy": 1,
            }
            response = await self.client.update_channel(request)

            if response["result"] != "success":
                failed_channels.append(channel.name)

        if failed_channels:
            raise DMError(
                f"Failed to unmute the following Channels: {', '.join(failed_channels)}"
            )

        yield DMResponse(
            f"The Channels of your Course `{course.CourseName}` are now unmuted :bothappy:"
        )

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
        n: bool = True,
        g: bool = True,
        o: bool = True,
        fn: bool = True,
        fa: bool = True,
        m: bool = True,
        t: bool = True,
    ) -> list[int]:
        result: list[int] = []

        if principals is None:
            principals = [client.id]
        else:
            principals.append(client.id)

        channels = []
        for opt_abr, suffix_en, suffix_de, desc_en, desc_de in [
            (
                n,
                "Announcements",
                "Ankündigungen",
                f"Welcome to the Channel for Announcements for {name}",
                f"Willkommen im Zulip Kanal für Ankündigungen von {name}",
            ),
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
                f"Welcome to the Channel for Feedback to {name}, where you can send anonymous Feedback with the help of the TUM CS Bot.",
                f"Willkommen im Feedback Zulip Kanal von dem Kurs {name}, in welchem du mit der Hilfe des TUM CS Bot anonymes Feedback senden kannst.",
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
            if not opt_abr:
                continue

            suffix = suffix_en if lan == "en" else suffix_de
            desc = desc_en if lan == "en" else desc_de
            full_name = name + " - " + suffix

            channels.append({"name": full_name, "description": desc})

        try:
            result2: dict[str, Any] = await client.add_subscriptions(
                channels=channels, principals=principals
            )

            if result2["result"] != "success":
                raise DMError("Could not add standard channels to the course.")

            to_add = [ZulipChannel(f"#**{s['name']}**") for s in channels]

            for s in to_add:
                await s

            Channelgroup.add_zulip_channels(session, to_add, sg)

            result = [s.id for s in to_add]

            if fa:
                fb = next(s for s in to_add if f"{name} - Feedback" in s.name)
                session.query(CourseDB).filter(CourseDB.CourseName == name).update(
                    {"FeedbackChannel": fb}
                )
                session.commit()

            if m:
                me = next(s for s in to_add if f"{name} - Memes" in s.name)
                mcg: ChannelGroup = session.query(ChannelGroup).filter(ChannelGroup.ChannelGroupId == "Memes").one_or_none()
                if mcg is not None:
                    Channelgroup.add_zulip_channels(session, [me], mcg)

            return result

        except Exception as e:
            session.rollback()

            for c in channels:
                cid = await client.get_channel_id_by_name(c["name"])
                if cid is not None:
                    await client.delete_channel(cid)

            raise DMError(
                "Something went wrong when creating the default channels :botsad:"
            ) from e

    # ========================================================================================================================
    #       HELPER METHODS
    # ========================================================================================================================

    @staticmethod
    def get_course_by_id(Id: int, session: Session) -> CourseDB:
        result: CourseDB | None = None
        result = session.query(CourseDB).filter(CourseDB.CourseId == Id).one_or_none()

        if result:
            return result

        raise DMError(
            f"Uuups, it looks like i could not find any Course associated with `{Id}` :botsceptical:"
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
        ID = str(course.Channels)
        return (
            session.query(ChannelGroup).filter(ChannelGroup.ChannelGroupId == ID).one()
        )

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
    async def get_tutors(course: CourseDB, session: Session) -> list[ZulipUser]:
        """
        Get the Tutors of a Course a list of ZulipUsers.
        """
        ug: UserGroup = Course.get_tutorgroup(course, session)
        return await Usergroup.get_users_for_group(session, ug)

    @staticmethod
    def get_instructorgroup(course: CourseDB, session: Session) -> UserGroup:
        """
        Get the Tutor-UserGroup of a given Course.
        """
        ID = int(course.InstructorsUserGroup)
        return session.query(UserGroup).filter(UserGroup.GroupId == ID).one()

    @staticmethod
    async def get_instructors(course: CourseDB, session: Session) -> list[ZulipUser]:
        """
        Get the Tutors of a Course a list of ZulipUsers.
        """
        ug: UserGroup = Course.get_instructorgroup(course, session)
        return await Usergroup.get_users_for_group(session, ug)

    @staticmethod
    async def get_channels(course: CourseDB, session: Session) -> list[ZulipChannel]:
        """
        Get the Channels of a Course as list of ZulipChannels.
        """
        sg: ChannelGroup = Course.get_channelgroup(course, session)
        res = await Channelgroup.get_channels(session, sg)
        return res

    @staticmethod
    async def get_channel_names(session: Session, course: CourseDB) -> list[str]:
        """
        Get the Channel Names of a Course as list of strings.
        """
        sg: ChannelGroup = Course.get_channelgroup(course, session)
        return await Channelgroup.get_channel_names(session, [sg])

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
            raise DMError("Could not update Channelgroup :botsad:") from e

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
            raise DMError("Could not update Tutors :botsad:") from e

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
            raise DMError("Could not update Instructors :botsad:") from e

    @staticmethod
    async def _update_tutorchannel(
        course: CourseDB, session: Session, client: AsyncClient, channel: ZulipChannel
    ) -> None:
        """
        Set the Tutor-Channel of a given Course.
        """
        oldTS = cast(ZulipChannel, course.TutorChannel)
        await oldTS
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
            raise DMError("Could not update Tutor-Channel :botsad:") from e

        await client.delete_channel(oldTS.id)

    @staticmethod
    async def _update_instructorchannel(
        course: CourseDB, session: Session, client: AsyncClient, channel: ZulipChannel
    ) -> None:
        """
        Set the Instructor-Channel of a given Course.
        """
        oldIS: ZulipChannel | None = None

        if course.InstructorChannel is not None:
            oldIS = cast(ZulipChannel, course.InstructorChannel)
            await oldIS

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
            raise DMError("Could not update Instructor-Channel :botsad:") from e

        if oldIS is not None:
            await client.delete_channel(oldIS.id)

    @staticmethod
    async def _build_info_message(
        course: CourseDB,
        session: Session,
    ) -> str:
        """
        Build a string with all the information about a course.
        """
        chan_group: ChannelGroup = Course.get_channelgroup(course, session)
        chan_group_name: str = str(chan_group.ChannelGroupId)
        channels: list[ZulipChannel] = await Course.get_channels(
            session=session, course=course
        )
        channel_names: list[str] = [c.mention for c in channels]
        emoji: str = Course.get_emoji(course, session)

        tutors_ug: UserGroup = Course.get_tutorgroup(course, session)
        tutors: list[ZulipUser] = await Course.get_tutors(course, session)
        tutor_channel: ZulipChannel = cast(ZulipChannel, course.TutorChannel)
        await tutor_channel

        instructors_ug: UserGroup = Course.get_instructorgroup(course, session)
        instructors: list[ZulipUser] = await Course.get_instructors(course, session)

        instructor_channel_name = "-"
        if course.InstructorChannel is not None:
            instructor_channel = cast(ZulipChannel, course.InstructorChannel)
            await instructor_channel
            instructor_channel_name = instructor_channel.mention

        feedback_channel_name = "-"
        if course.FeedbackChannel is not None:
            feedback_chan = cast(ZulipChannel, course.FeedbackChannel)
            await feedback_chan
            feedback_channel_name = feedback_chan.mention

        lan: str = ":flag_germany:"
        if course.CourseLanguage == "en":
            lan = ":flag_united_kingdom:"

        return cleandoc(
            f"""
            # **Course Information**
            **Name**: {str(course.CourseName)}
            **Language**: {lan}

            ## **Channels**:

            | Id Channelgroup | Emoji |
            | ---- | ---- |
            | {chan_group_name} | :{emoji}: | 


            {", ".join(channel_names)}


            ## **Tutors**: 

            | Name Usergroup | Tutors | Tutor-Channel |
            | ---- | ---- | ---- |
            | {str(tutors_ug.GroupName)} | {", ".join([t.mention_silent for t in tutors])} | {tutor_channel.mention} |

            ## **Instructors**: 
            
            | Name Usergroup | Instructors | Instructor-Channel |
            | ---- | ---- | ---- |
            | {str(instructors_ug.GroupName)} | {", ".join([i.mention_silent for i in instructors])} | {instructor_channel_name} |

            ## **Anonymous Feedback-Channel**: 
            {feedback_channel_name}
            """
        )
