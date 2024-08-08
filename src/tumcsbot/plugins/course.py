#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from collections.abc import Iterable as IterableClass
from inspect import cleandoc
import re
import logging

from sqlite3 import IntegrityError
from typing import cast, Any, Callable, Iterable
from sqlalchemy import Column, String, Integer, ForeignKey, UniqueConstraint, update, Boolean
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
from tumcsbot.plugins.streamgroup import StreamGroup, Streamgroup
from tumcsbot.plugins.streams import Streams
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


class CourseDB(TableBase):
    """Represents a course in the system."""

    __tablename__ = "Courses"

    CourseId = Column(Integer, primary_key=True, autoincrement=True)
    CourseName = Column(String, unique=True)
    CourseDescription = Column(String, nullable=True)
    CourseLanguage = Column(String, nullable=False)

    Streams = Column(
        String,
        ForeignKey("StreamGroups.StreamGroupId", ondelete="CASCADE"),
        nullable=False,
    )

    TutorsUserGroup = Column(
        Integer, ForeignKey("UserGroups.GroupId", ondelete="CASCADE"), nullable=False
    )

    InstructorsUserGroup = Column(
        Integer, ForeignKey("UserGroups.GroupId", ondelete="CASCADE"), nullable=False
    )

    TutorStream = Column(ZulipStream, nullable=True)
    InstructorStream = Column(ZulipStream, nullable=True)

    _streams = relationship(
        "StreamGroup", back_populates="_course", cascade="all, delete-orphan", single_parent=True,
    )
    _tutors = relationship(
        "UserGroup", back_populates="_courseT", cascade="all, delete-orphan", single_parent=True, foreign_keys="CourseDB.TutorsUserGroup"
    )

    _instructors = relationship(
        "UserGroup", back_populates="_courseI", cascade="all, delete-orphan", single_parent=True, foreign_keys="CourseDB.InstructorsUserGroup"
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
        List all Courses with their associated streams.
        """
        response: str = (
            "Course Name | Emoji | Streams \n---- | ---- | ----"
        )

        courses: list[CourseDB] = session.query(CourseDB).all()
      

        if len(courses) == 0:
            raise DMError(f"No courses found")

        for course in courses:
            course_name = course.CourseName
            streams: list[str] = await Course._get_stream_names(session, self.client, course)
            emoji: str = Course._get_emoji(course, session)
           
            streams_concat: str = ", ".join(f"`{s}`" for s in streams)
            response += (
                f"\n{course_name} | {emoji} :{emoji}: | {streams_concat}"
            )

        yield DMResponse(response)

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the Course.")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the Course.")
    @opt(
        "i",
        long_opt="instructors",
        description="The course has an additional Stream for Instructors.",
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
        streamgroup_emoji: str = args.emoji
        streams : StreamGroup | None = None

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result = await self.client.send_response(Response.build_message(message, content=f"Course `{name}` already exists. Dou you want to replace it with an empty course?"))
            if result["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, msg = await UserInput.confirm(self.client, result["id"])
            if not result:
                yield DMResponse("Ok, I will not create a new course. Please choose another name.")
                return
        
        if (
            session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
            is not None
        ):
            result = await self.client.send_response(Response.build_message(message, content=f"A Streamgroup with :{streamgroup_emoji}: already exists. Dou you want to replace it with an empty Streamgroup for your course?"))
            if result["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, msg = await UserInput.confirm(self.client, result["id"])
            if not result:
                res = await self.client.send_response(Response.build_message(message, content=f"Do you want to use the existing Streamgroup with :{streamgroup_emoji}: for your course?"))
                if res["result"] != "success":
                    raise DMError("Could not send message to user")
                
                res, msg = await UserInput.confirm(self.client, res["id"])
                if not res:
                    yield DMResponse(f"Ok, I will not create a new course then. Please choose another emote because :{streamgroup_emoji}: is already in use :botsad:")
                    return
                else:
                    streams : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
            else: 
                sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
                Streamgroup._delete_group(session, sg)

        resLan = await self.client.send_response(Response.build_message(message, content=f"Is your course held in English or German?"))
        lan, _ = await UserInput.i8n_german_or_english(self.client, resLan["id"])

        try:
            # get a corresponding (empty) Streamgroup
            if not streams:
                streamgroup_name: str = "streams_" + name
                streams: StreamGroup = Streamgroup._create_and_get_group(
                    session, streamgroup_name, streamgroup_emoji
                )

            # get a corresponding (empty) Usergroup
            usergroup_name_tut: str = "tutors_" + name

            if (
                session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_tut).first()
                is not None
            ):
                result = await self.client.send_response(Response.build_message(message, content=f"Usergroup for Tutors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?"))
                if result["result"] != "success":
                    Streamgroup._delete_group(session, streams)
                    raise DMError("Could not send message to user")
            
                result, msg = await UserInput.confirm(self.client, result["id"])
                if not result:
                    Streamgroup._delete_group(session, streams)
                    raise DMError("Ok, I will not create a new course then. Please choose another name.")
                else:
                    ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_tut).first()
                    Usergroup.delete_group(session, ug)
                
            tutors = Usergroup.create_and_get_group(session, usergroup_name_tut)

            # get a corresponding (empty) Usergroup
            usergroup_name_ins: str = "instructors_" + name

            if (
                session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_ins).first()
                is not None
            ):
                result = await self.client.send_response(Response.build_message(message, content=f"Usergroup for Instructors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?"))
                if result["result"] != "success":
                    Streamgroup._delete_group(session, streams)
                    raise DMError("Could not send message to user")
            
                result, msg = await UserInput.confirm(self.client, result["id"])
                if not result:
                    Streamgroup._delete_group(session, streams)
                    Usergroup.delete_group(session, tutors)
                    raise DMError("Ok, I will not create a new course then. Please choose another name.")
                else:
                    ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_ins).first()
                    Usergroup.delete_group(session, ug)
        
            instructors = Usergroup.create_and_get_group(session, usergroup_name_ins)

            # get a corresponding (empty) Stream for Tutors
            tutors_stream_name : str = name + " - Tutors"
            tutors_stream_desc : list[str] = [f"Internal Stream for {name}-Tutors"]
            if lan == "de":
                tutors_stream_name: str = name + " - Tutoren"
                tutors_stream_desc: list[str] = [f"Interner Stream für {name}-Tutoren"]

            tut_ex = await self.client.get_stream_id_by_name(tutors_stream_name)

            if tut_ex is not None:
                result = await self.client.send_response(Response.build_message(message, content=f"Stream for Tutors of this course already exists. Dou you want to replace it with a new empty Stream for your course?"))
                if result["result"] != "success":
                    Streamgroup._delete_group(session, streams)
                    Usergroup.delete_group(session, tutors)
                    Usergroup.delete_group(session, instructors)
                    raise DMError("Could not send message to user")
            
                result, msg = await UserInput.confirm(self.client, result["id"])
                if not result:
                    Streamgroup._delete_group(session, streams)
                    Usergroup.delete_group(session, tutors)
                    Usergroup.delete_group(session, instructors)
                    raise DMError("Ok, I will not create a new course then. Please choose another name.")
                else:
                    await self.client.delete_stream(tut_ex)

            result: dict[str, Any] = await self.client.add_subscriptions(
                streams=[{"name": tutors_stream_name, "description": " ".join(tutors_stream_desc)}]
            )
            if result["result"] != "success":
                Streamgroup._delete_group(session, streams)
                Usergroup.delete_group(session, tutors)
                Usergroup.delete_group(session, instructors)
                raise DMError(result["msg"])
            
            
            tutors_stream: ZulipStream = ZulipStream(f"#**{tutors_stream_name}**")
            await tutors_stream
            Streamgroup._add_zulip_streams(session, [tutors_stream], streams)

            # get a corresponding (empty) Stream for Instructors or None
            instructor_stream: ZulipStream | None = None
            if opts.i:

                instructor_stream_name: str = name + " - Instructors"
                instructor_stream_desc: list[str] = [f"Internal Stream for Instructors of {name}"]
                if lan == "de":
                    instructor_stream_name: str = name + " - Instructors"
                    instructor_stream_desc: list[str] = [f"Interner Stream für {name}-Instructors"]

                ins_ex = await self.client.get_stream_id_by_name(instructor_stream_name)

                if ins_ex is not None:
                    result = await self.client.send_response(Response.build_message(message, content=f"Stream for Instructors of this course already exists. Dou you want to replace it with a new empty Stream for your course?"))
                    if result["result"] != "success":
                        Streamgroup._delete_group(session, streams)
                        Usergroup.delete_group(session, tutors)
                        Usergroup.delete_group(session, instructors)
                        await self.client.delete_stream(await tutors_stream.id)
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"])
                    if not result:
                        Streamgroup._delete_group(session, streams)
                        Usergroup.delete_group(session, tutors)
                        Usergroup.delete_group(session, instructors)
                        await self.client.delete_stream(tutors_stream.id)
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        await self.client.delete_stream(ins_ex)

                result: dict[str, Any] = await self.client.add_subscriptions(
                    streams=[{"name": instructor_stream_name, "description": " ".join(instructor_stream_desc)}]
                )
                if result["result"] != "success":
                    Streamgroup._delete_group(session, streams)
                    Usergroup.delete_group(session, tutors)
                    Usergroup.delete_group(session, instructors)
                    await self.client.delete_stream(tutors_stream.id)
                    raise DMError(result["msg"])

                
                instructor_stream: ZulipStream = ZulipStream(f"#**{instructor_stream_name}**")
                await instructor_stream
                Streamgroup._add_zulip_streams(session, [instructor_stream], streams)
 
            # create and add a Course to the DB
            course: CourseDB = CourseDB(
                CourseName=name,
                CourseLanguage=lan,
                Streams=streams.StreamGroupId,
                TutorsUserGroup=tutors.GroupId,
                InstructorsUserGroup=instructors.GroupId,
                TutorStream=tutors_stream,
                InstructorStream=instructor_stream
            )

            session.add(course)
            session.commit()

        except (sqlalchemy.exc.IntegrityError) as e:
            session.rollback()
            raise DMError(f"Something went wrong when creating the course `{name}` :botsweat:")

        yield DMResponse(f"Course `{name}` created :bothappypad:")

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the Course")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the Streamgroup.")
    @opt(
        "s",
        long_opt="streamgroup",
        type=StreamGroup.StreamGroupId,
        description="The id of a Streamgroup containing the streams for this course.",
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
        long_opt="tutor_stream",
        type=ZulipStream,
        description="The course has an additional Stream for tutors.",
    )
    @opt(
        "ins",
        long_opt="instructor_stream",
        type=ZulipStream,
        description="The course has an additional Stream for Instructors.",
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
        streamgroup_emoji: str = args.emoji
        streams: StreamGroup | None = None

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result = await self.client.send_response(Response.build_message(message, content=f"Course `{name}` already exists. Dou you want to replace it with the new course?"))
            if result["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, msg = await UserInput.confirm(self.client, result["id"])
            if not result:
                yield DMResponse("Ok, I will not create a new course. Please choose another name.")
                return
        
        if (
            session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
            is not None
        ):
            result = await self.client.send_response(Response.build_message(message, content=f"A Streamgroup with :{streamgroup_emoji}: already exists. Dou you want to replace it with a new Streamgroup for your course?"))
            if result["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, msg = await UserInput.confirm(self.client, result["id"])
            if not result:
                res = await self.client.send_response(Response.build_message(message, content=f"Do you want to use the existing Streamgroup with :{streamgroup_emoji}: for your course?"))
                if res["result"] != "success":
                    raise DMError("Could not send message to user")
                
                res, msg = await UserInput.confirm(self.client, res["id"])
                if not res:
                    yield DMResponse(f"Ok, I will not create a new course then. Please choose another emote because :{streamgroup_emoji}: is already in use :botsad:")
                    return
                else:
                    streams : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
            else: 
                sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
                Streamgroup._delete_group(session, sg)

        resLan = await self.client.send_response(Response.build_message(message, content=f"Is your course held in English or German?"))
        lan, _ = await UserInput.i8n_german_or_english(self.client, resLan["id"])

        try:
            # get corresponding Streamgroup
            if not  streams:
                if opts.s:
                    streams = opts.s
                else:
                    streamgroup_name: str = "streams_" + name
                    streams = Streamgroup._create_and_get_group(
                        session, streamgroup_name, streamgroup_emoji
                    )

            # get corresponding Usergroup
            tutors: Usergroup
            if opts.t:
                tutors = opts.t
            else:
                usergroup_name: str = "tutors_" + name

                if (
                    session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name).first()
                    is not None
                ):
                    result = await self.client.send_response(Response.build_message(message, content=f"Usergroup for Tutors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?"))
                    if result["result"] != "success":
                        Streamgroup._delete_group(session, streams)
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"])
                    if not result:
                        Streamgroup._delete_group(session, streams)
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name).first()
                        Usergroup.delete_group(session, ug)
                    
                tutors = Usergroup.create_and_get_group(session, usergroup_name)

            # get corresponding Usergroup
            instructors: Usergroup
            if opts.i:
                instructors = opts.i
            else:
                usergroup_name: str = "instructors_" + name

                if (
                    session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name).first()
                    is not None
                ):
                    result = await self.client.send_response(Response.build_message(message, content=f"Usergroup for Instructors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?"))
                    if result["result"] != "success":
                        Streamgroup._delete_group(session, streams)
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"])
                    if not result:
                        Streamgroup._delete_group(session, streams)
                        Usergroup.delete_group(session, tutors)
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name).first()
                        Usergroup.delete_group(session, ug)
            
                instructors = Usergroup.create_and_get_group(session, usergroup_name)

            # get corresponding Stream for Tutors
            tutors_stream: ZulipStream
            if opts.tuts:
                tutors_stream = opts.tuts
            else:
                tutors_stream_name: str = name + " - Tutors"
                tutors_stream_desc: list[str] = [f"Internal Stream for {name}-Tutors"]
                if lan == "de":
                    tutors_stream_name: str = name + " - Tutoren"
                    tutors_stream_desc: list[str] = [f"Interner Stream für {name}-Tutoren"]
               
                tut_ex = await self.client.get_stream_id_by_name(tutors_stream_name)

                if tut_ex is not None:
                    result = await self.client.send_response(Response.build_message(message, content=f"Stream for Tutors of this course already exists. Dou you want to replace it with a new empty Stream for your course?"))
                    if result["result"] != "success":
                        Streamgroup._delete_group(session, streams)
                        Usergroup.delete_group(session, tutors)
                        Usergroup.delete_group(session, instructors)
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"])
                    if not result:
                        Streamgroup._delete_group(session, streams)
                        Usergroup.delete_group(session, tutors)
                        Usergroup.delete_group(session, instructors)
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        await self.client.delete_stream(tut_ex)

                result: dict[str, Any] = await self.client.add_subscriptions(
                    streams=[{"name": tutors_stream_name, "description": " ".join(tutors_stream_desc)}]
                )
                if result["result"] != "success":
                    Streamgroup._delete_group(session, streams)
                    Usergroup.delete_group(session, tutors)
                    Usergroup.delete_group(session, instructors)
                    raise DMError(result["msg"])
              
                tutors_stream = ZulipStream(f"#**{tutors_stream_name}**")
                await tutors_stream


            # get a corresponding Stream for Instructors or None
            instructor_stream: ZulipStream | None = None
            if opts.ins:
                instructor_stream = opts.ins
            else:
                instructor_stream_name: str = name + " - Instructors"
                instructor_stream_desc: list[str] = [f"Internal Stream for Instructors of {name}"]
                if lan == "de":
                    instructor_stream_name: str = name + " - Instructors"
                    instructor_stream_desc: list[str] = [f"Interner Stream für {name}-Instructors"]

                ins_ex = await self.client.get_stream_id_by_name(instructor_stream_name)

                if ins_ex is not None:
                    result = await self.client.send_response(Response.build_message(message, content=f"Stream for Instructors of this course already exists. Dou you want to replace it with a new empty Stream for your course?"))
                    if result["result"] != "success":
                        Streamgroup._delete_group(session, streams)
                        Usergroup.delete_group(session, tutors)
                        Usergroup.delete_group(session, instructors)
                        await self.client.delete_stream(await tutors_stream.id)
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"])
                    if not result:
                        Streamgroup._delete_group(session, streams)
                        Usergroup.delete_group(session, tutors)
                        Usergroup.delete_group(session, instructors)
                        await self.client.delete_stream(tutors_stream.id)
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        await self.client.delete_stream(ins_ex)

                result: dict[str, Any] = await self.client.add_subscriptions(
                    streams=[{"name": instructor_stream_name, "description": " ".join(instructor_stream_desc)}]
                )
                if result["result"] != "success":
                    Streamgroup._delete_group(session, streams)
                    Usergroup.delete_group(session, tutors)
                    Usergroup.delete_group(session, instructors)
                    await self.client.delete_stream(tutors_stream.id)
                    raise DMError(result["msg"])
                
                instructor_stream = ZulipStream(f"#**{instructor_stream_name}**")
                await instructor_stream

            Streamgroup._add_zulip_streams(session, [tutors_stream, instructor_stream], streams)
 
            # create and add a Course to the DB
            course: CourseDB = CourseDB(
                CourseName=name,
                CourseLanguage=lan,
                Streams=streams.StreamGroupId,
                TutorsUserGroup=tutors.GroupId,
                InstructorsUserGroup=instructors.GroupId,
                TutorStream=tutors_stream,
                InstructorStream=instructor_stream
            )
         
            session.add(course)
            session.commit()

            # subscribe tutors to Tutorstream
            tut_list: list[int] = Usergroup.get_user_ids_for_group(session,tutors)
            await self.client.subscribe_users(user_ids=tut_list,
                                                stream_name=tutors_stream.name,
                                                allow_private_streams=True)
            
            # subscribe instructors to Instructorstream
            if opt.ins:
                ins_list: list[int] = Usergroup.get_user_ids_for_group(session,instructors)
                await self.client.subscribe_users(user_ids=ins_list,
                                                    stream_name=instructor_stream.name,
                                                    allow_private_streams=True)

        except (sqlalchemy.exc.IntegrityError) as e:
            session.rollback()
            raise DMError(f"Something went wrong when creating the course `{name}` :botsweat:")

        yield DMResponse(f"Course `{name}` created :bothappypad:")


    @command
    @privilege(Privilege.ADMIN)
    @arg("course", type=CourseDB.CourseName,description="The name of the Course to add the streams to.")
    @opt(
        "a",
        long_opt="all",
        description="Add all standard steams to the course (Allgemein, Organisation, Feedback, Ankündigungen, Technik, Memes)."
    )
    @opt(
        "g",
        long_opt="general",
        description="Add a general stream."
    )
    @opt(
        "o",
        long_opt="orga",
        description="Add a stream for Organization."
    )
    @opt(
        "f",
        long_opt="feedback",
        description="Add a stream for Feedback."
    )
    @opt(
        "n",
        long_opt="announcements",
        description="Add a stream for Announcements."
    )
    @opt(
        "m",
        long_opt="memes",
        description="Add a stream for Memes."
    )
    @opt(
        "t",
        long_opt="tech",
        description="Add a stream for Tech-Support."
    )
    async def add_default_streams(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Add standard streams to a given course. If Streams with the same names already exist, they will be transferred and not replaced with new ones.
        """
        course: CourseDB = args.course 
        lan : str = course.CourseLanguage
        streams_id: str = course.Streams
        stremgroup: StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupId==streams_id).first()

        if opts.a:
            async for msg in Course.add_standard_streams(self, self.client, session, message, course.CourseName, stremgroup, lan):
                yield msg
            
        else:
            async for msg in Course.add_standard_streams(self, self.client, session, message, course.CourseName, stremgroup, lan, opts.g, opts.o, opts.f, opts.n, opts.m, opts.t):
                yield msg

        yield DMResponse(f"Standard Streams added to Course `{course.CourseName}`.")
        

    @command
    @privilege(Privilege.ADMIN)
    @arg("course", type=CourseDB.CourseName,description="The name of the Course to delete.")
    @opt(
        "a",
        long_opt="all",
        description="Delete the whole course (Streamgroup, Usergroups, Streams).",
    )
    @opt(
        "s",
        long_opt="streamgroup",
        description="Delete also Streamgroup",
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
        long_opt="tutor_stream",
        description="Delete also Stream for Tutors.",
    )
    @opt(
        "ins",
        long_opt="instructor_stream",
        description="Delete also Stream for Instructors.",
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
        c_name : str = course.CourseName
        streams_id: str = course.Streams
        tut_ug_id:int = course.TutorsUserGroup
        ins_ug_id:int = course.InstructorsUserGroup
        tut_s: ZulipStream = course.TutorStream
        ins_s: ZulipStream = course.InstructorStream

        try:
            session.query(CourseDB).filter(CourseDB.CourseId==course.CourseId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError(f"Could not delete Course `{course.CourseName}`.") from e
        
        if opts.s or opts.a:
            sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupId==streams_id).first()

            strm : list[str] = await Streamgroup._get_stream_names(session, self.client, [sg])
            await Streamgroup._remove_streams(session, self.client, sg, strm)

            Streamgroup._delete_group(session, sg)

            for s in strm:
                sid = await self.client.get_stream_id_by_name(s)
                if sid is not None:
                    await self.client.delete_stream(sid)
        
        if opts.t or opts.a:
            ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupId==tut_ug_id).first()
            
            Usergroup.delete_group(session, ug)
            
        if opts.i or opts.a:
            ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupId==ins_ug_id).first()
            
            Usergroup.delete_group(session, ug)
            
        if opts.tuts or opts.a:
            await self.client.delete_stream(tut_s.id)

        if (opts.ins or opts.a) and ins_s is not None:
            await self.client.delete_stream(ins_s.id)

        yield DMResponse(f"Course `{c_name}` deleted.")

            
    @command
    @privilege(Privilege.ADMIN)
    @arg("course", type=CourseDB.CourseName,description="The name of the Course to delete.")
    @opt(
        "s",
        long_opt="streamgroup",
        type=StreamGroup.StreamGroupId,
        description="The id of an existing Streamgroup containing the streams for this course.",
    )
    @opt(
        "t",
        long_opt="tutors",
        type=UserGroup.GroupName,
        description="The name of an existing Usergroup containing the tutors for this course.",
    )
    @opt(
        "tuts",
        long_opt="tutor_stream",
        type=ZulipStream,
        description="The name of an existing Stream for Instructors.",
    )
    @opt(
        "ins",
        long_opt="instructor_stream",
        type=ZulipStream,
        description="The name of an existing Stream for Instructors.",
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
            streams: StreamGroup = opts.s
            Course._update_streamgroup(course, session, streams)

        if opts.t:
            tutors: UserGroup = opts.t
            Course._update_tutorgroup(course, session, tutors)

        if opts.tuts:
            tutstream: ZulipStream = opts.tuts
            await Course._update_tutorstream(course, session,self.client, tutstream)

        if opts.ins:
            insstream : ZulipStream = opts.ins
            await Course._update_instructorstream(course, session,self.client, insstream)

    @command
    @privilege(Privilege.ADMIN)
    @arg("course", type=CourseDB.CourseName,description="The name of the Course to delete.")
    @opt("s", long_opt="streams", description="Remove the streams from the Course.")
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
        Clear a course (Streams/Tutors), but keep the underlying components (Streamgroup/UserGroup).
        """
        course: CourseDB = args.course

        if opts.s:
            sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupId==course.Streams).first()
            streams : list[str] = Streamgroup._get_stream_names(session, self.client, [sg])
            Streamgroup._remove_streams(session, self.client, sg, streams)
        
        if opts.t:
            tutors: UserGroup = session.query(UserGroup).filter(UserGroup.GroupId==course.TutorsUserGroup).first()
            users : list[ZulipUser] = Usergroup.get_users_for_group(session, tutors)
            for user in users:
                Usergroup.remove_user_from_group(session, user ,tutors)


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
        courseName : str | None = None
        courseEmoji : str | None = None
        courseLan : str | None = None
        courseStreams : StreamGroup | None = None
        courseTutors : UserGroup | None = None
        courseInstructors : UserGroup | None = None
        courseTutorStream : ZulipStream | None = None
        courseInstructorStream : ZulipStream | None = None

        yield DMResponse("Welcome to the Course Creation Wizard :bothappypad:")

        yield DMResponse("Let's start by choosing a name for your new course :bothappy:")

        while True:
            prompt = self.client.send_response(Response.build_message(message, content="What is the name of the course?"))
            if prompt["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, _ = await UserInput.short_text_response(prompt["id"])
            if result is None:
                yield DMResponse("Please provide a name for the course.")
            else: 
                c : CourseDB = session.query(CourseDB).filter(CourseDB.CourseName==result).one_or_none()
                if c is None:
                    courseName = result
                    break
                else:
                    yield DMResponse(f"A course with the name `{result}` already exists. Please choose another name.")

        yield DMResponse("Great, so now we continue by choosing an emoji for the course :bothappy:")

        while True:
            prompt = self.client.send_response(Response.build_message(message, content="What is the emoji representing the course?"))
            if prompt["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, _ = await UserInput.short_text_response(prompt["id"])
            
            if result is None:
                yield DMResponse("Please provide an emoji for the course.")
            else: 
                emote = Regex.get_emoji_name(result)
                if emote is None:
                    yield DMResponse("Please provide a valid emoji.")
                    continue
                sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==emote).one_or_none()
                if sg is None:
                    courseEmoji = emote
                    break
                else:
                    yield DMResponse(f"A course with the emote :{emote}: already exists. Please choose another name.")

        promptLan = await self.client.send_response(Response.build_message(message, content=f"Amazing :bothappypad:\nIs your course held in English or German?"))
        courseLan, _ = await UserInput.i8n_german_or_english(self.client, promptLan["id"])



        # TODO: choose modalities of usergroups (initialize)
        # TODO: choose modalities of streams (default streams)

        # get a corresponding Stream for Tutors
        tutors_stream_name : str = courseName + " - Tutors"
        tutors_stream_desc : list[str] = [f"Internal Stream for {courseName}-Tutors"]
        if courseLan == "de":
            tutors_stream_name: str = courseName + " - Tutoren"
            tutors_stream_desc: list[str] = [f"Interner Stream für {courseName}-Tutoren"]

        
        tut_ex = await self.client.get_stream_id_by_name(tutors_stream_name)

        if tut_ex is not None:
            result = await self.client.send_response(Response.build_message(message, content=f"Stream for Tutors of this course already exists. Dou you want to replace it with a new Stream for your course?"))
            if result["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, _ = await UserInput.confirm(self.client, result["id"])
            if not result:
                while True:
                    prompt = self.client.send_response(Response.build_message(message, content="Please choose another name for the Tutor-Stream."))
                    if prompt["result"] != "success":
                        raise DMError("Could not send message to user")
                    
                    res, _ = await UserInput.short_text_response(prompt["id"])
                    if res is None:
                        yield DMResponse("Please provide a name for the Stream.")
                    else: 
                        tut_ex = await self.client.get_stream_id_by_name(res)
                        if tut_ex is None:
                            tutors_stream_name = res
                            break
                        else:
                            yield DMResponse(f"A Stream with the name {res} already exists.")
            else:
                await self.client.delete_stream(tut_ex)

        result: dict[str, Any] = await self.client.add_subscriptions(
            streams=[{"name": tutors_stream_name, "description": " ".join(tutors_stream_desc)}]
        )
        if result["result"] != "success":
            raise DMError(result["msg"])
        
        courseTutorStream: ZulipStream = ZulipStream(f"#**{tutors_stream_name}**")
        await courseTutorStream

        # get a corresponding Stream for Instructors or None
        prompt = self.client.send_response(Response.build_message(message, content="Do you want a Instructor-Stream for your course?"))
        if prompt["result"] != "success":
            raise DMError("Could not send message to user")
    
        result, _ = await UserInput.confirm(self.client, result["id"])
        if result is not None:

            instructor_stream_name: str = courseName + " - Instructors"
            instructor_stream_desc: list[str] = [f"Internal Stream for Instructors of {courseName}"]
            if courseLan == "de":
                instructor_stream_name: str = courseName + " - Instructors"
                instructor_stream_desc: list[str] = [f"Interner Stream für {courseName}-Instructors"]

            ins_ex = await self.client.get_stream_id_by_name(instructor_stream_name)

            if ins_ex is not None:
                result = await self.client.send_response(Response.build_message(message, content=f"Stream for Instructor of this course already exists. Dou you want to replace it with a new Stream for your course?"))
                if result["result"] != "success":
                    raise DMError("Could not send message to user")
            
                result, _ = await UserInput.confirm(self.client, result["id"])
                if not result:
                    while True:
                        prompt = self.client.send_response(Response.build_message(message, content="Please choose another name for the Instructor-Stream."))
                        if prompt["result"] != "success":
                            raise DMError("Could not send message to user")
                        
                        res, _ = await UserInput.short_text_response(prompt["id"])
                        if res is None:
                            yield DMResponse("Please provide a name for the Stream.")
                        else: 
                            ins_ex = await self.client.get_stream_id_by_name(res)
                            if ins_ex is None:
                                instructor_stream_name = res
                                break
                            else:
                                yield DMResponse(f"A Stream with the name {res} already exists.")
                else:
                    await self.client.delete_stream(tut_ex)

            result: dict[str, Any] = await self.client.add_subscriptions(
                streams=[{"name": instructor_stream_name, "description": " ".join(instructor_stream_desc)}]
            )
            if result["result"] != "success":
                raise DMError(result["msg"])
            
            courseInstructorStream: ZulipStream = ZulipStream(f"#**{instructor_stream_name}**")
            await courseInstructorStream
 

        # TODO: Create streamgroup
        # TODO: Add 2 Streams to Streamgroup

        # TODO: Create Usergroup for Tutors
        # TODO: Create Usergroup for Instructors
        # subscribe tutors to Tutorstream
        tut_list: list[int] = Usergroup.get_user_ids_for_group(session,courseTutors)
        await self.client.subscribe_users(user_ids=tut_list,
                                            stream_name=courseTutorStream.name,
                                            allow_private_streams=True)
        
        # subscribe instructors to Instructorstream
        if courseInstructorStream is not None:
            ins_list: list[int] = Usergroup.get_user_ids_for_group(session,courseInstructors)
            await self.client.subscribe_users(user_ids=ins_list,
                                                stream_name=courseInstructorStream.name,
                                                allow_private_streams=True)
                
        try:
            # create and add a Course to the DB
            course: CourseDB = CourseDB(
                CourseName=courseName,
                CourseLanguage=courseLan,
                Streams=courseStreams.StreamGroupId,
                TutorsUserGroup=courseTutors.GroupId,
                InstructorsUserGroup=courseInstructors.GroupId,
                TutorStream=courseTutorStream,
                InstructorStream=courseInstructorStream
            )
         
            session.add(course)
            session.commit()
        except (sqlalchemy.exc.IntegrityError) as e:
            session.rollback()
            raise DMError(f"Something went wrong when creating the course `{courseName}` :botsweat:")

        yield DMResponse(f"Course `{courseName}` created :bothappypad:")


    # ========================================================================================================================
    #       CLASS METHODS
    # ========================================================================================================================
    @staticmethod
    async def add_standard_streams(plugin:Plugin, client:AsyncClient, session:Session, message:dict[str, Any], name:str, sg:StreamGroup, lan:str
                                   , g: bool = True, o: bool = True, f: bool = True, n: bool = True, m: bool = True, t: bool = True):
        
        allg_stream = None
        org_stream = None
        fb_stream = None
        ank_stream = None
        tech_stream = None
        memes_stream = None

        # get a corresponding Streams
        if g:
            allg_name: str = name + " - General"
            allg_desc: list[str] = [f"Welcome to the Stream for general info to the course {name}"]
            if lan == "de":
                allg_name: str = name + " - Allgemein"
                allg_desc: list[str] = [f"Willkommen im allgemeinen Zulip Stream von dem Kurs {name}"]

            ex = await client.get_stream_id_by_name(allg_name)

            if ex is None:
                    result: dict[str, Any] = await client.add_subscriptions(
                        streams=[{"name": allg_name, "description": " ".join(allg_desc)}]
                    )
                    if result["result"] != "success":
                        yield DMResponse(result["msg"])

            allg_stream: ZulipStream = ZulipStream(f"#**{allg_name}**")
            await allg_stream

        if o:
            org_name: str = name + " - Organization"
            org_desc: list[str] = [f"Welcome to the organizational Stream of the course {name}"]
            if lan == "de":
                org_name: str = name + " - Organisation"
                org_desc: list[str] = [f"Willkommen im Orga-Zulip Stream von dem Kurs {name}"]

            ex = await client.get_stream_id_by_name(org_name)

            if ex is None:
                    result: dict[str, Any] = await client.add_subscriptions(
                        streams=[{"name": org_name, "description": " ".join(org_desc)}]
                    )
                    if result["result"] != "success":
                        yield DMResponse(result["msg"])
                    
            org_stream: ZulipStream = ZulipStream(f"#**{org_name}**")
            await org_stream

        if f:
            fb_name: str = name + " - Feedback"
            fb_desc: list[str] = [f"Welcome to the Stream for Feedback to the course {name}"]
            if lan == "de":
                fb_name: str = name + " - Feedback"
                fb_desc: list[str] = [f"Willkommen im Feedback Zulip Stream von dem Kurs {name}"]
            

            ex = await client.get_stream_id_by_name(fb_name)

            if ex is None:
                    result: dict[str, Any] = await client.add_subscriptions(
                        streams=[{"name": fb_name, "description": " ".join(fb_desc)}]
                    )
                    if result["result"] != "success":
                        yield DMResponse(result["msg"])

            fb_stream: ZulipStream = ZulipStream(f"#**{fb_name}**")
            await fb_stream

        if n:
            ank_name: str = name + " - Announcements"
            ank_desc: list[str] = [f"Welcome to the Stream for Announcements for the course {name}"]
            if lan == "de":
                ank_name: str = name + " - Ankündigungen"
                ank_desc: list[str] = [f"Willkommen im Zulip Stream für Ankündigungen von dem Kurs {name}"]
            
            ex = await client.get_stream_id_by_name(ank_name)

            if ex is None:
                    result: dict[str, Any] = await client.add_subscriptions(
                        streams=[{"name": ank_name, "description": " ".join(ank_desc)}]
                    )
                    if result["result"] != "success":
                        yield DMResponse(result["msg"])

            ank_stream: ZulipStream = ZulipStream(f"#**{ank_name}**")
            await ank_stream

        if t:
            tech_name: str = name + " - TechSupport"
            tech_desc: list[str] = [f"Welcome to the Stream for Tech-Support in the course {name}"]
            if lan == "de":
                tech_name: str = name + " - Technik"
                tech_desc: list[str] = [f"Willkommen im Technik Zulip Stream von dem Kurs {name}"]
           
            ex = await client.get_stream_id_by_name(tech_name)

            if ex is None:
                    result: dict[str, Any] = await client.add_subscriptions(
                        streams=[{"name": tech_name, "description": " ".join(tech_desc)}]
                    )
                    if result["result"] != "success":
                        yield DMResponse(result["msg"])

            tech_stream: ZulipStream = ZulipStream(f"#**{tech_name}**")
            await tech_stream

        if m:
            memes_name: str = name + " - Memes"
            memes_desc: list[str] = [f"Welcome to the Stream for top-quality Memes of the course {name}"]
            if lan == "de":
                memes_name: str = name + " - Memes"
                memes_desc: list[str] = [f"Willkommen im Meme Zulip Stream von dem Kurs {name}"]
            
            ex = await client.get_stream_id_by_name(memes_name)

            if ex is None:
                    result: dict[str, Any] = await client.add_subscriptions(
                        streams=[{"name": memes_name, "description": " ".join(memes_desc)}]
                    )
                    if result["result"] != "success":
                        yield DMResponse(result["msg"])

            memes_stream: ZulipStream = ZulipStream(f"#**{memes_name}**")
            await memes_stream

        to_add = [ s for s in [allg_stream, org_stream, fb_stream, ank_stream, tech_stream, memes_stream] if s is not None ]

        Streamgroup._add_zulip_streams(session,
                                       to_add,
                                       sg)
        



    # ========================================================================================================================
    #       HELPER METHODS
    # ========================================================================================================================

    @staticmethod
    def _get_course_by_id(id: int, session: Session) -> CourseDB:
        result: CourseDB | None = None
        result = session.query(CourseDB).filter(CourseDB.CourseId == id).one_or_none()

        if result:
            return result

        raise DMError(
            f"Uuups, it looks like i could not find any Course associated with `{id}` :botsceptical:"
        )

    @staticmethod
    def _get_course_by_name(name: str, session: Session) -> CourseDB:
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
    def _get_streamgroup(course: CourseDB, session: Session) -> StreamGroup:
        """
        Get the StreamGroup of a given Course.
        """
        id: int = course.Streams
        return session.query(StreamGroup).filter(StreamGroup.StreamGroupId == id).one()
    
    @staticmethod
    def _get_emoji(course: CourseDB, session: Session) -> str:
        """
        Get the Emoji of the StreamGroup associated with a given Course.
        """
        sg : StreamGroup = Course._get_streamgroup(course, session)
        return sg.StreamGroupEmote

    @staticmethod
    def _get_tutorgroup(course: CourseDB, session: Session) -> UserGroup:
        """
        Get the Tutor-UserGroup of a given Course.
        """
        id: int = course.TutorsUserGroup
        return session.query(UserGroup).filter(UserGroup.GroupId == id).one()

    @staticmethod
    def _get_tutors(course: CourseDB, session: Session) -> list[ZulipUser]:
        """
        Get the Tutors of a Course a list of ZulipUsers.
        """
        ug: UserGroup = Course._get_tutorgroup(course, session)
        return Usergroup.get_users_for_group(session, ug)
    
    @staticmethod
    def _get_instructorgroup(course: CourseDB, session: Session) -> UserGroup:
        """
        Get the Tutor-UserGroup of a given Course.
        """
        id: int = course.InstructorsUserGroup
        return session.query(UserGroup).filter(UserGroup.GroupId == id).one()

    @staticmethod
    def _get_instructors(course: CourseDB, session: Session) -> list[ZulipUser]:
        """
        Get the Tutors of a Course a list of ZulipUsers.
        """
        ug: UserGroup = Course._get_instructorgroup(course, session)
        return Usergroup.get_users_for_group(session, ug)

    @staticmethod
    def _get_streams(course: CourseDB, session: Session) -> list[ZulipStream]:
        """
        Get the Streams of a Course as list of ZulipStreams.
        """
        sg: StreamGroup = Course._get_streamgroup(course, session)
        return Streamgroup._get_streams(session, sg)
    
    @staticmethod
    async def _get_stream_names(session: Session, client: AsyncClient, course: CourseDB) -> list[str]:
        """
        Get the Stream Names of a Course as list of strings.
        """
        sg: StreamGroup = Course._get_streamgroup(course, session)
        return await Streamgroup._get_stream_names(session, client, [sg])
    
    @staticmethod
    def _update_streamgroup(course: CourseDB, session: Session, group:StreamGroup) -> None:
        """
        Set the StreamGroup of a given Course.
        """
        oldSG : StreamGroup = Course._get_streamgroup(course, session)
        if (oldSG == group):
            raise DMError("The given Streamgroup is already set for this course.")
        
        stmt = update(CourseDB).where(CourseDB.CourseId==course.CourseId).values(Streams=group)
        try:
            session.execute(stmt)
            session.query(StreamGroup).filter(StreamGroup.StreamGroupId==oldSG.StreamGroupId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Streamgroup :botsad:")
        


    @staticmethod
    def _update_tutorgroup(course: CourseDB, session: Session, group: UserGroup) -> None:
        """
        Set the Tutor-UserGroup of a given Course.
        """
        oldTG : UserGroup = Course._get_tutorgroup(course, session)
        if (oldTG == group):
            raise DMError("The given Usergroup is already set as Tutorgroup for this course.")
        
        stmt = update(CourseDB).where(CourseDB.CourseId==course.CourseId).values(TutorsUserGroup=group.GroupId)
        try:
            session.execute(stmt)
            session.query(UserGroup).filter(UserGroup.GroupId==oldTG.GroupId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Tutors :botsad:")
        
    @staticmethod
    def _update_instructorgroup(course: CourseDB, session: Session, group: UserGroup) -> None:
        """
        Set the Tutor-UserGroup of a given Course.
        """
        oldIG : UserGroup = Course._get_instructorgroup(course, session)
        if (oldIG == group):
            raise DMError("The given Usergroup is already set as Instructorgroup for this course.")
        
        stmt = update(CourseDB).where(CourseDB.CourseId==course.CourseId).values(InstructorsUserGroup=group)
        try:
            session.execute(stmt)
            session.query(UserGroup).filter(UserGroup.GroupId==oldIG.GroupId).delete()
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Instructors :botsad:")

    @staticmethod
    async def _update_tutorstream(course:CourseDB, session:Session, client:AsyncClient, stream: ZulipStream) -> None:
        """
        Set the Tutor-Stream of a given Course.
        """
        oldTS : ZulipStream = course.TutorStream
        if (oldTS == stream):
            raise DMError("The given Stream is already set as Tutorstream for this course.")
        
        stmt = update(CourseDB).where(CourseDB.CourseId==course.CourseId).values(TutorStream=stream)
        try:
            session.execute(stmt)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("SCould not update Tutor-Stream :botsad:")
        
        await client.delete_stream(oldTS.id)

    @staticmethod
    async def _update_instructorstream(course:CourseDB, session:Session, client:AsyncClient, stream: ZulipStream) -> None:
        """
        Set the Tutor-Stream of a given Course.
        """
        oldIS : ZulipStream = course.InstructorStream
        if (oldIS == stream):
            raise DMError("The given Stream is already set as Instructorstream for this course.")
        
        stmt = update(CourseDB).where(CourseDB.CourseId==course.CourseId).values(InstructorStream=stream)
        try:
            session.execute(stmt)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Instructor-Stream :botsad:")
        
        await client.delete_stream(oldIS.id)

