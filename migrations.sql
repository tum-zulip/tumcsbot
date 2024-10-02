drop table GroupAuthorization;
drop table selfStats;
drop table ReactionConfig;
drop table UserGroups;
drop table UserGroupMembers;
drop table Plugins;
drop table PublicStreams;
drop table NewMessages;

create table UserGroups (
    GroupId integer primary key autoincrement,
    GroupName text not null unique
);

create table ChannelGroups (
    ChannelGroupId text primary key,
    ChannelGroupEmote text not null unique,
    UserGroupId integer not null,
    foreign key (UserGroupId) references UserGroups(GroupId) on delete cascade
);

create table UserGroupMembers (
    GroupId integer not null,
    User integer not null,
    primary key (GroupId, User),
    foreign key (GroupId) references UserGroups(GroupId) on delete cascade
);

insert into UserGroups(GroupName)
select distinct Id as GroupName from Groups;

insert into ChannelGroups(ChannelGroupId, ChannelGroupEmote, UserGroupId)
select Id as ChannelGroupId, Emoji as ChannelGroupEmote, UserGroups.GroupId as UserGroupId
from Groups, UserGroups
where Groups.Id = UserGroups.GroupName;


insert into UserGroupMembers(GroupId, User)
select UserGroups.GroupId as GroupId, GroupUsers.UserId as User
from GroupUsers, UserGroups
where GroupUsers.GroupId = UserGroups.GroupName;

drop table Groups;
drop table GroupUsers;

create table NewGroupClaimsAll (
    MessageId integer primary key,
    IsAnnouncement boolean not null default 0
);

insert into NewGroupClaimsAll(MessageId, IsAnnouncement)
select MessageId, 1 as IsAnnouncement from GroupClaimsAll;
drop table GroupClaimsAll;
alter table NewGroupClaimsAll rename to GroupClaimsAll;