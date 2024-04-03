#!/usr/bin/env python3

# See LICENSE file for copyright and license details.
# TUM CS Bot - https://github.com/ro-i/tumcsbot

"""Manage reactions on certain words or phrases with emojis.

Use the Zulip facility, see https://zulip.com/help/add-an-alert-word.
Provide also an interactive command so administrators are able to
change the alert words and specify the emojis to use for the reactions.
"""

from enum import Enum
from inspect import cleandoc
from typing import Any, Iterable, Callable

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship, Mapped
import yaml

from tumcsbot.lib.response import Response
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import TableBase, serialize_model
from tumcsbot.lib.types import ZulipStream
from tumcsbot.plugin import PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import *
from tumcsbot.plugins.usergroup import UserGroup, UserGroupMember, Usergroup

# from tumcsbot.plugins.moderation_reaction_handler import ModerationReactionHandler


class ReactionAction(TableBase):
    __tablename__ = "ReactionAction"

    ReactionActionId = Column(Integer, primary_key=True, autoincrement=True)
    Action = Column(String, nullable=False)
    Data = Column(String)

    reaction = Column(Integer, ForeignKey("ReactionConfig.id", ondelete="CASCADE"))


class ReactionConfig(TableBase):
    __tablename__ = "ReactionConfig"

    id = Column(Integer, primary_key=True, autoincrement=True)
    emote = Column(String, unique=True, nullable=False)
    ModerationConfigId = Column(
        Integer, ForeignKey("ModerationConfig.ModerationConfigId", ondelete="CASCADE")
    )

    actions: Mapped[list[ReactionAction]] = relationship()


class GroupAuthorization(TableBase):
    __tablename__ = "GroupAuthorization"

    GroupId = Column(
        Integer, ForeignKey(UserGroup.GroupId, ondelete="CASCADE"), primary_key=True
    )
    ModerationConfigId = Column(
        Integer, ForeignKey("ModerationConfig.ModerationConfigId", ondelete="CASCADE"), primary_key=True
    )

    group: Mapped[UserGroup] = relationship()


class StreamAuthorization(TableBase):
    __tablename__ = "StreamAuthorization"

    Stream = Column(ZulipStream, primary_key=True)
    ModerationConfigId = Column(
        Integer, ForeignKey("ModerationConfig.ModerationConfigId", ondelete="CASCADE"), primary_key=True
    )


class ModerationConfig(TableBase):
    __tablename__ = "ModerationConfig"

    ModerationConfigId = Column(Integer, primary_key=True, autoincrement=True)
    ModerationConfigName = Column(String, nullable=False, unique=True)

    streams: Mapped[list[StreamAuthorization]] = relationship()
    groups: Mapped[list[GroupAuthorization]] = relationship()
    reactions: Mapped[list[ReactionConfig]] = relationship()


class ReactionActionType(Enum):
    DM = 1
    DELETE = 2
    RESPOND = 2


class Moderate(PluginCommandMixin, Plugin):

    # pylint: disable=line-too-long
    _default_config: list[tuple[str, str, str | None, str]] = [
        (
            ":recycle:",
            "dm",
            cleandoc(
                """            Deine Frage wurde bereits woanders gestellt und wurde deshalb gelöscht. Bitte verwende die Suchfunktion um die Antwort für deine Frage zu finden.
                                                        ---
                                                        Your question has already been asked elsewhere and therefore has been deleted. Please use the search function to find the answer to your question.
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that the question has already been asked on zulip",
        ),
        (":recycle:", "delete", None, "deletes the message"),
        (
            ":taking_a_picture:",
            "dm",
            cleandoc(
                """   In deiner Frage hast du ein Foto von deinem Bildschirm gepostet. Deine Nachricht wurde deshalb gelöscht. Bitte verwende formatierten Text für Textausgaben und Screenshots für nicht textuelle Inhalte und stelle deine Frage erneut.
                                                        ---
                                                        In your question, you posted a photo of your screen. Therefore, it was deleted. Please use formatted text for text outputs and screenshots for non-textual content and repost your question.
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that proper formatting should be used instead of pictures or screenshots",
        ),
        (":taking_a_picture:", "delete", None, "deletes the message"),
        (
            ":question:",
            "dm",
            cleandoc(
                """           Deine Frage ist nicht klar genug formuliert oder das Problem ist nicht klar erkennbar und wurde deshalb gelöscht. Bitte versuche genauer auf dein Problem einzugehen und eine klare Frage zu stellen.
                                                        ---
                                                        Your question is not formulated clearly enough, or the problem is not clearly identifiable. Therefore, the message was deleted. Please ask your question again and try to elaborate more on your problem, providing a clear question.
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that he should clarify the question",
        ),
        (
            ":scroll:",
            "dm",
            cleandoc(
                """             Deine Frage wird bereits in der Aufgabenstellung beantwortet und wurde deshalb gelöscht.
                                                        ---
                                                        Your question is already answered in the task description and therefore was deleted.
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that the question has already been answered in the problem statement",
        ),
        (":scroll:", "delete", None, "deletes the message"),
        (
            ":headlines:",
            "dm",
            cleandoc(
                """          Deine Frage hat keinen deskriptiven Topic-Titel oder deine Nachricht gehört nicht in das Topic $topic und wurde deshalb gelöscht. Bitte passe den Topic-Titel an, erstelle ein neues Topic oder poste deine Nachricht in ein passendes Topic.
                                                        ---
                                                        Your question has an ambiguous topic title or does not belong in the topic $topic and therefore was deleted. Please change the topic title, create a new topic, or post your message in an appropriate topic.
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that the question has a bad title or is in the wrong stream",
        ),
        (":headlines:", "delete", None, "deletes the message"),
        (
            ":www:",
            "dm",
            cleandoc(
                """                Deine Nachricht wurde gelöscht. Bitte verwende eine Suchmaschine deiner Wahl, um dein Problem zu lösen oder deine Frage zu beantworten.
                                                        ---
                                                        Your message was deleted. Please use a search engine of your choice to answer your question or solve your problem.
                                                        ```spoiler
                                                        [stackoverflow](https://stackoverflow.com/?q=$escaped_topic):stackoverflow:
                                                        [DuckDuckGo](https://duckduckgo.com/?q=$escaped_topic):duck:
                                                        [Google](https://google.com/search?q=$escaped_topic):google:
                                                        [Bing](https://bing.com/search?q=$escaped_topic):microsoft:
                                                        [Ecosia](https://ecosia.com/search?q=$escaped_topic):tree:
                                                        [Yahoo](https://yahoo.com/search?q=$escaped_topic):yahoo:
                                                        [webcrawler](https://www.webcrawler.com/search?q=$escaped_topic):web:
                                                        ```
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic 
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that question should be aswered by searching online (e.g. googeling)",
        ),
        (":www:", "delete", None, "deletes the message"),
        (
            ":crown:",
            "dm",
            cleandoc(
                """              Ich wollte mich herzlich bei dir bedanken für deine herausragende Antwort in unserem Forum. Deine Erklärung war besonders klar und hilfreich. Es ist toll zu sehen, dass so engagierte Studierende wie du dazu beitragen, unser Wissen zu vertiefen. Weiter so:penguin: 
                                                        ---
                                                        I wanted to express my sincere gratitude for your outstanding response in our forum. Your explanation was exceptionally clear and helpful. It's great to see dedicated students like you contributing to deepening our knowledge. Keep up the excellent work:penguin:
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that his answer is excellent",
        ),
        (
            ":wastebasket:",
            "dm",
            cleandoc(
                """        Deine Nachricht wurde gelöscht.
                                                        ---
                                                        Your question has been deleted.
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that the message was deleted",
        ),
        (":wastebasket:", "delete", None, "deletes the message"),
        (
            ":document:",
            "dm",
            cleandoc(
                """        Deine Nachricht wurde gelöscht. Bitte verwende die offizielle Dokumentation, um dein Problem zu lösen oder deine Frage zu beantworten.
                                                        ---
                                                        Your question has been deleted. Please use the official documentation to answer your question or solve your problem.
                                                        ```spoiler Deine ursprüngliche Nachricht in $topic | Your original message in $topic
                                                        $content
                                                        ```
                                                        ---
                                                        Diese Benachrichtigung wurde von $mod beauftragt.
                                                        This notification was issued by $mod.""",
            ),
            "sends a dm to the author that the question should be answered by looking at the official documentation",
        ),
        (":document:", "delete", None, "deletes the message"),
    ]
    # pylint: enable=line-too-long

    @staticmethod
    def parse_action(s: str) -> str:
        if s in Moderate._actions:
            return s
        raise ValueError

    @staticmethod
    def parse_action_or_number(s: str) -> str:
        try:
            return Moderate.parse_action(s)
        except:
            return str(int(s))

        # pylint: disable=line-too-long
        self.command_parser: CommandParser = CommandParser()
        self.command_parser.add_subcommand(
            "list",
            optionals={"user": Regex.match_user_argument},
            opts={"a": None, "all": None, "v": None, "verbose": None},
            # todo: description=cleandoc(
            # todo:     """
            # todo:     list moderation configuration for a users.
            # todo:     - `user` : the user for which the config should be displayed. Defaults to the sender of the command
            # todo:     - `-a, --all` : option to display configuration for all users
            # todo:     - `-v, --verbose` : additionaly show the actions taken for each reaction
            # todo:     """
            # todo: ),
        )

        actions_str = "\n" + "\n".join([f"  - `{a}`" for a in self._actions]) + "\n"
        supported_variables = "\n".join(
            [
                f"  - `${name}`: {desc}"
                for name, (_, desc) in ModerationReactionHandler._replace_dict.items()
            ]
        )

        self.command_parser.add_subcommand(
            "add",
            args={
                "reaction": Regex.match_reaction_argument,
                "action": Moderate.parse_action,
            },
            optionals={
                "user": Regex.match_user_argument,
                "message": str,
                "description": str,
            },
            # todo: description=cleandoc(
            # todo:     """
            # todo:     Add an moderation configuration for a user.
            # todo:     - `reaction` : the reaction that should trigger an action
            # todo:     - `action` : the action that should be triggered. Supported actions are:
            # todo:     """
            # todo: )
            # todo: + actions_str
            # todo: + cleandoc(
            # todo:     """
            # todo:     - `user` : the user this configuration should be addded. Defaults to the sender of the command
            # todo:     - `message` : the message an action should use. The message may use special variables that are replaced depending on the context. Supported variables for message content:
            # todo:     """
            # todo: )
            # todo: + supported_variables,
        )

        self.command_parser.add_subcommand(
            "remove",
            optionals={
                "user": Regex.match_user_argument,
                "reaction": Regex.match_reaction_argument,
                "action": Moderate.parse_action_or_number,
            },
            # todo: description=cleandoc(
            # todo:     """
            # todo:
            # todo:     Remove reactions from a configuration
            # todo:     - `user` : the user the reaction should be removed from. Defaults to the sender of the command
            # todo:     - `reaction` : the reaction that should be affected. Defaults to all reactions
            # todo:     - `action` : the action that should be removed. May be the action keyword or the number of the action-element (starting with 1)
            # todo:     """
            # todo: ),
        )

        self.command_parser.add_subcommand(
            "authorize",
            args={"group": str},
            greedy={"streams": str},
            # todo: description=cleandoc(
            # todo:     """
            # todo:     Authorize a group to allow moderation in streams
            # todo:     - `group` : the group that should be granted moderation rights
            # todo:     - `streams` : the streams that users in `<group>` should be able to moderate
            # todo:     """
            # todo: ),
        )

        self.command_parser.add_subcommand(
            "revoke",
            optionals={
                "group": Regex.match_group_argument,
                "stream": Regex.match_stream_argument,
            },
            # todo: description=cleandoc(
            # todo:     """
            # todo:     Remove authorization
            # todo:     - `group` : the group that should be revoked. If `stream` is not specified, permissions for all streams are revoked for this group
            # todo:     - `stream` : the stream that should be revoked. If `group` is not specified, permissions of all groups are revoked for this stream
            # todo:     """
            # todo: ),
        )

        defaults_str = ", ".join(set([e for e, _, _, desc in self._default_config]))
        self.command_parser.add_subcommand(
            "defaults",
            greedy={"users": Regex.match_user_argument},
            # todo: description=cleandoc(
            # todo:     """
            # todo:     Set the actions for [
            # todo:     """
            # todo: )
            # todo: + defaults_str
            # todo: + cleandoc(
            # todo:     """] to their defaults
            # todo:     - `users` : the users that should get their default reactions set
            # todo:     """
            # todo: ),
        )
        # pylint: enable=line-too-long

        self.update_plugin_usage()

    @command(name="list")
    @arg(
        "user",
        ZulipUser,
        optional=True,
        description="The user for which the config should be displayed. Defaults to the sender of the command",
    )
    @opt(
        "a",
        "all",
        privilege=Privilege.ADMIN,
        description="Option to display configuration for all users",
    )
    @opt(
        "v",
        "verbose",
        description="Additionaly show the actions taken for each reaction",
    )
    async def _list(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        user: ZulipUser

        if args.user is not None:
            user = args.user
        else:
            user = sender

        if not user.isPrivileged and sender.id != user.id:
            raise UserNotPrivilegedException(
                "You need to be privileged to view the moderation configuration of other users."
            )

        content = ""
        if opts.a:
            cfgs = session.query(ModerationConfig).all()
        else:
            cfgs = (
                session.query(ModerationConfig)
                .join(GroupAuthorization)
                .join(UserGroup)
                .filter(UserGroup._members.any(UserGroupMember.User == user))
                .all()
            )

        content = "---\n".join(
            [await Moderate.format_config(c, verbose=opts.v) for c in cfgs]
        )

        if not cfgs:
            if opts.a:
                raise DMError("No moderation configuration found")

            raise DMError(
                f"No moderation configuration found for {user.mention_silent}"
            )

        if not opts.v:
            content += "---\n*hint: use option -v to see detailed description*"
        yield DMResponse(content)

    def _add(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        user_id: int | None
        uid: int
        description: str

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
            if user_result["result"] != "success":
                return Response.build_message(
                    message,
                    f"Error: User with id {uid} not found.",
                    msg_type="private",
                )
            args.user = f"@_**{user_result['user']['full_name']}|{user_result['user']['user_id']}**"

        if (
            not self.client.user_is_privileged(message["sender_id"])
            and message["sender_id"] != uid
        ):
            return Response.privilege_err(message)

        if args.action not in self._actions:
            return Response.build_message(
                message,
                f"Error: '{args.action}' is not a valid action.",
                msg_type="private",
            )

        if args.description is None:
            description = self._actions[args.action]
        else:
            description = args.description

        self._db.execute(
            self._insert_reaction_sql,
            uid,
            args.reaction,
            args.action,
            args.message,
            description,
            commit=True,
        )
        return Response.ok(message)

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the configuration")
    async def create(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Create a new moderation configuration
        """
        if (
            session.query(ModerationConfig)
            .filter(ModerationConfig.ModerationConfigName == args.name)
            .count()
            > 0
        ):
            raise DMError(
                f"Error: Configuration with name '{args.name}' already exists"
            )

        session.add(ModerationConfig(name=args.name))
        session.commit()
        yield DMResponse(f"Configuration '{args.name}' created")

    @command
    async def defaults(
        self,
        _sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:

        emotes = set([e for e, _, _, _ in self._default_config])
        session.query(ModerationConfig).filter(
            ModerationConfig.ModerationConfigName == "default"
        ).delete()
        session.merge(
            ModerationConfig(
                name="default",
                reactions=[
                    ReactionConfig(
                        emote=emote_str,
                        actions=[
                            ReactionAction(action=action_str, data=msg_str)
                            for inner_emote, action_str, msg_str, _ in self._default_config
                            if inner_emote == emote_str
                        ],
                    )
                    for emote_str in emotes
                ],
            )
        )
        session.commit()
        yield DMResponse("Default reactions set")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "moderation_config",
        ModerationConfig.ModerationConfigName,
        description="The name of the moderation configuration",
    )
    @opt(
        "g",
        "group",
        UserGroup.GroupName,
        description="The group that should be granted moderation rights",
    )
    @opt(
        "s",
        "stream",
        ZulipStream,
        description="The streams that the moderation configuration is applicable to should be able to moderate",
    )
    async def authorize(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        moderation_config: ModerationConfig = args.moderation_config
        group: UserGroup | None = opts.group
        stream: ZulipStream | None = opts.stream

        if not group and not stream:
            raise DMError("Error: At least a stream or a group must be specified.")

        if group and stream:
            raise DMError(
                "Error: Either a group or streams must be specified, not both."
            )

        if group:
            session.merge(
                GroupAuthorization(
                    GroupId=group.GroupId, ModerationConfigId=moderation_config.id
                )
            )
            session.commit()
            for member in group.members:
                yield DMMessage(
                    member,
                    f"Hey,\nthe group '{group.GroupName}' you are a member of has been granted moderation rights for `{moderation_config.name}`.\n*hint: use the moderate command for more information*",
                )
            yield DMResponse(
                f"Notified members of group '{group.GroupName}' about the new moderation rights."
            )
        else:
            session.merge(
                StreamAuthorization(
                    Stream=stream, ModerationConfigId=moderation_config.id
                )
            )

            member: UserGroupMember
            for member in group.members:
                yield DMMessage(
                    member,
                    f"Hey,\nthe group '{group.GroupName}' you are a member of has been granted moderation rights for `{moderation_config.name}`.\n*hint: use the moderate command for more information*",
                )

            session.commit()
            yield DMResponse(
                f"Group '{group.GroupName}' has been granted moderation rights for {moderation_config.name}."
            )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "moderation_config", ModerationConfig.ModerationConfigName, description="The name of the moderation configuration"
    )
    @opt("g", "group", UserGroup.GroupName, description="The group that should no longer be granted moderation rights")
    @opt("s", "stream", ZulipStream, description="The stream that should no longer be able to be moderated")
    async def revoke(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        config: ModerationConfig = args.moderation_config


        if opts.stream:
            stream: ZulipStream = opts.stream

            if not config.streams.any(StreamAuthorization.Stream == stream):
                raise DMError(
                    f"Error: Stream '{stream.name}' does not have moderation rights for {config.ModerationConfigName}"
                )

            session.query(StreamAuthorization).filter(
                StreamAuthorization.ModerationConfigId == config.ModerationConfigId
            ).filter(StreamAuthorization.Stream == stream).delete()
            session.commit()
            yield DMResponse(
                f"Stream '{stream}' has been revoked moderation rights for {config.ModerationConfigName} and the members have been notified."
            )
        
        else:
            group: UserGroup = opts.group

            if not config.groups.any(GroupAuthorization.GroupId == group.GroupId):
                raise DMError(
                    f"Error: Group '{group.GroupName}' does not have moderation rights for {config.ModerationConfigName}"
                )

            session.query(GroupAuthorization).filter(
                GroupAuthorization.ModerationConfigId == config.ModerationConfigId
            ).filter(GroupAuthorization.GroupId == group.GroupId).delete()
            session.commit()
            yield DMResponse(
                f"Group '{group.GroupName}' has been revoked moderation rights for {config.ModerationConfigName} and the members have been notified."
            )


    @command
    @privilege(Privilege.ADMIN)
    @arg("name", ModerationConfig.ModerationConfigName, description="The name of the configuration")
    @arg("emote", Regex.get_reaction_emoji, description="The emote that should trigger the reaction")
    @arg("configuration", str, description="The configuration for the reaction as a yaml-style code block.", greedy=True)
    async def configure_reaction(
        self,
        _sender: ZulipUser,
        session: Session,
        _args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Configure a reaction.
        ---
        the configuration should be a yaml-style code block with the following structure:
        ```yaml
        - <action>: [data]
        # ...
        # ...
        # ...
        ```
        where `<action>` is one of the following actions:
        - `dm` : send a dm to the author
        - `delete` : delete the message
        - `respond` : respond to the message
        and `[data]` is the message that should be sent.

        Here is an example configuration:
        ```yaml
        - dm: >
            Your question has already been asked elsewhere and was therefore deleted.
        - delete: null
        ```
        """
        pass

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
        configs = []
        for c in session.query(ModerationConfig).all():
            try:
                dict_repr = {
                    "name": c.name,
                    "streams": [serialize_model(s) for s in c.streams],
                    "groups": [g.group.GroupName for g in c.groups],
                    "reactions": [serialize_model(r) for r in c.reactions],
                }
                configs.append(dict_repr)
            except Exception as e:
                yield PartialError(f"Could not serialize group {c.name}: {str(e)}")
                self.logger.exception(e)
                continue
            yield PartialSuccess(f"Exported group {c.name}")
        yield DMResponse(
            "```yaml\n"
            + yaml.dump(configs, allow_unicode=True, sort_keys=False)
            + "\n```"
        )

    @staticmethod
    async def format_reactions(
        reactions: list[ReactionConfig], verbose: bool = False
    ) -> str:
        if verbose:
            msg = "\nEmote | Reaction\n"
            msg += "---- | ----\n"
            for r in reactions:
                actions = ",".join([f"[{a.Action}]`{a.Data}`" for a in r.actions])
                msg += f"{r.emote} | {actions}\n"
            msg += "\n"
        else:
            emotes = [r.emote for r in reactions]
            if len(emotes) == 0:
                msg = "*No reactions configured*\n"
            else:
                msg = ", ".join(emotes) + "\n"
        return msg

    @staticmethod
    async def format_authorizations(
        authorizations: list[GroupAuthorization],
        verbose: bool = False,
    ) -> str:
        msg = ""
        if len(authorizations) == 0:
            return "*No authorizations configured*\n"
        else:
            for g in authorizations:
                msg += f" - {g.group.GroupName}\n"
                if verbose:
                    members = [m for m in g.group.members]
                    for m in members:
                        await m
                    msg += "    " + ", ".join([m.mention_silent for m in members]) 
        return msg

    @staticmethod
    async def format_config(
        cfg: ModerationConfig,
        verbose: bool = False,
    ) -> str:
        msg = f"## Configuration for {cfg.ModerationConfigName}\n"
        msg += "**Configured reactions**\n"
        msg += await Moderate.format_reactions(cfg.reactions, verbose)
        msg += "**Authorized groups:**\n"
        msg += await Moderate.format_authorizations(cfg.groups, verbose)
        return msg
