# Reddit Canned Response Bot
Simple Python 3 based bot to send canned responses to specific phrases.

Uses [praw](https://praw.readthedocs.io/en/latest/) and [pushshift](https://github.com/pushshift/api) ([psaw](https://github.com/dmarx/psaw)) to continually get new comments containing specific keywords, then regular expressions for further filtering. Sends a canned response if filter matches.

### Before you do anything
* Read the [Reddit bottiquette rules](https://www.reddit.com/r/Bottiquette/wiki/bottiquette)

### Setup
* Create a Reddit account for bot and [create an authorised app](https://www.reddit.com/prefs/apps/) if you haven't already
* Install python requirements `pip3 install -r requirements.txt`
* Create a [praw.ini](https://praw.readthedocs.io/en/latest/getting_started/configuration/prawini.html) file and fill out bot details (see example_praw.ini)
* Create a mybotname_config.json bot configuration file (see example_bot_config.json)
  * Remembering to put your main account name in the postfix!
  
### Usage
* View options
  * `./bot.py -h`
* Run bot
  * `./bot.py mybotname_config.json mymainaccountname`
* Dry run (print replies without actually sending)
  * `./bot.py mybotname_config.json mymainaccountname --dry-run`
* Dry run starting from comments made one week (24*7=168 hours) ago
  * `./bot.py mybotname_config.json mymainaccountname --dry-run=168`

## Notes
* Currently all regexes are currently case insensitive (`re.I`)
* Don't actually use example_bot_config.log it's just a silly example
* Don't reuse/duplicate patchesohoulihanbot
