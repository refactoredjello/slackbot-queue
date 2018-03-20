# Slackbot Queue

Slackbot with a celery queue for long running tasks


### Install
`pip install slackbot-queue`  


### Usage

```python
from slackbot_queue import slack_controller, queue

from example import Example  # Import the example command class

# Set up the celery configs
queue.conf.task_default_queue = 'custom_slackbot'
queue.conf.broker_url = 'amqp://guest:guest@localhost:5672//'

# The token could also be set by the env variable SLACK_BOT_TOKEN
slack_controller.setup(slack_bot_token='xxxxxxxx')

# Set up the command by passing in the slack_controller to it
ex = Example(slack_controller)

# Set up the example command to only work in the `general` channel or as a direct message
slack_controller.add_commands({'__direct_message__': [ex],
                               '__all__': [],
                               'general': [ex],
                               })

# Either start the listener
slack_controller.start_listener()

# Or the worker:
# The argv list is celery arguments used to start the worker
slack_controller.start_worker(argv=['celery', 'worker', '--concurrency', '1', '-l', 'info'])
```

A full example can be found in the `example` dir.
