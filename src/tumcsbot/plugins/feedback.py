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
from tumcsbot.plugins.course import CourseDB, Course
from tumcsbot.plugins.streams import Streams
from tumcsbot.lib.types import (
    DMError,
    DMResponse,
    Privilege,
    response_type,
    ZulipUser,
    ZulipStream,
)

class Feedback(PluginCommandMixin, Plugin):
    """
    Give anonymous feedback on courses
    """

    @command
    @privilege(Privilege.USER)
    async def send(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Write an anonymous message in the Feedback-Channel of a specified course
        """

        course : Course | None  = None
        
        prompt1 = await self.client.send_response(Response.build_message(message, content="What is the name of the course you want to give Feedback to?"))
        if prompt1["result"] != "success":
            raise DMError("Could not send message to user")

        result1, _ = await UserInput.short_text_response(self.client, prompt1["id"], timeout=60)
        if result1 is None:
            raise DMError("No response from user")
        else:
            course = Course._get_course_by_name(result1, session)

        
        if not course.FeedbackStream:
            raise DMError(
                f"Uuups, it looks like the course `{course.name}` does not support anonymous feedback :botsad:"
            )
        
        await course.FeedbackStream

    
        # TODO: @Janez Rotman
        max_topic_length = 60
        topic : str = f"New Feedback" 


        prompt2 = await self.client.send_response(Response.build_message(message, content=f"In which (new) topic do you want to write your Feedback (max. {max_topic_length} Characters)?\n If no topic is given, the default topic `New Feedback` will be used."))
        if prompt2["result"] != "success":
            raise DMError("Could not send message to user")

        result2, _ = await UserInput.short_text_response(self.client, prompt2["id"], timeout=60, allow_spaces=True,max_length=max_topic_length)
        if result2 is not None and len(result2) <= max_topic_length:
            topic = result2


        prompt3 = await self.client.send_response(Response.build_message(message, content="Now you have 10 minutes to write your Feedback, please remember to be respectful and constructive:"))
        if prompt3["result"] != "success":
            raise DMError("Could not send message to user")
        
        result3, _ = await UserInput.short_text_response(self.client, prompt3["id"], timeout=600, allow_spaces=True)
        if result3 is None:
            raise DMError("No response from user")
        else:
            response = await self.client.send_message({"type": "stream",
                                            "to": course.FeedbackStream.id, 
                                            "topic": topic, 
                                            "content": result3
                                            })
            
            if response["result"] != "success":
                raise DMError("Something went wrong when sending your message to the Feedback-Channel :botsweat:")
            
        
        yield DMResponse("Your Feedback is now sent to the Feedback-Channel of the course :bothappy:")
            


