#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

from collections.abc import Iterable as IterableClass
from inspect import cleandoc
import inspect
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
        List all Courses with their associated Channels.
        """
        response: str = (
            "Course Name | Emoji | Channels \n---- | ---- | ----"
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
        description="The course has an additional Channel for Instructors.",
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

        cleanup_opterations: list[Callable] = []

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result = await self.client.send_response(Response.build_message(message, content=f"Course `{name}` already exists. Dou you want to replace it with an empty course?"))
            if result["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
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
        
            result, msg = await UserInput.confirm(self.client, result["id"],timeout=60)
            if not result:
                res = await self.client.send_response(Response.build_message(message, content=f"Do you want to use the existing Streamgroup with :{streamgroup_emoji}: for your course?"))
                if res["result"] != "success":
                    raise DMError("Could not send message to user")
                
                res, msg = await UserInput.confirm(self.client, res["id"], timeout=60)
                if not res:
                    yield DMResponse(f"Ok, I will not create a new course then. Please choose another emote because :{streamgroup_emoji}: is already in use :botsad:")
                    return
                else:
                    streams : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
            else: 
                sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
                Streamgroup._delete_group(session, sg)

        resLan = await self.client.send_response(Response.build_message(message, content=f"Is your course held in English or German?"))
        lan, _ = await UserInput.i8n_german_or_english(self.client, resLan["id"], timeout=60)

        try:
            # get a corresponding (empty) Streamgroup
            if not streams:
                streamgroup_name: str = "streams_" + name
                streams: StreamGroup = Streamgroup._create_and_get_group(
                    session, streamgroup_name, streamgroup_emoji
                )
                cleanup_opterations.append(lambda: Streamgroup._delete_group(session, streams))

            # get a corresponding (empty) Usergroup
            usergroup_name_tut: str = "tutors_" + name

            if (
                session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_tut).first()
                is not None
            ):
                result = await self.client.send_response(Response.build_message(message, content=f"Usergroup for Tutors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?"))
                if result["result"] != "success":
                    raise DMError("Could not send message to user")
            
                result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                if not result:
                    raise DMError("Ok, I will not create a new course then. Please choose another name.")
                else:
                    ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_tut).first()
                    Usergroup.delete_group(session, ug)
                
            tutors = Usergroup.create_and_get_group(session, usergroup_name_tut)
            cleanup_opterations.append(lambda: Usergroup.delete_group(session, tutors))

            # get a corresponding (empty) Usergroup
            usergroup_name_ins: str = "instructors_" + name

            if (
                session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_ins).first()
                is not None
            ):
                result = await self.client.send_response(Response.build_message(message, content=f"Usergroup for Instructors of this course already exists. Dou you want to replace it with a new empty Usergroup for your course?"))
                if result["result"] != "success":
                    raise DMError("Could not send message to user")
            
                result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                if not result:
                    raise DMError("Ok, I will not create a new course then. Please choose another name.")
                else:
                    ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name_ins).first()
                    Usergroup.delete_group(session, ug)
        
            instructors = Usergroup.create_and_get_group(session, usergroup_name_ins)
            cleanup_opterations.append(lambda: Usergroup.delete_group(session, instructors))

            # get a corresponding (empty) Channel for Tutors
            tutors_stream_name : str = name + " - Tutors"
            tutors_stream_desc : list[str] = [f"Internal Channel for {name}-Tutors"]
            if lan == "de":
                tutors_stream_name: str = name + " - Tutoren"
                tutors_stream_desc: list[str] = [f"Interner Kanal für {name}-Tutoren"]

            tut_ex = await self.client.get_stream_id_by_name(tutors_stream_name)

            if tut_ex is not None:
                result = await self.client.send_response(Response.build_message(message, content=f"Channel for Tutors of this course already exists. Dou you want to replace it with a new empty Channel for your course?"))
                if result["result"] != "success":
                    raise DMError("Could not send message to user")
            
                result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                if not result:
                    raise DMError("Ok, I will not create a new course then. Please choose another name.")
                else:
                    await self.client.delete_stream(tut_ex)

            result: dict[str, Any] = await self.client.add_subscriptions(
                streams=[{"name": tutors_stream_name, "description": " ".join(tutors_stream_desc)}],
                principals=[sender.id, self.client.id],
                invite_only=True,
                history_public_to_subscribers=True,
            )


            if result["result"] != "success":
                raise DMError(result["msg"])
            
            
            tutors_stream: ZulipStream = ZulipStream(f"#**{tutors_stream_name}**")
            await tutors_stream

            cleanup_opterations.append(lambda: self.client.delete_stream(tutors_stream.id)) 

            # get a corresponding (empty) Channel for Instructors or None
            instructor_stream: ZulipStream | None = None
            if opts.i:

                instructor_stream_name: str = name + " - Instructors"
                instructor_stream_desc: list[str] = [f"Internal Channel for Instructors of {name}"]
                if lan == "de":
                    instructor_stream_name: str = name + " - Instructors"
                    instructor_stream_desc: list[str] = [f"Interner Kanal für {name}-Instructors"]

                ins_ex = await self.client.get_stream_id_by_name(instructor_stream_name)

                if ins_ex is not None:
                    result = await self.client.send_response(Response.build_message(message, content=f"Channel for Instructors of this course already exists. Dou you want to replace it with a new empty Channel for your course?"))
                    if result["result"] != "success":
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                    if not result:
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        await self.client.delete_stream(ins_ex)

                result: dict[str, Any] = await self.client.add_subscriptions(
                    streams=[{"name": instructor_stream_name, "description": " ".join(instructor_stream_desc)}],
                    principals=[sender.id, self.client.id],
                    invite_only=True,
                    history_public_to_subscribers=True,
                )

                if result["result"] != "success":
                    raise DMError(result["msg"])

                
                instructor_stream: ZulipStream = ZulipStream(f"#**{instructor_stream_name}**")
                await instructor_stream
                
                cleanup_opterations.append(lambda: self.client.delete_stream(instructor_stream.id))
 
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

        except Exception as e:
            session.rollback()
            for cleanup in cleanup_opterations:
                if inspect.iscoroutinefunction(cleanup):
                    await cleanup()
                else:
                    cleanup()
            if isinstance(e, DMError):
                raise e
            
            raise DMError(f"Something went wrong when creating the course `{name}` :botsweat:") from e

        yield DMResponse(f"Course `{name}` created :bothappypad:")

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the Course")
    @arg("emoji", Regex.get_emoji_name, description="The emoji to use for the Streamgroup.")
    @opt(
        "c",
        long_opt="channelgroup",
        type=StreamGroup.StreamGroupId,
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
        type=ZulipStream,
        description="The course has an additional Channel for tutors.",
    )
    @opt(
        "ins",
        long_opt="instructor_channel",
        type=ZulipStream,
        description="The course has an additional Channel for Instructors.",
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

        cleanup_opterations: list[Callable] = []

        if (
            session.query(CourseDB).filter(CourseDB.CourseName == name).first()
            is not None
        ):
            result = await self.client.send_response(Response.build_message(message, content=f"Course `{name}` already exists. Dou you want to replace it with the new course?"))
            if result["result"] != "success":
                raise DMError("Could not send message to user")
        
            result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
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
        
            result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
            if not result:
                res = await self.client.send_response(Response.build_message(message, content=f"Do you want to use the existing Streamgroup with :{streamgroup_emoji}: for your course?"))
                if res["result"] != "success":
                    raise DMError("Could not send message to user")
                
                res, msg = await UserInput.confirm(self.client, res["id"], timeout=60)
                if not res:
                    yield DMResponse(f"Ok, I will not create a new course then. Please choose another emote because :{streamgroup_emoji}: is already in use :botsad:")
                    return
                else:
                    streams : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
            else: 
                sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==streamgroup_emoji).first()
                Streamgroup._delete_group(session, sg)

        resLan = await self.client.send_response(Response.build_message(message, content=f"Is your course held in English or German?"))
        lan, _ = await UserInput.i8n_german_or_english(self.client, resLan["id"], timeout=60)

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
                    cleanup_opterations.append(lambda: Streamgroup._delete_group(session, streams))

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
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                    if not result:
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name).first()
                        Usergroup.delete_group(session, ug)
                    
                tutors = Usergroup.create_and_get_group(session, usergroup_name)

                cleanup_opterations.append(lambda: Usergroup.delete_group(session, tutors))

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
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                    if not result:
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName == usergroup_name).first()
                        Usergroup.delete_group(session, ug)
            
                instructors = Usergroup.create_and_get_group(session, usergroup_name)

                cleanup_opterations.append(lambda: Usergroup.delete_group(session, instructors))

            # get corresponding Channel for Tutors
            tutors_stream: ZulipStream
            if opts.tuts:
                tutors_stream = opts.tuts
            else:
                tutors_stream_name: str = name + " - Tutors"
                tutors_stream_desc: list[str] = [f"Internal Channel for {name}-Tutors"]
                if lan == "de":
                    tutors_stream_name: str = name + " - Tutoren"
                    tutors_stream_desc: list[str] = [f"Interner Kanal für {name}-Tutoren"]
               
                tut_ex = await self.client.get_stream_id_by_name(tutors_stream_name)

                if tut_ex is not None:
                    result = await self.client.send_response(Response.build_message(message, content=f"Channel for Tutors of this course already exists. Dou you want to replace it with a new empty Channel for your course?"))
                    if result["result"] != "success":
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                    if not result:
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        await self.client.delete_stream(tut_ex)


                tutor_ids = Usergroup.get_user_ids_for_group(session,tutors)
                tutor_ids.append(sender.id)
                tutor_ids.append(self.client.id)

                result: dict[str, Any] = await self.client.add_subscriptions(
                    streams=[{"name": tutors_stream_name, "description": " ".join(tutors_stream_desc)}],
                    principals=tutor_ids,
                    invite_only=True,
                    history_public_to_subscribers=True,
                )
                if result["result"] != "success":
                    raise DMError(result["msg"])
              
                tutors_stream = ZulipStream(f"#**{tutors_stream_name}**")
                await tutors_stream

                cleanup_opterations.append(lambda: self.client.delete_stream(tutors_stream.id))


            # get a corresponding Channel for Instructors or None
            instructor_stream: ZulipStream | None = None
            if opts.ins:
                instructor_stream = opts.ins
            else:
                instructor_stream_name: str = name + " - Instructors"
                instructor_stream_desc: list[str] = [f"Internal Channel for Instructors of {name}"]
                if lan == "de":
                    instructor_stream_name: str = name + " - Instructors"
                    instructor_stream_desc: list[str] = [f"Interner Kanal für {name}-Instructors"]

                ins_ex = await self.client.get_stream_id_by_name(instructor_stream_name)

                if ins_ex is not None:
                    result = await self.client.send_response(Response.build_message(message, content=f"Channel for Instructors of this course already exists. Dou you want to replace it with a new empty Channel for your course?"))
                    if result["result"] != "success":
                        raise DMError("Could not send message to user")
                
                    result, msg = await UserInput.confirm(self.client, result["id"], timeout=60)
                    if not result:
                        raise DMError("Ok, I will not create a new course then. Please choose another name.")
                    else:
                        await self.client.delete_stream(ins_ex)

                instructor_ids = Usergroup.get_user_ids_for_group(session,instructors)
                instructor_ids.append(sender.id)
                instructor_ids.append(self.client.id)

                result: dict[str, Any] = await self.client.add_subscriptions(
                    streams=[{"name": instructor_stream_name, "description": " ".join(instructor_stream_desc)}],
                    principals=instructor_ids,
                    invite_only=True,
                    history_public_to_subscribers=True,
                )
                if result["result"] != "success":
                    raise DMError(result["msg"])
                
                instructor_stream = ZulipStream(f"#**{instructor_stream_name}**")
                await instructor_stream

                cleanup_opterations.append(lambda: self.client.delete_stream(instructor_stream.id))

 
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

        except Exception as e:
            session.rollback()
            for cleanup in cleanup_opterations:
                if inspect.iscoroutinefunction(cleanup):
                    await cleanup()
                else:
                    cleanup()
            if isinstance(e, DMError):
                raise e
            
            raise DMError(f"Something went wrong when creating the course `{name}` :botsweat:") from e

        yield DMResponse(f"Course `{name}` created :bothappypad:")


    @command
    @privilege(Privilege.ADMIN)
    @arg("course", type=CourseDB.CourseName,description="The name of the Course to add the Channels to.")
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
        description="Add a Channel for Organization."
    )
    @opt(
        "f",
        long_opt="feedback",
        description="Add a Channel for Feedback."
    )
    @opt(
        "n",
        long_opt="announcements",
        description="Add a Channel for Announcements."
    )
    @opt(
        "m",
        long_opt="memes",
        description="Add a Channel for Memes."
    )
    @opt(
        "t",
        long_opt="tech",
        description="Add a Channel for Tech-Support."
    )
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
        lan : str = course.CourseLanguage
        streams_id: str = course.Streams
        stremgroup: StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupId==streams_id).first()

        if opts.a:
            await Course.add_standard_streams(self.client, session, course.CourseName, stremgroup, lan)
            
        else:
            await Course.add_standard_streams(self.client, session, course.CourseName, stremgroup, lan, opts.g, opts.o, opts.f, opts.n, opts.m, opts.t)

        yield DMResponse(f"Standard Channels added to Course `{course.CourseName}`.")
        

    @command
    @privilege(Privilege.ADMIN)
    @arg("course", type=CourseDB.CourseName,description="The name of the Course to delete.")
    @opt(
        "a",
        long_opt="all",
        description="Delete the whole course (Streamgroup, Usergroups, Channels).",
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
        long_opt="tutorial_stream",
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
        "c",
        long_opt="channelgroup",
        type=StreamGroup.StreamGroupId,
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
        long_opt="tutorial_stream",
        type=ZulipStream,
        description="The name of an existing Channel for Instructors.",
    )
    @opt(
        "ins",
        long_opt="instructor_channel",
        type=ZulipStream,
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
        Clear a course (Channels/Tutors), but keep the underlying components (Streamgroup/UserGroup).
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

        cleanup_opterations: list[Callable] = []

        await self.client.send_response(Response.build_message(message, content="Welcome to the Course Creation Wizard :bothappypad:"))

        await self.client.send_response(Response.build_message(message, content="Let's start by choosing a name for your new course :bothappy:"))


        try:
            while True:
                prompt = await self.client.send_response(Response.build_message(message, content="What is the name of the course?"))
                if prompt["result"] != "success":
                    raise DMError("Could not send message to user")
            
                result, _ = await UserInput.short_text_response(prompt["id"], timeout=60)
                if result is None:
                    await self.client.send_response(Response.build_message(message, content="Please provide a name for the course."))
                else: 
                    c : CourseDB = session.query(CourseDB).filter(CourseDB.CourseName==result).one_or_none()
                    sgName : str = "streams_" + result
                    sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupId==sgName).one_or_none()
                    if c is None and sg is None:
                        courseName = result
                        break
                    else:
                        await self.client.send_response(Response.build_message(message, content=f"A Course or Streamgroup with the name `{result}` already exists. Please choose another name."))



            promptLan = await self.client.send_response(Response.build_message(message, content=f"Amazing :bothappypad:\nIs your course held in English or German?"))
            courseLan, _ = await UserInput.i8n_german_or_english(self.client, promptLan["id"], timeout=60)

            # TODO: Create streamgroup
            await self.client.send_response(Response.build_message(message, content="Now we will add a Streamgroup for your course. \nWe can do this by using an already existing Streamgroup, creating a new Streamgroup with already existing Channels or creating a completely new empty Streamgroup.\n In every case, you can update the Streamgroup later."))


            prompt1 = await self.client.send_response(Response.build_message(message, content="Do you want to use an existing Streamgroup for your course?"))
            if prompt1["result"] != "success":
                    raise DMError("Could not send message to user")
            
            result1, _ = await UserInput.confirm(self.client, prompt1["id"],timeout=60)
            if result1:
                # use existing streamgroup
                while True:
                    prompt1a = await self.client.send_response(Response.build_message(message, content="Please provide the name of the Streamgroup."))
                    if prompt1a["result"] != "success":
                            raise DMError("Could not send message to user")
                    
                    result1a, _ = await UserInput.short_text_response(prompt1a["id"], timeout=60)
                    if result1a is None:
                        continue

                    sg : StreamGroup | None = session.query(StreamGroup).filter(StreamGroup.StreamGroupId==result1a).one_or_none()
                    if sg is None:
                        await self.client.send_response(Response.build_message(message, content=f"A Streamgroup with the name `{result1a}` does not exist."))
                        continue

                    courseStreams = sg
                    break      
            else:
                await self.client.send_response(Response.build_message(message, content="Great, so let's create a new Streamgroup by choosing an emoji for the course :bothappy:"))


                while True:
                    prompt2a = await self.client.send_response(Response.build_message(message, content="What is the emoji representing the course?"))
                    if prompt2a["result"] != "success":
                        raise DMError("Could not send message to user")
                
                    result2a, _ = await UserInput.short_text_response(prompt2a["id"], timeout=60)
                    
                    if result2a is None:
                        continue
                    else: 
                        emote = Regex.get_emoji_name(result2a)
                        if emote is None:
                            await self.client.send_response(Response.build_message(message, content="Please provide a valid emoji."))
                            continue

                        sg : StreamGroup = session.query(StreamGroup).filter(StreamGroup.StreamGroupEmote==emote).one_or_none()
                        if sg is None:
                            courseEmoji = emote
                            break
                        else:
                            self.client.send_response(Response.build_message(message, content=f"A course with the emote :{emote}: already exists. Please choose another emoji."))

                prompt2b = await self.client.send_response(Response.build_message(message, content=f"Do you want to create the Streamgroup {courseEmoji} from a list of Stream-Name-Regexes?"))
                if prompt2b["result"] != "success":
                        raise DMError("Could not send message to user")
                
                result2b, _ = await UserInput.confirm(self.client, prompt2b["id"], timeout=60)
                if result2b:
                    # enter list of names
                    while True:
                        prompt2c = await self.client.send_response(Response.build_message(message, content=f"Please enter a list of Channels in the format 'StreamNameRegex1,StreamNameRegex2, ..., StreamNameRegexN'"))
                        if prompt2c["result"] != "success":
                            raise DMError("Could not send message to user")
                        
                        result2c, _ = await UserInput.short_text_response(prompt2c["id"], timeout=60)
                        if result2c is None:
                            continue
                        else: 
                            # parse Channel Names
                            streamgroup_name: str = "streams_" + courseName
                            courseStreams: StreamGroup = Streamgroup._create_and_get_group(session, streamgroup_name, courseEmoji)
                            cleanup_opterations.append(lambda: Streamgroup._delete_group(session, courseStreams))

                            streams = result2c.split(",")
                            await Streamgroup.add_streams(self.client, session, sender, courseStreams, streams)
                            break
                    
                else:
                   # create empty Streamgroup
                    streamgroup_name: str = "streams_" + courseName
                    courseStreams: StreamGroup = Streamgroup._create_and_get_group(session, streamgroup_name, courseEmoji)
                    cleanup_opterations.append(lambda: Streamgroup._delete_group(session, courseStreams))


            # add default Streams
            await self.client.send_response(Response.build_message(message, content="Now you can add the default Channels to your course."))


            promptG = await self.client.send_response(Response.build_message(message, content="Do you want to add a general Channel to your course?"))
            if promptG["result"] != "success":
                raise DMError("Could not send message to user")
            
            resultG, _ = await UserInput.confirm(self.client, promptG["id"], timeout=60)

            promptO = await self.client.send_response(Response.build_message(message, content="Do you want to add a Organization-Channel to your course?"))
            if promptO["result"] != "success":
                raise DMError("Could not send message to user")
            
            resultO, _ = await UserInput.confirm(self.client, promptO["id"], timeout=60)


            promptM = await self.client.send_response(Response.build_message(message, content="Do you want to add a Memes-Channel to your course?"))
            if promptM["result"] != "success":
                raise DMError("Could not send message to user")
            
            resultM, _ = await UserInput.confirm(self.client, promptM["id"], timeout=60)


            promptT = await self.client.send_response(Response.build_message(message, content="Do you want to add a Channel for Tech-Support to your course?"))
            if promptT["result"] != "success":
                raise DMError("Could not send message to user")
            
            resultT, _ = await UserInput.confirm(self.client, promptT["id"], timeout=60)

            # wizard adds Feedback and Announcement per default to improve the communication between instructors and students (they have to be removed manually) 
            await Course.add_standard_streams(self.client, session, courseName, courseStreams, courseLan, resultG, resultO, True, True, resultM, resultT)
            


            # TODO create Usergroup for Tutors
            await self.client.send_response(Response.build_message(message, content="Now we will add a Usergroup for the Tutors of your course. \nWe can do this by using an already existing Usergroup, creating a new Usergroup with certain people or creating a completely new empty Usergroup.\n In every case, you can update the Usergroup later."))


            prompt1t = await self.client.send_response(Response.build_message(message, content="Do you want to use an existing Usergroup for your course?"))
            if prompt1t["result"] != "success":
                    raise DMError("Could not send message to user")
            
            result1t, _ = await UserInput.confirm(self.client, prompt1t["id"], timeout=60)
            if result1t:
                # use existing usergroup
                while True:
                    prompt1at = await  self.client.send_response(Response.build_message(message, content="Please provide the name of the Usergroup."))
                    if prompt1at["result"] != "success":
                            raise DMError("Could not send message to user")
                    
                    result1at, _ = await UserInput.short_text_response(prompt1at["id"], timeout=60)
                    if result1at is None:
                        continue

                    ug : UserGroup | None = session.query(UserGroup).filter(UserGroup.GroupName==result1at).one_or_none()
                    if ug is None:
                        await self.client.send_response(Response.build_message(message, content=f"A Usergroup with the name `{result1at}` does not exist."))

                        continue

                    courseTutors = ug
                    break      
            else:
                await self.client.send_response(Response.build_message(message, content="Great, so let's create a new Usergroup for your Tutors"))


                while True:
                    usergroup_name_tut: str = "tutors_" + courseName + "_1"
                    vers = 1
                    
                    ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName==usergroup_name_tut).one_or_none()
                    if ug is None:
                        courseTutors = Usergroup.create_and_get_group(session, usergroup_name_tut)
                        cleanup_opterations.append(lambda: Usergroup.delete_group(session, courseTutors))
                        break
                    else:
                        vers += 1
                        usergroup_name_tut = usergroup_name_tut[:-1] + vers

                prompt2bt = await self.client.send_response(Response.build_message(message, content=f"Do you want to create the Usergroup from a list of Tutor-Names?"))
                if prompt2bt["result"] != "success":
                        raise DMError("Could not send message to user")
                
                result2bt, _ = await UserInput.confirm(self.client, prompt2bt["id"], timeout=60)
                if result2bt:
                    # enter list of names
                    while True:
                        prompt2ct = await self.client.send_response(Response.build_message(message, content=f"Please enter a list of Names in the format 'Name1,Name2, ..., NameN'"))
                        if prompt2ct["result"] != "success":
                            raise DMError("Could not send message to user")
                        
                        result2ct, _ = await UserInput.short_text_response(prompt2ct["id"], timeout=60)
                        if result2ct is None:
                            continue
                        else: 
                            # parse Channel Names
                            names = result2ct.split(",")

                            for name in names:
                                real_name = Regex.get_user_name(name)
                                if real_name is None:
                                    await self.client.send_response(Response.build_message(message, content=f"Could not find a user with the name {name}."))

                                    continue
                                try:
                                    user = ZulipUser(real_name)
                                    await user
                                    Usergroup.add_user_to_group(session, user, courseTutors)
                                except Exception:
                                    await self.client.send_response(Response.build_message(message, content=f"Could not add a user with the name {name}."))
                                    continue
                            break





            # TODO create Usergroup for Instructors
            await self.client.send_response(Response.build_message(message, content="Now we will add a Usergroup for the Instructors of your course. \nSame procedure as for the Tutors."))


            prompt1i = await self.client.send_response(Response.build_message(message, content="Do you want to use an existing Usergroup for your course?"))
            if prompt1i["result"] != "success":
                    raise DMError("Could not send message to user")
            
            result1i, _ = await UserInput.confirm(self.client, prompt1i["id"], timeout=60)
            if result1i:
                # use existing usergroup
                while True:
                    prompt1ai = await self.client.send_response(Response.build_message(message, content="Please provide the name of the Usergroup."))
                    if prompt1ai["result"] != "success":
                            raise DMError("Could not send message to user")
                    
                    result1ai, _ = await UserInput.short_text_response(prompt1ai["id"], timeout=60)
                    if result1ai is None:
                        continue

                    ug : UserGroup | None = session.query(UserGroup).filter(UserGroup.GroupName==result1ai).one_or_none()
                    if ug is None:
                        await self.client.send_response(Response.build_message(message, content=f"A Usergroup with the name `{result1i}` does not exist."))

                        continue

                    courseInstructors = ug
                    break      
            else:

                await self.client.send_response(Response.build_message(message, content="Great, so let's create a new Usergroup for your Instructors."))


                while True:
                    usergroup_name_ins: str = "instructors_" + courseName + "_1"
                    vers = 1
                    
                    ug : UserGroup = session.query(UserGroup).filter(UserGroup.GroupName==usergroup_name_ins).one_or_none()
                    if ug is None:
                        courseInstructors = Usergroup.create_and_get_group(session, usergroup_name_ins)
                        cleanup_opterations.append(lambda: Usergroup.delete_group(session, courseInstructors))
                        break
                    else:
                        vers += 1
                        usergroup_name_ins = usergroup_name_ins[:-1] + vers

                prompt2bi = await self.client.send_response(Response.build_message(message, content=f"Do you want to create the Usergroup from a list of Instructor-Names?"))
                if prompt2bi["result"] != "success":
                        raise DMError("Could not send message to user")
                
                result2bi, _ = await UserInput.confirm(self.client, prompt2bi["id"], timeout=60)
                if result2bi:
                    # enter list of names
                    while True:
                        prompt2ci = await  self.client.send_response(Response.build_message(message, content=f"Please enter a list of Names in the format 'Name1,Name2, ..., NameN'"))
                        if prompt2ci["result"] != "success":
                            raise DMError("Could not send message to user")
                        
                        result2ci, _ = await UserInput.short_text_response(prompt2ci["id"], timeout=60)
                        if result2ci is None:
                            continue
                        else: 
                            # parse Channel Names
                            names = result2ci.split(",")

                            for name in names:
                                real_name = Regex.get_user_name(name)
                                if real_name is None:
                                    await self.client.send_response(Response.build_message(message, content=f"Could not find a user with the name {name}."))

                                    continue
                                try:
                                    user = ZulipUser(real_name)
                                    await user
                                    Usergroup.add_user_to_group(session, user, courseTutors)
                                except Exception:
                                    await self.client.send_response(Response.build_message(message, content=f"Could not add a user with the name {name}."))

                                    continue
                            break

            Usergroup.add_user_to_group(session, sender, courseInstructors)

            # get a corresponding Channel for Tutors
            tutors_stream_name : str = courseName + " - Tutors"
            tutors_stream_desc : list[str] = [f"Internal Channel for {courseName}-Tutors"]
            if courseLan == "de":
                tutors_stream_name: str = courseName + " - Tutoren"
                tutors_stream_desc: list[str] = [f"Interner Kanal für {courseName}-Tutoren"]

            
            tut_ex = await self.client.get_stream_id_by_name(tutors_stream_name)

            if tut_ex is not None:
                resultts = await self.client.send_response(Response.build_message(message, content=f"Channel for Tutors of this course already exists. Dou you want to replace it with a new Channel for your course?"))
                if resultts["result"] != "success":
                    raise DMError("Could not send message to user")
            
                resultts, _ = await UserInput.confirm(self.client, resultts["id"], timeout=60)
                if not result:
                    while True:
                        promptts = await self.client.send_response(Response.build_message(message, content="Please choose another name for the Tutor-Stream."))
                        if promptts["result"] != "success":
                            raise DMError("Could not send message to user")
                        
                        res, _ = await UserInput.short_text_response(promptts["id"], timeout=60)
                        if res is None:
                            await self.client.send_response(Response.build_message(message, content="Please provide a name for the Stream."))
                        else: 
                            tut_ex = await self.client.get_stream_id_by_name(res)
                            if tut_ex is None:
                                tutors_stream_name = res
                                break
                            else:
                                await self.client.send_response(Response.build_message(message, content=f"A Channel with the name {res} already exists."))

                else:
                    await self.client.delete_stream(tut_ex)

            tutor_ids = Usergroup.get_user_ids_for_group(session,courseTutors)
            tutor_ids.append(sender.id)
            tutor_ids.append(self.client.id)

            result: dict[str, Any] = await self.client.add_subscriptions(
                streams=[{"name": tutors_stream_name, "description": " ".join(tutors_stream_desc)}],
                principals=tutor_ids,
                invite_only=True,
                history_public_to_subscribers=True,
            )

            if result["result"] != "success":
                raise DMError(result["msg"])
            
            courseTutorStream: ZulipStream = ZulipStream(f"#**{tutors_stream_name}**")
            await courseTutorStream

            cleanup_opterations.append(lambda: self.client.delete_stream(courseTutorStream.id))

            # get a corresponding Channel for Instructors or None
            promptis = await self.client.send_response(Response.build_message(message, content="Do you want a Instructor-Channel for your course?"))
            if promptis["result"] != "success":
                raise DMError("Could not send message to user")
        
            resultis, _ = await UserInput.confirm(self.client, promptis["id"], timeout=60)
            if resultis is not None:

                instructor_stream_name: str = courseName + " - Instructors"
                instructor_stream_desc: list[str] = [f"Internal Channel for Instructors of {courseName}"]
                if courseLan == "de":
                    instructor_stream_name: str = courseName + " - Instructors"
                    instructor_stream_desc: list[str] = [f"Interner Kanal für {courseName}-Instructors"]

                ins_ex = await self.client.get_stream_id_by_name(instructor_stream_name)

                if ins_ex is not None:
                    result = await self.client.send_response(Response.build_message(message, content=f"Channel for Instructor of this course already exists. Dou you want to replace it with a new Channel for your course?"))
                    if result["result"] != "success":
                        raise DMError("Could not send message to user")
                
                    result, _ = await UserInput.confirm(self.client, result["id"], timeout=60)
                    if not result:
                        while True:
                            prompt = await self.client.send_response(Response.build_message(message, content="Please choose another name for the Instructor-Stream."))
                            if prompt["result"] != "success":
                                raise DMError("Could not send message to user")
                            
                            res, _ = await UserInput.short_text_response(prompt["id"], timeout=60)
                            if res is None:
                                await self.client.send_response(Response.build_message(message, content="Please provide a name for the Stream."))

                            else: 
                                ins_ex = await self.client.get_stream_id_by_name(res)
                                if ins_ex is None:
                                    instructor_stream_name = res
                                    break
                                else:
                                    await self.client.send_response(Response.build_message(message, content=f"A Channel with the name {res} already exists."))

                    else:
                        await self.client.delete_stream(tut_ex)

                instructor_ids = Usergroup.get_user_ids_for_group(session,courseInstructors)
                instructor_ids.append(sender.id)
                instructor_ids.append(self.client.id)

                result: dict[str, Any] = await self.client.add_subscriptions(
                    streams=[{"name": instructor_stream_name, "description": " ".join(instructor_stream_desc)}],
                    principals=instructor_ids,
                    invite_only=True,
                    history_public_to_subscribers=True,
                )
                if result["result"] != "success":
                    raise DMError(result["msg"])
                
                courseInstructorStream: ZulipStream = ZulipStream(f"#**{instructor_stream_name}**")
                await courseInstructorStream

                cleanup_opterations.append(lambda: self.client.delete_stream(courseInstructorStream.id))
            
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

        except Exception as e:
            session.rollback()
            for cleanup in cleanup_opterations:
                if inspect.iscoroutinefunction(cleanup):
                    await cleanup()
                else:
                    cleanup()
            if isinstance(e, DMError):
                raise e
            
            if courseName is None:  
                raise DMError(f"Something went wrong when creating your course :botsweat:") from e
            else:
                raise DMError(f"Something went wrong when creating the course `{courseName}` :botsweat:") from e

        yield DMResponse(f"Course `{courseName}` created :bothappypad:")


    # ========================================================================================================================
    #       CLASS METHODS
    # ========================================================================================================================
    @staticmethod
    async def add_standard_streams(client:AsyncClient, session:Session, name:str, sg:StreamGroup, lan:str
                                   , g: bool = True, o: bool = True, f: bool = True, n: bool = True, m: bool = True, t: bool = True):
        
        allg_stream = None
        org_stream = None
        fb_stream = None
        ank_stream = None
        tech_stream = None
        memes_stream = None

        cleanup_opterations: list[Callable] = []

        try:
            if g:
                allg_name: str = name + " - General"
                allg_desc: list[str] = [f"Welcome to the Channel for general info to the course {name}"]
                if lan == "de":
                    allg_name: str = name + " - Allgemein"
                    allg_desc: list[str] = [f"Willkommen im allgemeinen Zulip Kanal von dem Kurs {name}"]

                ex = await client.get_stream_id_by_name(allg_name)

                if ex is None:
                        result: dict[str, Any] = await client.add_subscriptions(
                            streams=[{"name": allg_name, "description": " ".join(allg_desc)}]
                        )
                        if result["result"] != "success":
                            raise DMError(result["msg"])

                allg_stream: ZulipStream = ZulipStream(f"#**{allg_name}**")
                await allg_stream

                cleanup_opterations.append(lambda: client.delete_stream(allg_stream.id))

            if o:
                org_name: str = name + " - Organization"
                org_desc: list[str] = [f"Welcome to the organizational Channel of the course {name}"]
                if lan == "de":
                    org_name: str = name + " - Organisation"
                    org_desc: list[str] = [f"Willkommen im Orga-Zulip Kanal von dem Kurs {name}"]

                ex = await client.get_stream_id_by_name(org_name)

                if ex is None:
                        result: dict[str, Any] = await client.add_subscriptions(
                            streams=[{"name": org_name, "description": " ".join(org_desc)}]
                        )
                        if result["result"] != "success":
                            raise DMError(result["msg"])
                        
                org_stream: ZulipStream = ZulipStream(f"#**{org_name}**")
                await org_stream

                cleanup_opterations.append(lambda: client.delete_stream(org_stream.id))

            if f:
                fb_name: str = name + " - Feedback"
                fb_desc: list[str] = [f"Welcome to the Channel for Feedback to the course {name}"]
                if lan == "de":
                    fb_name: str = name + " - Feedback"
                    fb_desc: list[str] = [f"Willkommen im Feedback Zulip Kanal von dem Kurs {name}"]
                

                ex = await client.get_stream_id_by_name(fb_name)

                if ex is None:
                        result: dict[str, Any] = await client.add_subscriptions(
                            streams=[{"name": fb_name, "description": " ".join(fb_desc)}]
                        )
                        if result["result"] != "success":
                            raise DMError(result["msg"])

                fb_stream: ZulipStream = ZulipStream(f"#**{fb_name}**")
                await fb_stream

                cleanup_opterations.append(lambda: client.delete_stream(fb_stream.id))

            if n:
                ank_name: str = name + " - Announcements"
                ank_desc: list[str] = [f"Welcome to the Channel for Announcements for the course {name}"]
                if lan == "de":
                    ank_name: str = name + " - Ankündigungen"
                    ank_desc: list[str] = [f"Willkommen im Zulip Kanal für Ankündigungen von dem Kurs {name}"]
                
                ex = await client.get_stream_id_by_name(ank_name)

                if ex is None:
                        result: dict[str, Any] = await client.add_subscriptions(
                            streams=[{"name": ank_name, "description": " ".join(ank_desc)}]
                        )
                        if result["result"] != "success":
                            raise DMError(result["msg"])

                ank_stream: ZulipStream = ZulipStream(f"#**{ank_name}**")
                await ank_stream

                cleanup_opterations.append(lambda: client.delete_stream(ank_stream.id))

            if t:
                tech_name: str = name + " - TechSupport"
                tech_desc: list[str] = [f"Welcome to the Channel for Tech-Support in the course {name}"]
                if lan == "de":
                    tech_name: str = name + " - Technik"
                    tech_desc: list[str] = [f"Willkommen im Technik Zulip Kanal von dem Kurs {name}"]
            
                ex = await client.get_stream_id_by_name(tech_name)

                if ex is None:
                        result: dict[str, Any] = await client.add_subscriptions(
                            streams=[{"name": tech_name, "description": " ".join(tech_desc)}]
                        )
                        if result["result"] != "success":
                            raise DMError(result["msg"])

                tech_stream: ZulipStream = ZulipStream(f"#**{tech_name}**")
                await tech_stream

                cleanup_opterations.append(lambda: client.delete_stream(tech_stream.id))

            if m:
                memes_name: str = name + " - Memes"
                memes_desc: list[str] = [f"Welcome to the Channel for top-quality Memes of the course {name}"]
                if lan == "de":
                    memes_name: str = name + " - Memes"
                    memes_desc: list[str] = [f"Willkommen im Meme Zulip Kanal von dem Kurs {name}"]
                
                ex = await client.get_stream_id_by_name(memes_name)

                if ex is None:
                        result: dict[str, Any] = await client.add_subscriptions(
                            streams=[{"name": memes_name, "description": " ".join(memes_desc)}]
                        )
                        if result["result"] != "success":
                            raise DMError(result["msg"])

                memes_stream: ZulipStream = ZulipStream(f"#**{memes_name}**")
                await memes_stream

                cleanup_opterations.append(lambda: client.delete_stream(memes_stream.id))

            to_add = [ s for s in [allg_stream, org_stream, fb_stream, ank_stream, tech_stream, memes_stream] if s is not None ]

            Streamgroup._add_zulip_streams(session,
                                        to_add,
                                        sg)
        
        except Exception as e:
            for cleanup in cleanup_opterations:
                    await cleanup()
            raise DMError(f"Something went wrong when creating the default channels :botsad:") from e
        



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
        Get the Channels of a Course as list of ZulipStreams.
        """
        sg: StreamGroup = Course._get_streamgroup(course, session)
        return Streamgroup._get_streams(session, sg)
    
    @staticmethod
    async def _get_stream_names(session: Session, client: AsyncClient, course: CourseDB) -> list[str]:
        """
        Get the Channel Names of a Course as list of strings.
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
        Set the Tutor-Channel of a given Course.
        """
        oldTS : ZulipStream = course.TutorStream
        if (oldTS == stream):
            raise DMError("The given Channel is already set as Tutor-Channel for this course.")
        
        stmt = update(CourseDB).where(CourseDB.CourseId==course.CourseId).values(TutorStream=stream)
        try:
            session.execute(stmt)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Tutor-Channel :botsad:")
        
        await client.delete_stream(oldTS.id)

    @staticmethod
    async def _update_instructorstream(course:CourseDB, session:Session, client:AsyncClient, stream: ZulipStream) -> None:
        """
        Set the Instructor-Channel of a given Course.
        """
        oldIS : ZulipStream = course.InstructorStream
        if (oldIS == stream):
            raise DMError("The given Channel is already set as Instructor-Channel for this course.")
        
        stmt = update(CourseDB).where(CourseDB.CourseId==course.CourseId).values(InstructorStream=stream)
        try:
            session.execute(stmt)
            session.commit()
        except sqlalchemy.exc.IntegrityError as e:
            session.rollback()
            raise DMError("Could not update Instructor-Channel :botsad:")
        
        await client.delete_stream(oldIS.id)

