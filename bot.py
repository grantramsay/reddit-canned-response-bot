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
import praw.models
import psaw
import re
import sys
import time
import typing
from typing import Dict, List, Optional, Union
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

    def get_response(self, comment: str) -> Optional[str]:
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
    def __init__(self, canned_responses: List[CannedResponse],
                 comment_mention_reply: Optional[str]=None, postfix: str = ''):
        self.canned_responses = canned_responses
        self.postfix = postfix
        self.comment_mention_reply = comment_mention_reply
        self.search_keys = set(itertools.chain.from_iterable(r.search_keys for r in canned_responses))

    def get_response(self, comment: str) -> Optional[str]:
        for canned_response in self.canned_responses:
            response = canned_response.get_response(comment)
            if response is not None:
                return response + self.postfix
        return None

    # noinspection PyUnusedLocal
    def get_comment_mention_response(self, comment: str) -> Optional[str]:
        if self.comment_mention_reply:
            return self.comment_mention_reply + self.postfix
        return None


class Bot:
    class SubredditInfo:
        def __init__(self, name: str, first_query_time_utc: int):
            self.name = name
            self.next_query_time_utc = first_query_time_utc

    def __init__(self, pushshift: psaw.PushshiftAPI, reply_generator: ReplyGenerator,
                 subreddit_names: List[str], dry_run: bool = False, start_time_subtract_hours: int = 0):
        self.pushshift = pushshift
        self.praw = pushshift.r  # type: praw.Reddit
        self.reply_generator = reply_generator
        first_query_time_utc = int(datetime.utcnow().replace(tzinfo=timezone.utc).timestamp() -
                                   start_time_subtract_hours * 3600)
        self.subreddits = list(Bot.SubredditInfo(name, first_query_time_utc) for name in subreddit_names)
        self.dry_run = dry_run
        self.bot_username = self.praw.config.username
        self.search_query = '|'.join(reply_generator.search_keys)
        self._load_commented_items()

    def _load_commented_items(self):
        # Keeps track of what we've recently commented on to avoid duplicate/spamming responses.
        self.commented_items_filename = self.bot_username + '_commented_items.json'
        commented_items = {}
        if os.path.isfile(self.commented_items_filename):
            with open(self.commented_items_filename) as f:
                commented_items = json.load(f)  # type: Dict[str, typing.Any]
        self.replied_to_comments = collections.deque(
            commented_items.get('replied_to_comments', []), maxlen=1000)
        self.commented_submissions = collections.deque(
            commented_items.get('commented_submissions', []), maxlen=1000)

    def _append_commented_items(self, comment):
        self.replied_to_comments.append(str(comment.id))
        self.commented_submissions.append(str(comment.submission.id))
        with atomic_write(self.commented_items_filename, overwrite=True) as f:
            dump = json.dumps({'replied_to_comments': list(self.replied_to_comments),
                               'commented_submissions': list(self.commented_submissions)}, indent=2)
            f.write(dump)

    def _check_and_handle_inbox(self) -> bool:
        max_messages_per_request = 25  # <= 100
        all_unread_messages = list(self.praw.inbox.unread(limit=max_messages_per_request))

        for comment in [msg for msg in all_unread_messages
                        if isinstance(msg, praw.models.Comment)]:
            self._handle_comment_mention(comment)

        for message in [msg for msg in all_unread_messages
                        if isinstance(msg, praw.models.Message)]:
            self._handle_direct_message(message)

        self.praw.inbox.mark_read(all_unread_messages)

        handled_all_messages = len(all_unread_messages) < max_messages_per_request
        return handled_all_messages

    def _scrape_and_handle_comments(self) -> bool:
        max_comments_per_request = 500  # <= 500
        handled_all_comments = True

        for subreddit in self.subreddits:
            comments = list(
                self.pushshift.search_comments(
                    q=self.search_query,
                    # Special handling for r/all as it's not a "real" subreddit
                    subreddit=subreddit.name if subreddit.name != 'all' else None,
                    after=subreddit.next_query_time_utc,
                    sort="asc",
                    sort_type="created_utc",
                    limit=max_comments_per_request))

            if comments:
                log.debug("Received {} comments after utc {} for r/{}".format(
                    len(comments), subreddit.next_query_time_utc, subreddit.name))

                if len(comments) == max_comments_per_request:
                    handled_all_comments = False

                # Update next request time to time of the latest comment received
                subreddit.next_query_time_utc = int(comments[-1].created_utc)

                for comment in comments:
                    self._handle_scraped_comment(comment)

        return handled_all_comments

    def _handle_scraped_comment(self, comment: praw.models.Comment):
        response = self.reply_generator.get_response(comment.body)
        if response and self._can_reply_to_comment(comment):
            log.info('Replying to comment: {}\n\n{}'.format(
                self._comment_log_txt(comment), response))
            self._send_reply(comment, response)

    def _handle_comment_mention(self, comment: praw.models.Comment):
        response = self.reply_generator.get_comment_mention_response(comment.body)
        if response and self._can_reply_to_comment(comment):
            log.info('Replying to comment mention: {}\n\n{}'.format(
                self._comment_log_txt(comment), response))
            self._send_reply(comment, response)

    def _handle_direct_message(self, message: praw.models.Message):
        # Direct messages are not currently handled.
        pass

    def _can_reply_to_comment(self, comment: praw.models.Comment) -> bool:
        if not comment.author or comment.author == self.bot_username:
            # Don't reply to yourself...
            return False

        if str(comment.id) in self.replied_to_comments:
            log.info('Already replied to comment, ignoring: {}'.format(self._comment_log_txt(comment)))
            return False
        if self.commented_submissions.count(str(comment.submission.id)) >= MAX_COMMENTS_PER_SUBMISSION:
            log.info('Max submission replies ({}) hit, ignoring: {}'.format(
                MAX_COMMENTS_PER_SUBMISSION, self._comment_log_txt(comment)))
            return False
        return True

    def _send_reply(self, comment: Union[praw.models.Comment, praw.models.Message], response: str):
        # Keep track of what we've recently commented on to avoid duplicate/spamming responses.
        self._append_commented_items(comment)

        if not self.dry_run:
            # Post actual reddit comment
            comment.reply(response)

    @staticmethod
    def _comment_log_txt(comment: praw.models.Comment) -> str:
        if hasattr(comment, 'permalink'):
            return 'https://www.reddit.com{} {}'.format(comment.permalink, comment.body)
        return '{}-{}-{} {}'.format(comment.subreddit.display_name, comment.submission.id, comment.id, comment.body)

    def run(self):
        while True:
            try:
                handled_all_messages = self._check_and_handle_inbox()
                handled_all_comments = True
                if self.search_query:
                    handled_all_comments = self._scrape_and_handle_comments()
            except Exception as e:
                log.warning('Crash!!\n{}'.format(e))
                ratelimit = parse.search('try again in {:d} minutes', str(e))
                if ratelimit:
                    log.info("Sleeping for ratelimit of {} minutes".format(ratelimit[0]))
                    time.sleep(ratelimit[0] * 60)
            else:
                # Slow down requests if we've caught up and processed all pending items/events
                if handled_all_messages and handled_all_comments:
                    time.sleep(30)


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
                            help='json bot config file (see examples/minimal_example_bot_config.json)')
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
        canned_responses, bot_config.get('comment_mention_reply', None), bot_config['postfix'])

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
    bot.run()


if __name__ == '__main__':
    main()
