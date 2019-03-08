# Reddit Canned Response Bot
Simple Python 3 based bot to send canned responses to specific phrases and/or username mentions/summons.

Uses [praw](https://praw.readthedocs.io/en/latest/) and [pushshift](https://github.com/pushshift/api) ([psaw](https://github.com/dmarx/psaw)) to continually get new comments containing specific keywords, then regular expressions for further filtering. Sends a canned response if filter matches.
Username mentions just use praw to check unread inbox messages.

### Before you do anything
* Read the [Reddit bottiquette rules](https://www.reddit.com/r/Bottiquette/wiki/bottiquette)

### Setup
* Create a Reddit account for bot and [create an authorised app](https://www.reddit.com/prefs/apps/) if you haven't already
* Install python requirements `pip3 install -r requirements.txt`
* Create a [praw.ini](https://praw.readthedocs.io/en/latest/getting_started/configuration/prawini.html) file and fill out bot details (see example_praw.ini)
* Create a mybotname_config.json bot configuration file (see example_bot_config.json)

### Usage
* View options
  * `./bot.py -h`
* Run bot
  * `./bot.py mybotname_config.json mymainaccountname`
* Dry run (print replies without actually sending)
  * `./bot.py mybotname_config.json mymainaccountname --dry-run`
* Dry run starting from comments made one week (24*7=168 hours) ago
  * `./bot.py mybotname_config.json mymainaccountname --dry-run=168`

## Heroku
[Heroku](https://www.heroku.com) is a free way to run your bot entirely in the cloud.
* [Create a free Heroku account](https://signup.heroku.com)
* [Install Heroko CLI](https://devcenter.heroku.com/articles/heroku-cli#download-and-install)
*  Clone this repo and `cd` into project folder
* Create and run detached bot
    ```
    heroku login
    heroku create
    # Praw config (i.e. from praw.ini file)
    heroku config:set praw_client_id=abcdefhijklmno
    heroku config:set praw_client_secret=abcdefhijklmnopqrstuzwxyz01
    heroku config:set praw_user_agent=foo
    heroku config:set praw_username=foobot
    heroku config:set praw_password=bar
    # Add a Procfile that tells Heroku how to (re)start your app
    echo "worker: ./bot.py mybotname_config.json mymainaccountname" > Procfile
    git add Procfile --force
    git commit -m "Add Heroku Procfile"
    git push heroku master
    heroku ps:scale worker=1
    ```
* View logs: `heroku logs --tail`

#### Heroku Caveats
* Heroku dynos are [periodically restarted/cycled (~24 hourly)](https://devcenter.heroku.com/articles/dynos#automatic-dyno-restarts)
  * This will cause a few seconds of downtime each time it occurs
  * Due to Heroku's [ephemeral filesystem](https://devcenter.heroku.com/articles/dynos#ephemeral-filesystem) the list of already commented on submissions will be wiped, this makes `MAX_COMMENTS_PER_SUBMISSION` more of a daily limit

## Notes
* Currently all regexes are case insensitive (`re.I`)
* Don't actually use example_bot_config.json it's just a silly example
* Don't reuse/duplicate patchesohoulihanbot
