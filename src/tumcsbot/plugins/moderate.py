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
from typing import Any, AsyncGenerator, cast

from sqlalchemy import Column, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship, Mapped
import yaml

from tumcsbot.lib.response import Response
from tumcsbot.lib.regex import Regex
from tumcsbot.lib.command_parser import CommandParser
from tumcsbot.lib.db import TableBase, serialize_model, Session, deserialize_model
from tumcsbot.lib.types import ZulipChannel
from tumcsbot.plugin import PluginCommandMixin, Plugin
from tumcsbot.plugin_decorators import arg, command, opt, privilege
from tumcsbot.plugins.usergroup import UserGroup, UserGroupMember, Usergroup

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


class ReactionAction(TableBase):  # type: ignore
    __tablename__ = "ReactionAction"

    ReactionActionId = Column(Integer, primary_key=True, autoincrement=True)
    Action = Column(String, nullable=False)
    Data = Column(String)

    reaction = Column(Integer, ForeignKey("ReactionConfig.id", ondelete="CASCADE"))


class ReactionConfig(TableBase):  # type: ignore
    __tablename__ = "ReactionConfig"

    id = Column(Integer, primary_key=True, autoincrement=True)
    emote = Column(String, nullable=False)
    ModerationConfigId = Column(
        Integer, ForeignKey("ModerationConfig.ModerationConfigId", ondelete="CASCADE")
    )

    actions: Mapped[list[ReactionAction]] = relationship()

    __table_args__ = (UniqueConstraint("emote", "ModerationConfigId"),)


class GroupAuthorization(TableBase):  # type: ignore
    __tablename__ = "GroupAuthorization"

    GroupId = Column(
        Integer, ForeignKey(UserGroup.GroupId, ondelete="CASCADE"), primary_key=True
    )
    ModerationConfigId = Column(
        Integer,
        ForeignKey("ModerationConfig.ModerationConfigId", ondelete="CASCADE"),
        primary_key=True,
    )

    group: Mapped[UserGroup] = relationship()


class ChannelAuthorization(TableBase):  # type: ignore
    __tablename__ = "ChannelAuthorization"

    Channel = Column(ZulipChannel, primary_key=True) # type: ignore
    ModerationConfigId = Column(
        Integer,
        ForeignKey("ModerationConfig.ModerationConfigId", ondelete="CASCADE"),
        primary_key=True,
    )


class ModerationConfig(TableBase):  # type: ignore
    __tablename__ = "ModerationConfig"

    ModerationConfigId = Column(Integer, primary_key=True, autoincrement=True)
    ModerationConfigName = Column(String, nullable=False, unique=True)

    channels: Mapped[list[ChannelAuthorization]] = relationship()
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
            "sends a dm to the author that the question has a bad title or is in the wrong channel",
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
        priv=Privilege.ADMIN,
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
        _message: dict[str, Any],
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
                .filter(UserGroup._members.any(UserGroupMember.User == user)) # type: ignore
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

        session.add(ModerationConfig(ModerationConfigName=args.name))
        session.commit()
        yield DMResponse(f"Configuration '{args.name}' created")

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
        "c",
        "channel",
        ZulipChannel,
        description="The channels that the moderation configuration is applicable to should be able to moderate",
    )
    async def authorize(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        moderation_config: ModerationConfig = args.moderation_config
        group: UserGroup | None = opts.group
        channel: ZulipChannel | None = opts.channel

        if group is None and channel is None:
            raise DMError("Error: At least a channel or a group must be specified.")

        if group and channel:
            raise DMError(
                "Error: Either a group or channels must be specified, not both."
            )

        if group is not None:
            if (
                session.query(ModerationConfig)
                .filter(GroupAuthorization.GroupId == group.GroupId)
                .first()
            ):
                raise DMError(
                    f"Error: Group '{group.GroupName}' already has moderation rights for '{moderation_config.ModerationConfigName}'"
                )

            session.merge(
                GroupAuthorization(
                    GroupId=group.GroupId,
                    ModerationConfigId=moderation_config.ModerationConfigId,
                )
            )
            session.commit()
            members = Usergroup.get_users_for_group(session, group)
            for member in members:
                yield DMMessage(
                    member,
                    cleandoc(
                        f"""
                        Hey,
                        The group '{group.GroupName}' you are a member of has been granted moderation rights for `{moderation_config.ModerationConfigName}`.
                        *hint: use the moderate command for more information*
                    """
                    ),
                )
            yield DMResponse(
                f"Notified members of group '{group.GroupName}' about the new moderation rights."
            )
        else:
            channel = cast(ZulipChannel, channel)
            if (
                session.query(ModerationConfig)
                .filter(ChannelAuthorization.Channel == channel) # type: ignore
                .first()
            ):
                raise DMError(
                    f"Error: Moderation for Channel {channel.mention} is already enabled in {moderation_config.ModerationConfigName}"
                )

            session.merge(
                ChannelAuthorization(
                    Channel=channel,
                    ModerationConfigId=moderation_config.ModerationConfigId,
                )
            )
            session.commit()
            yield DMResponse(
                f"Channel {channel.mention} has been marked as moderateable for {moderation_config.ModerationConfigName}."
            )

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
        description="The group that should no longer be granted moderation rights",
    )
    @opt(
        "c",
        "channel",
        ZulipChannel,
        description="The channel that should no longer be able to be moderated",
    )
    async def revoke(
        self,
        sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        config: ModerationConfig = args.moderation_config

        if not opts.group and not opts.channel:
            raise DMError("Error: At least a channel or a group must be specified.")

        if opts.group and opts.channel:
            raise DMError(
                "Error: Either a group or channels must be specified, not both."
            )

        if opts.channel:
            channel = opts.channel
            if (
                not session.query(ModerationConfig)
                .filter(ChannelAuthorization.Channel == channel)
                .first()
            ):
                raise DMError(
                    f"Error: Channel {channel.mention} does not have moderation rights for {config.ModerationConfigName}"
                )

            session.query(ChannelAuthorization).filter(
                ChannelAuthorization.ModerationConfigId == config.ModerationConfigId
            ).filter(ChannelAuthorization.Channel == channel).delete()
            session.commit()
            yield DMResponse(
                f"Channel {channel.mention} has been revoked moderation rights for {config.ModerationConfigName} and the members have been notified."
            )

        else:
            group = opts.group
            if (
                not session.query(ModerationConfig)
                .filter(GroupAuthorization.GroupId == group.GroupId)
                .first()
            ):
                raise DMError(
                    f"Error: Group '{group.GroupName}' does not have moderation rights for '{config.ModerationConfigName}'"
                )

            session.query(GroupAuthorization).filter(
                GroupAuthorization.ModerationConfigId == config.ModerationConfigId
            ).filter(GroupAuthorization.GroupId == group.GroupId).delete()
            session.commit()
            yield DMResponse(
                f"Group '{group.GroupName}' has been revoked moderation rights for '{config.ModerationConfigName}' and the members have been notified."
            )

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "name",
        ModerationConfig.ModerationConfigName,
        description="The name of the configuration",
    )
    @arg(
        "emote",
        Regex.get_reaction_emoji,
        description="The emote that should trigger the reaction",
    )
    @arg(
        "configuration",
        str,
        description="The configuration for the reaction as a yaml-style code block.",
    )
    @opt(
        "f",
        "force",
        description="Overwrite existing configuration for reaction and delete all configured actions",
    )
    async def configure_reaction(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Configure a reaction.
        ---
        the configuration should be a yaml-style code block with the following structure:
        ```yaml
        - <action>[: data]
        # ...
        # ...
        # ...
        ```
        where `<action>` is one of the following actions:
        `dm` : send a dm to the author
        `delete` : delete the message
        `respond` : respond to the message
        and `[data]` is the message that should be sent.

        Here is an example configuration:
        ```yaml
        - dm: Your question has already been asked elsewhere and was therefore deleted.
        - delete
        ```
        """
        config = self.load_yaml_from_string(args.configuration)

        actions = []
        for action in config:
            actions.append(self.reaction_action_from_yaml(action))

        moderation_config: ModerationConfig = args.name
        reaction = ReactionConfig(
            ModerationConfigId=moderation_config.ModerationConfigId,
            emote=args.emote,
            actions=actions,
        )
        r = (
            session.query(ReactionConfig)
            .filter(
                ReactionConfig.emote == args.emote,
                ReactionConfig.ModerationConfigId
                == moderation_config.ModerationConfigId,
            )
            .first()
        )
        if r and not opts.f:
            raise DMError(
                f"Reaction for {args.emote} already configured. Use -f to overwrite."
            )

        session.merge(reaction)
        # moderation_config.reactions.append(reaction)
        session.commit()
        yield DMResponse(f"Reaction for {args.emote} configured")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "name",
        ModerationConfig.ModerationConfigName,
        description="The name of the configuration",
    )
    @arg(
        "emote",
        Regex.get_reaction_emoji,
        description="The emote that should trigger the reaction",
    )
    async def remove(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Remove a reaction.
        """
        moderation_config: ModerationConfig = args.name
        reaction = (
            session.query(ReactionConfig)
            .filter(
                ReactionConfig.emote == args.emote,
                ReactionConfig.ModerationConfigId
                == moderation_config.ModerationConfigId,
            )
            .first()
        )
        if reaction is None:
            yield DMResponse(f"No Actions configured for :{args.emote}:")
            return
        session.delete(reaction)
        session.commit()
        yield DMResponse(f"Actions for :{args.emote}: removed")

    @command
    @privilege(Privilege.ADMIN)
    @arg(
        "name",
        ModerationConfig.ModerationConfigName,
        description="The name of the configuration",
        optional=True,
    )
    async def export(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        _opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        """
        Export all user groups as yaml.
        """
        if args.name:
            cfg = (
                session.query(ModerationConfig)
                .filter(ModerationConfig.ModerationConfigName == args.name)
                .first()
            )
            if not cfg:
                raise DMError(f"Configuration '{args.name}' not found.")
            yield DMResponse(
                f"```yaml\n{yaml.dump(await serialize_model(cfg), allow_unicode=True, sort_keys=False)}\n```"
            )
            return

        configs = []
        for c in session.query(ModerationConfig).all():
            try:
                m = await serialize_model(c)
                configs.append(m)
            except Exception as e:
                yield PartialError(
                    f"Could not serialize group {c.ModerationConfigName}: {str(e)}"
                )
                self.logger.exception(e)
                continue
            yield PartialSuccess(f"Exported group {c.ModerationConfigName}")
        yield DMResponse(
            "```yaml\n"
            + yaml.dump(configs, allow_unicode=True, sort_keys=False)
            + "\n```"
        )

    @command
    @privilege(Privilege.ADMIN)
    @arg("name", str, description="The name of the configuration")
    @arg("config", str, description="The configuration as yaml")
    @opt(
        "f",
        "force",
        description="Overwrite existing configuration and delete all configured reactions",
    )
    async def load(
        self,
        _sender: ZulipUser,
        session: Session,
        args: CommandParser.Args,
        opts: CommandParser.Opts,
        _message: dict[str, Any],
    ) -> AsyncGenerator[response_type, None]:
        cfg = self.load_yaml_from_string(args.config)
        model = await deserialize_model(session, ModerationConfig, cfg)
        if (
            session.query(ModerationConfig)
            .filter(ModerationConfig.ModerationConfigName == model.ModerationConfigName)
            .count()
            > 0
        ):
            if not opts.force:
                raise DMError(
                    f"Configuration '{model.ModerationConfigName}' already exists. Use the -f option to overwrite."
                )
            session.query(ModerationConfig).filter(
                ModerationConfig.ModerationConfigName == model.ModerationConfigName
            ).delete()
            session.commit()

        session.add(model)
        session.commit()
        yield DMResponse(
            f"Configuration '{model.ModerationConfigName}' loaded:\n{await Moderate.format_config(model)}"
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
                msg = ", ".join(str(e) for e in emotes) + "\n"
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
                    msg += "\n"
        return msg

    @staticmethod
    async def format_config(
        cfg: ModerationConfig,
        verbose: bool = False,
    ) -> str:
        msg = f"## Configuration for {cfg.ModerationConfigName}\n"
        msg += "\n**Configured reactions**\n"
        msg += await Moderate.format_reactions(cfg.reactions, verbose)
        msg += "\n**Authorized groups:**\n"
        msg += await Moderate.format_authorizations(cfg.groups, verbose)
        msg += "\n**Authorized channels:**\n"
        channels: list[ZulipChannel] = []
        for s in cfg.channels:
            channel = ZulipChannel(s.Channel)
            await channel
            channels.append(channel)
        msg += ", ".join([s.mention for s in channels]) + "\n"
        return msg

    @staticmethod
    def load_yaml_from_string(s: str) -> Any:
        s = s.strip()
        if not s.startswith("```"):
            raise DMError("Error: Configuration must be a yaml-style code block.")
        if not s.endswith("```"):
            raise DMError("Error: Configuration must be a yaml-style code block.")
        yaml_content = s[3:-3]
        try:
            config = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise DMError(f"Error: Could not parse configuration: {str(e)}")
        return config

    @staticmethod
    def reaction_action_from_yaml(action: str | dict[str, Any]) -> ReactionAction:
        if isinstance(action, str):
            Moderate.ensure_valid_action(action)
            return ReactionAction(Action=action)
        elif isinstance(action, dict):
            if len(action.keys()) != 1:
                raise DMError("Error: Action must have exactly one key")

            action_str, data = next(iter(action.items()))
            Moderate.ensure_valid_action(action_str)
            return ReactionAction(Action=action_str, Data=data)
        else:
            raise ValueError("Error: Action must be a string or a dictionary")

    @staticmethod
    def ensure_valid_action(action: str) -> None:
        if action not in ["dm", "delete", "respond"]:
            raise DMError(
                f"Error: '{action}' is not a valid action. Supported actions are 'dm', 'delete' and 'respond'"
            )
