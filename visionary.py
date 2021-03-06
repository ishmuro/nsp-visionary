#!/usr/bin/env python3
"""Visionary server for generating inline previews
Usage:
    visionary.py start [-i <image_dir>]
                       [-b <binary_path>]
                       [-d <driver_path>]
                       [-c <chat_name>]
                       [-w <worker_tasks>]
                       <token>

Arguments:
    token                               VK API Token

Options:
    -h --help                           Show this screen
    --version                           Show version
    -i --image-dir <image_dir>          Directory to store images to [default: img]
    -b --binary-path <binary_path>      Path to the Chrome binary [default: /usr/bin/google-chrome]
    -d --driver-path <driver_path>      Path to Chrome Selenium webdriver [default: /usr/bin/chromedriver-dev]
    -c --chat-name <chat_name>          Chat name to listen to [default: TEST_DLG]
    -w --workers <worker_tasks>         Concurrent worker tasks to run [default: 5]

"""
import sys
import os

from docopt import docopt
from pprint import pprint as pp
from logbook import StreamHandler

if __name__ == '__main__':
    args = docopt(__doc__, version='0.8')
    pp(args)

    if args['start']:
        import asyncio
        from visionary.server import VisionServer

        if not os.path.exists(args['--image-dir']):
            os.makedirs(args['--image-dir'])

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        StreamHandler(sys.stdout, level='DEBUG').push_application()

        serv = VisionServer(
            token=args['<token>'],
            chat_name=args['--chat-name'],
            binary_path=args['--binary-path'],
            driver_path=args['--driver-path'],
            image_path=args['--image-dir'],
            workers=int(args['--workers'])
        )
        serv.start()
