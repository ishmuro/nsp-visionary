#!/usr/bin/env python
"""Visionary server for generating inline previews
Usage:
    visionary.py start [-i <image_path>]
                       [-c <chrome_path>]
                       [-w <driver_path>]
                       [-n <chat_name>]
                       [-t <worker_tasks>]
                       [--cache-size <cache_size>]
                       <token>

Arguments:
    token                               VK API Token

Options:
    -h --help                           Show this screen
    --version                           Show version
    -i --image-dir <image_path>         Directory to store images to [default: ./img]
    -c --chrome-path <chrome_path>      Path to Chrome binary [default: /usr/bin/google-chrome]
    -w --webdriver-path <driver_path>   Path to Chrome Selenium webdriver [default: /usr/bin/chromedriver-dev]
    -n --chat-name <chat_name>          Chat name to listen to [default: TEST_DLG]
    -t --tasks <worker_tasks>           Concurrent worker tasks to run [default: 5]
    --cache-size <cache_size>           Cache size [default: 5000]

"""
import sys

from docopt import docopt
from pprint import pprint as pp
from logbook import StreamHandler

if __name__ == '__main__':
    args = docopt(__doc__, version='0.8')
    pp(args)

    if args['start']:
        import asyncio
        from visionary.server import VisionServer

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        StreamHandler(sys.stdout, level='DEBUG').push_application()

        serv = VisionServer(
            token=args['<token>'],
            chat_name=args['--chat-name'],
            binary_path=args['--chrome-path'],
            driver_path=args['--webdriver-path'],
            image_path=args['--image-dir'],
            workers=int(args['--tasks'])
        )
        serv.start()
