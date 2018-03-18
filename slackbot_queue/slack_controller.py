import os
import re
import time
import logging
from pprint import pprint
from collections import defaultdict
from slackclient import SlackClient

logger = logging.getLogger(__name__)


class Parser:

    def __init__(self):
        self.message_listener = defaultdict(list)
        self.reaction_added_listener = defaultdict(list)

    def trigger(self, *args, **kwargs):
        event_type = args[0]
        # Pass all other ars to the function (using [1:] to exclude the event_type)
        if event_type == 'message':
            return self._message(*args[1:], **kwargs)
        elif event_type == 'reaction_added':
            return self._reaction_added(*args[1:], **kwargs)

    def _message(self, regex_str, flags=0):
        def wrapper(func):
            parse_with = re.compile(regex_str, flags)
            self.message_listener[func].append(parse_with)
            logger.info("Registered listener `{func_name}` to regex `{regex_str}`".format(func_name=func.__name__,
                                                                                          regex_str=regex_str))
            return func

        return wrapper

    def _reaction_added(self, reaction_regex, message_regex='.*', flags=0):
        def wrapper(func):
            reaction_parse = re.compile(reaction_regex, flags)
            message_parse = re.compile(message_regex, flags)
            self.reaction_added_listener[func].append({'reaction': reaction_parse,
                                                       'message': message_parse})
            logger.info("Registered listener `{func_name}` to regex `{reaction_regex}` & {message_regex}"
                        .format(func_name=func.__name__,
                                reaction_regex=reaction_regex,
                                message_regex=message_regex,
                                ))
            return func

        return wrapper

    def parse_message(self, message_str, **kwargs):
        for callback in self.message_listener:
            for command in self.message_listener[callback]:
                result = re.search(command, message_str)
                if result is not None:
                    if len(result.groupdict().keys()) != 0:
                        rdata = callback(message_str, **result.groupdict(), **kwargs)
                    else:
                        rdata = callback(message_str, *result.groups(), **kwargs)

                    return rdata

    def parse_reaction(self, reaction_str, message_str, **kwargs):
        for callback in self.reaction_added_listener:
            for command in self.reaction_added_listener[callback]:
                reaction_result = re.search(command['reaction'], reaction_str)
                message_result = re.search(command['message'], message_str)
                if reaction_result is not None and message_result is not None:
                    # BUG: Both regexes need to use named groups or normal groups, cnnot be mixed
                    if len(reaction_result.groupdict().keys()) != 0:
                        rdata = callback(reaction_str, message_str, **reaction_result.groupdict(), **message_result.groupdict(), **kwargs)
                    else:
                        rdata = callback(reaction_str, message_str, *reaction_result.groups(), *message_result.groups(), **kwargs)

                    return rdata


class SlackController:

    def __init__(self):
        self.Parser = Parser

    def add_commands(self, channel_commands):
        for channel, commands in channel_commands.items():
            for command in commands:
                self.channel_to_actions[channel].append(command(self))

    def _setup_client(self):
        # Do not have this in __init__ because this is not needed when running tests
        self.slack_client = SlackClient(os.environ.get('SLACK_BOT_TOKEN'))
        self.channels = self._get_channel_list()
        self.channels.update(self._get_group_list())
        self.users = self._get_user_list()
        self.ims = self._get_im_list()
        self.BOT_ID = self.slack_client.api_call('auth.test')['user_id']
        self.BOT_NAME = '<@{}>'.format(self.BOT_ID)
        self.channel_to_actions = defaultdict(list)  # Filled in by the user

    def start_listener(self):
        self._setup_client()
        RTM_READ_DELAY = 1  # 1 second delay between reading from RTM

        if self.slack_client.rtm_connect(with_team_state=False):
            logger.info("Starter Bot connected and running!")

            while True:
                self.parse_event(self.slack_client.rtm_read())
                time.sleep(RTM_READ_DELAY)
        else:
            logger.error("Connection failed. Exception traceback printed above.")

    def parse_event(self, slack_events):
        """
            Parses a list of events coming from the Slack RTM API to find bot commands.
            If a bot command is found, this function returns a tuple of command and channel.
            If its not found, then this function returns None, None.
        """
        for event in slack_events:
            pprint(event)
            try:
                if event['type'] == 'message' and not 'subtype' in event:
                    self.handle_message_event(event)
                elif event['type'] in ['reaction_added']:
                    self.handle_reaction_event(event)
                else:
                    # Can handle other things like reactions and such
                    pass
            except Exception:
                logger.exception("Failed to parse event: {event}".format(event=event))

    def handle_reaction_event(self, reaction_event):
        # Get the mesage that the reaction was added to
        full_data = {'reaction': reaction_event,
                     'user': self._get_user_data(reaction_event['user']),
                     }

        if reaction_event['item']['type'] == 'message':
            full_data['message'] = self.slack_client.api_call(**{'method': 'conversations.history',
                                                                 'channel': reaction_event['item']['channel'],
                                                                 'limit': 1,
                                                                 'inclusive': True,
                                                                 'latest': reaction_event['item']['ts'],
                                                                 'oldest': reaction_event['item']['ts'],
                                                                 })['messages'][0]

        elif reaction_event['item']['type'] == 'file':
            full_data['file'] = self.slack_client.api_call(**{'method': 'files.info',
                                                              'file': reaction_event['item']['file'],
                                                              })['file']

        # Add channel data
        for _ in range(1):
            try:
                # If its a reaction on a message
                channel_id = full_data['reaction']['item']['channel']
            except Exception: pass
            else: break  # It worked

            try:
                # If its a reaction on an uploaded file to a dm/private channel
                channel_id = full_data['file']['ims'][0]
            except Exception: pass
            else: break  # It worked

            try:
                # If its a reaction on an uploaded file to a public channel
                channel_id = full_data['file']['channels'][0]
            except Exception: pass
            else: break  # It worked

        full_data['channel'] = self._get_channel_data(channel_id)

        # Do not ever trigger its self
        # Only parse the message if the message came from a channel that has commands in it
        if full_data['user']['id'] != self.BOT_ID and (self.channel_to_actions.get('__all__') is not None or
                                                       self.channel_to_actions.get(full_data['channel']['name']) is not None):
            response = {'channel': full_data['channel']['id'],
                        'as_user': True,  # Should not be changed
                        'method': 'chat.postMessage',
                        }

            # Get all commands in channel
            all_channel_commands = self.channel_to_actions.get(full_data['channel']['name'], [])
            # All commands that are in ALL channels
            all_channel_commands += self.channel_to_actions.get('__all__', [])

            parsed_response = None
            for command in all_channel_commands:
                message_text = ' uploaded a file '
                if full_data.get('message') is not None:
                    message_text = full_data['message']['text']

                parsed_response = command.parser.parse_reaction(full_data['reaction']['reaction'],
                                                                message_text,
                                                                full_event=full_data)
                if parsed_response is not None:
                    response.update(parsed_response)
                    break

            if parsed_response is not None:
                # Only post a message if needed
                self.slack_client.api_call(**response)

    def handle_message_event(self, message_event):
        """
            Executes bot command if the command is known
        """
        channel_data = self._get_channel_data(message_event['channel'])
        user_data = self._get_user_data(message_event['user'])

        full_data = {'channel': channel_data,
                     'message': message_event,
                     'user': user_data,
                     }

        # Do not ever trigger its self
        # Only parse the message if the message came from a channel that has commands in it
        if full_data['user']['id'] != self.BOT_ID and (self.channel_to_actions.get('__all__') is not None or
                                                       self.channel_to_actions.get(channel_data['name']) is not None):
            response = {'channel': channel_data['id'],  # Should not be changed
                        'as_user': True,  # Should not be changed
                        'method': 'chat.postMessage',
                        }
            if message_event.get('thread_ts') is not None:
                response['thread_ts'] = message_event.get('thread_ts')

            is_help_message = False
            help_messages_text = ['{bot_name} help'.format(bot_name=self.BOT_NAME),
                                  'help',
                                  ]
            if full_data['message']['text'] in help_messages_text:
                is_help_message = True

            # Get all commands in channel
            all_channel_commands = self.channel_to_actions.get(channel_data['name'], [])
            # All commands that are in ALL channels
            all_channel_commands += self.channel_to_actions.get('__all__', [])

            parsed_response = None
            for command in all_channel_commands:
                if is_help_message is False:
                    parsed_response = command.parser.parse_message(full_data['message']['text'],
                                                                   full_event=full_data)
                    if parsed_response is not None:
                        response.update(parsed_response)
                        break
                else:
                    try:
                        parsed_response = command.help().get('attachments')
                        if parsed_response is not None:
                            response['method'] = 'chat.postEphemeral'
                            response['user'] = full_data['user']['id']
                            response['text'] = 'Here are all the commands available in this channel'
                            response['attachments'].extend(parsed_response)
                    except AttributeError as e:
                        logger.error("Missing help function in class: {e}".format(e=e))

            if parsed_response is not None:
                # Only post a message if needed
                self.slack_client.api_call(**response)

    def _get_channel_data(self, channel):
        channel_data = None
        for _ in range(2):
            if channel in self.channels:
                channel_data = self.channels[channel]

            elif channel in self.ims:
                channel_data = self.ims[channel]
                channel_data['name'] = '__direct_message__'

            if channel_data is not None:
                break
            else:
                # Refresh both channels and ims and try again
                self.reload_channel_list()
                self.reload_im_list()

        return channel_data

    def _get_user_data(self, user):
        try:
            user_data = self.users[user]
        except KeyError:
            self.reload_user_list()
        finally:
            user_data = self.users[user]

        return user_data

    def _get_channel_list(self):
            channels_call = self.slack_client.api_call("channels.list", exclude_archived=1)
            if channels_call['ok']:
                by_id = {item['id']: item for item in channels_call['channels']}
                by_name = {item['name']: item for item in channels_call['channels']}
                return {**by_id, **by_name}

    def _get_group_list(self):
        groups_call = self.slack_client.api_call("groups.list", exclude_archived=1)
        if groups_call['ok']:
            by_id = {item['id']: item for item in groups_call['groups']}
            by_name = {item['name']: item for item in groups_call['groups']}
            return {**by_id, **by_name}

    def _get_user_list(self):
        users_call = self.slack_client.api_call("users.list")
        if users_call['ok']:
            by_id = {item['id']: item for item in users_call['members']}
            by_name = {item['name']: item for item in users_call['members']}
            return {**by_id, **by_name}

    def _get_im_list(self):
        ims_call = self.slack_client.api_call("im.list")
        if ims_call['ok']:
            return {item['id']: item for item in ims_call['ims']}

    def reload_channel_list(self):
        self.channels = self._get_channel_list()
        self.channels.update(self._get_group_list())

    def reload_im_list(self):
        self.ims = self._get_im_list()

    def reload_user_list(self):
        self.users = self._get_user_list()
