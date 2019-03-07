#!/usr/bin/env python3
import argparse
from atomicwrites import atomic_write
import collections
from datetime import datetime, timezone
import itertools
import json
import logging
import logging.handlers
import os
import parse
import pathlib
import praw
import psaw
import re
import sys
import time
import typing
from typing import Dict, List, Optional
import unittest

MAX_COMMENTS_PER_SUBMISSION = 2

log = logging.getLogger('bot_log')
log.setLevel(logging.DEBUG)


class CannedResponse:
    def __init__(self, search_keys: List[str], comment_regexes: List[str], response: str,
                 ignore_regexes: Optional[List[str]] = None, max_chars: int = sys.maxsize):
        self.search_keys = search_keys
        self.comment_regexes = comment_regexes
        self.response = response
        if ignore_regexes is None:
            ignore_regexes = []
        self.ignore_regexes = ignore_regexes
        self.max_chars = max_chars

    def get_response(self, comment: str):
        # All regexes are currently case insensitive (re.I).
        if any(re.search(comment_regex, comment, re.I) for comment_regex in self.comment_regexes):
            if any(re.findall(ignore_regex, comment, re.I) for ignore_regex in self.ignore_regexes):
                log.debug('Skipping due to ignored regex\n{}'.format(comment))
                return None
            if len(comment) > self.max_chars:
                log.debug('Skipping due to max comment length {} > {}\n{}'.format(
                    len(comment), self.max_chars, comment))
                return None

            return self.response
        return None


class ReplyGenerator:
    def __init__(self, canned_responses: List[CannedResponse], main_account_username: str, postfix: str = ''):
        self.canned_responses = canned_responses
        self.postfix = postfix + ' (u/{})'.format(main_account_username)
        self.search_keys = set(itertools.chain.from_iterable(r.search_keys for r in canned_responses))

    def get_response(self, comment: str):
        for canned_response in self.canned_responses:
            response = canned_response.get_response(comment)
            if response is not None:
                return response + self.postfix
        return None


class Bot:
    class SubredditInfo:
        def __init__(self, name: str, first_query_time_utc: int):
            self.name = name
            self.next_query_time_utc = first_query_time_utc

    def __init__(self, pushshift: psaw.PushshiftAPI, reply_generator: ReplyGenerator,
                 subreddit_names: List[str], dry_run: bool = False, start_time_subtract_hours: int = 0):
        self.pushshift = pushshift
        self.reply_generator = reply_generator
        first_query_time_utc = int(datetime.utcnow().replace(tzinfo=timezone.utc).timestamp() -
                                   start_time_subtract_hours * 3600)
        self.subreddits = list(Bot.SubredditInfo(name, first_query_time_utc) for name in subreddit_names)
        self.dry_run = dry_run
        self.bot_username = pushshift.r.config.username
        self.search_query = '|'.join(reply_generator.search_keys)
        self._load_commented_items()

    def _load_commented_items(self):
        # Keeps track of what we've recently commented on to avoid duplicate/spamming responses.
        self.commented_items_filename = self.bot_username + '_commented_items.json'
        commented_items = {}
        if os.path.isfile(self.commented_items_filename):
            with open(self.commented_items_filename) as f:
                commented_items = json.load(f)  # type: Dict[str, typing.Any]
        self.replied_to_comments = collections.deque(commented_items.get('replied_to_comments', []), maxlen=1000)
        self.commented_submissions = collections.deque(commented_items.get('commented_submissions', []), maxlen=1000)

    def _append_commented_items(self, comment):
        self.replied_to_comments.append(str(comment.id))
        self.commented_submissions.append(str(comment.submission.id))
        with atomic_write(self.commented_items_filename, overwrite=True) as f:
            dump = json.dumps({'replied_to_comments': list(self.replied_to_comments),
                               'commented_submissions': list(self.commented_submissions)}, indent=2)
            f.write(dump)

    def run_once(self):
        for subreddit in self.subreddits:
            comments = list(
                self.pushshift.search_comments(
                    q=self.search_query,
                    subreddit=subreddit.name,
                    after=subreddit.next_query_time_utc,
                    sort="asc",
                    sort_type="created_utc",
                    limit=500))

            if comments:
                log.debug("Received {} comments after utc {} for r/{}".format(
                    len(comments), subreddit.next_query_time_utc, subreddit.name))

                # Update next request time to time of the latest comment received
                subreddit.next_query_time_utc = int(comments[-1].created_utc)

                for comment in comments:
                    self.handle_comment(comment)

    def handle_comment(self, comment):
        if not comment.author or comment.author == self.bot_username:
            return

        response = self.reply_generator.get_response(comment.body)
        if response is not None:
            log_txt = 'https://www.reddit.com{} {}'.format(comment.permalink, comment.body)
            if str(comment.id) in self.replied_to_comments:
                log.info('Already replied to comment, ignoring: {}'.format(log_txt))
                return
            if self.commented_submissions.count(str(comment.submission.id)) >= MAX_COMMENTS_PER_SUBMISSION:
                log.info('Max submission replies ({}) hit, ignoring: {}'.format(MAX_COMMENTS_PER_SUBMISSION, log_txt))
                return

            # Keep track of what we've recently commented on to avoid duplicate/spamming responses.
            self._append_commented_items(comment)

            log.info('Replying to comment: {}\n\n{}'.format(log_txt, response))
            if not self.dry_run:
                # Post actual reddit comment
                comment.reply(response)

    def run_forever(self):
        while True:
            try:
                self.run_once()
            except Exception as e:
                log.warning('Crash!!\n{}'.format(e))
                ratelimit = parse.search('try again in {:d} minutes', str(e))
                if ratelimit:
                    log.info("Sleeping for ratelimit of {} minutes".format(ratelimit[0]))
                    time.sleep(ratelimit[0] * 60)


class BotTests(unittest.TestCase):
    def __init__(self, tests: List[Dict[str, str]], reply_generator: ReplyGenerator):
        super(BotTests, self).__init__(methodName='runTest')
        self.tests = tests
        self.reply_generator = reply_generator

    def runTest(self):
        for test in self.tests:
            reply = test['reply']
            if reply is not None:
                reply += self.reply_generator.postfix
            self.assertEqual(reply, self.reply_generator.get_response(test['comment']), test['comment'])


def main():
    arg_parser = argparse.ArgumentParser(
        description='Reddit canned response bot')
    arg_parser.add_argument(dest='bot_config_file', type=str,
                            help='json bot config file (see example_bot_config.json)')
    arg_parser.add_argument(dest='main_account_username', type=str,
                            help='Your main account username that will be appended to the bots name.'
                            ' I.e. "jeff" for u/jeff')
    arg_parser.add_argument('--dry-run', dest='dry_run', type=int, const=0, default=None, nargs='?',
                            help='Doesn\'t actually reply, just prints what it would\'ve sent.'
                                 ' A number of hours prior to "now" may also be supplied to '
                                 'iterate over old comments first e.g. "--dry-run=168"')
    arg_parser.add_argument('--verbose', dest='verbose', action='store_true',
                            help='Display additional debug messages')
    arg_parser.add_argument('--skip-tests', dest='skip_tests', action='store_true', help='Skips tests')
    arguments = arg_parser.parse_args()

    # Reddit credentials should be supplied via praw.ini file.
    reddit = praw.Reddit()
    pushshift = psaw.PushshiftAPI(reddit)

    # Setup logging to stdout and rotating files
    log_stream_handler = logging.StreamHandler(sys.stdout)
    log_stream_handler.setLevel(logging.DEBUG if arguments.verbose else logging.INFO)
    log_stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    log.addHandler(log_stream_handler)
    # Rotate file log through 3 * 10MiB files
    log_file_handler = logging.handlers.RotatingFileHandler(
        pathlib.Path(reddit.config.username).with_suffix('.log'),
        maxBytes=10*1048576, backupCount=2)
    log_file_handler.setLevel(logging.DEBUG)
    log_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    log.addHandler(log_file_handler)

    with open(arguments.bot_config_file) as f:
        bot_config = json.load(f)  # type: Dict[str, typing.Any]

    canned_responses = [CannedResponse(**kwargs) for kwargs in bot_config['canned_responses']]
    reply_generator = ReplyGenerator(
        canned_responses, arguments.main_account_username, bot_config['postfix'])

    tests = bot_config.get('tests', None)
    if tests and not arguments.skip_tests:
        # Run tests
        log.setLevel(logging.WARNING)  # Hide test log output
        suite = unittest.TestSuite()
        suite.addTest(BotTests(tests, reply_generator))
        unittest.TextTestRunner().run(suite)
        log.setLevel(logging.DEBUG)  # Restore log output

    dry_run = arguments.dry_run is not None
    start_time_subtract_hours = 0 if arguments.dry_run is None else arguments.dry_run
    bot = Bot(pushshift, reply_generator, bot_config['subreddits'], dry_run=dry_run,
              start_time_subtract_hours=start_time_subtract_hours)
    bot.run_forever()


if __name__ == '__main__':
    main()
