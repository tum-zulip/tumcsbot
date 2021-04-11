TUM CS Bot
==========

An interactive bot for zulip.in.tum.de, the [Zulip Chat](https://zulipchat.com/)
of the Department of Informatics of the Technical University of Munich.

**Note: This bot is currently under development and not yet production-ready!**


setup
-----

- The bot is intended to run as `Generic` bot, owned by an administrator.
  - [add a bot](https://zulipchat.com/help/add-a-bot-or-integration)
  - [about bots](https://zulipchat.com/help/bots-and-integrations)
- Get the bot's `zuliprc` file. Per default, it is expected to be located
right in the root of the bot's git repo.
- Configure `supervisor` (used by Zulip installations per default) to handle
the bot by placing the configuration file `zulip_tumcsbot.conf` in
`/etc/supervisor/conf.d`.

Note: For some commands such as `subscribe` or `solved` the bot needs
administrator and `api_super_user` rights.
([documentation for Zulip 3.x](https://github.com/zulip/zulip/blob/3.x/docs/production/security-model.md)).
In order to grant those rights, run
- `manage.py knight --for-real --permission=administer <bot_email>` (Zulip <= 3.2)
- `manage.py change_user_role -r REALM_ID <bot_email> admin` and\
  `manage.py change_user_role -r REALM_ID <bot_email> administrator`

in the appropriate directory of your zulip server installation.


usage
-----

- `make init` will create the database `tumcsbot.db` and install a virtual
  environment into `venv`.
- `make run` lets you run the bot.
- `make debug` runs the bot with debug logging enabled.

You can also run the bot manually:
```
usage: main.py [-h] [-t N] [-d] [-l LOGFILE] ZULIPRC DB_PATH

TUM CS Bot - a generic Zulip bot.

This bot is currently especially intended for administrative tasks.
It supports several commands which can be written to the bot using
a private message or a message starting with @mentioning the bot.

positional arguments:
  ZULIPRC               zuliprc file containing the bot's configuration
  DB_PATH               path to the bot's database

optional arguments:
  -h, --help            show this help message and exit
  -t N, --threads N     maximum number of threads to use to run the plugins (default: 8)
  -d, --debug           debugging mode switch
  -l LOGFILE, --logfile LOGFILE
                        use LOGFILE for logging output
```


usage in chat
-------------
Write the word `help` to the bot - as private message or using
`@<tumcsbot-name> help`. It will tell you how you can use it. :-)


notes
-----

My work on the possibility of accessing a file that the sender has added to a
message using the "Attach files" function and my questions about this topic on
chat.zulip.org have led to the corresponding issue on github:
https://github.com/zulip/python-zulip-api/issues/628

The bot supports a dynamic plugin infrastructure and also generates the help
message dynamically by using appropriate attributes every plugin has to
provide.

In order to apply database migration scripts conveniently, there is the script
`src/migrate.py` (see also `make migrations`).


additional `make` targets
-------------------------
- `make tests` runs some unit tests.
- `make mypy` runs `mypy --strict` and should not show any issue.
- `make static_analysis` currently runs `mypy` and `pylint`.
- `make migrations` applies the migrations in `migrations.sql` to the database
  `tumcsbot.db` using the script `src/migrate.py`.


model
-----

![class diagram](./class_diagram.svg?)

