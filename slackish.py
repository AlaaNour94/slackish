import re
import shlex
import logging

import slack

from threading import Lock
from slack import WebClient, RTMClient

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class SingletonMeta(type):
    """This is a thread-safe implementation of Singleton."""

    _instance = None

    _lock = Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if not cls._instance:
                cls._instance = super().__call__(*args, **kwargs)
        return cls._instance


class Command:

    registry = dict()

    def __init__(self, fn):
        self.registry[fn.__name__] = {'cmd': fn, 'argnames': fn.__code__.co_varnames, 'help': fn.__doc__}
        self._fn = fn

    def __call__(self, *args, **kwargs):
        self._fn(*args, **kwargs)


class Slackish(metaclass=SingletonMeta):

    message_queue = []
    default_error_message = "Something went wrong!"

    def __init__(self, registry, **kwargs):
        self.token = kwargs.get('SLACK_BOT_TOKEN')
        self.MENTION_REGEX = kwargs.get('MENTION_REGEX', "^<@(|[WU].+?)>(.*)")
        self.BOT_ID = kwargs.get('BOT_ID')
        self.registry = registry
        self.web_client = None
        self.rtm_client = None

    @classmethod
    def send(cls, message):
        """enqueue message in Slackish print queue"""
        cls.message_queue.append(message)

    def parse_bot_commands(self, slack_event):
        """
            Parses a list of events coming from the Slack RTM API to find bot commands.
            If a bot command is found, this function returns a tuple of command and channel.
            If its not found, then this function returns None, None.
        """
        if "subtype" not in slack_event:
            user_id, message = self.parse_direct_mention(slack_event["text"])
            if user_id == self.BOT_ID:
                return message, slack_event["channel"]
        return None, None

    def parse_direct_mention(self, message_text):
        """
            Finds a direct mention (a mention that is at the beginning) in message text
            and returns the user ID which was mentioned. If there is no direct mention, returns None
        """
        matches = re.search(self.MENTION_REGEX, message_text)
        # the first group contains the username, the second group contains the remaining message
        return (matches.group(1), matches.group(2).strip()) if matches else (None, None)

    def serve(self):
        if not self.BOT_ID:
            self.BOT_ID = WebClient(token=self.token).api_call("auth.test")["user_id"]
        logger.info("BOT serving loop started")
        RTMClient(token=self.token).start()

    def list_to_dict(self, alist):
        """convert a list to a dictionary"""
        it = iter(alist)
        return dict(zip(it, it))

    def command_to_fn_call(self, command):
        logger.debug("converting command to function call!")

        command_words = shlex.split(command)
        command_key = command_words[0].lower()
        logger.debug(f"command key is {command_key}")
        try:
            cmd_function = self.registry[command_key]['cmd']
            logger.info(f'Executing command {command_key}!')
            kwargs = self.list_to_dict(command_words[1:])
            cmd_function(**kwargs)

        except KeyError as KE:
            logger.debug(f"Command Key {command_key} not found")
            logger.debug(f"Registery: {self.registry}")
            logger.debug(KE)
            self.error("Invalid command!")
            self.cmd_help()

    def cmd_help(self, command=None):
        if command:
            self.post(self.registry[command]['help'])
        else:
            for cmd in self.registry:
                self.post(self.registry[cmd]['help'])

    def post(self, message):
        self.web_client.chat_postMessage(channel=self.channel, text=message)

    def error(self, error_message):
        self.web_client.chat_postMessage(
            channel=self.channel,
            text=f"Error: {(error_message or self.default_error_message)}",
        )

    def flush(self, message_queue):
        for message in message_queue:
            self.post(message)

    @slack.RTMClient.run_on(event='message')
    def handle(**payload):
        print(payload)
        bot = Slackish()

        command, channel = bot.parse_bot_commands(payload['data'])
        if not command:
            return

        bot.channel = channel
        bot.web_client = payload['web_client']
        bot.rtm_client = payload['rtm_client']

        logger.info(f"BOT received {command} from channel: {channel}")
        logger.info(f"handeling command: {command}")

        try:
            bot.command_to_fn_call(command)
        except Exception as e:
            logger.exception(f"Error {e} while excuting command {command}")
            bot.post("I'm sorry, I don't understand! ")
            bot.cmd_help()

        bot.flush(Slackish.message_queue)
        Slackish.message_queue = []
