#!/usr/bin/env python3
"""Visionary server for generating inline previews
Usage:
    visionary.py start [-i <image_dir>]
                       [-w <worker_tasks>]
                       [-l <chat_name>]
                       [-r <reply_chat>]
                       <token>

Arguments:
    token                               VK API Token

Options:
    -h --help                           Show this screen
    --version                           Show version
    -i --image-dir <image_dir>          Directory to store images to [default: img]
    -w --workers <worker_tasks>         Concurrent worker tasks to run [default: 5]
    -l --listen-to <chat_name>          Chat name to listen to [default: TEST_DLG]
    -r --reply-to <reply_chat>          Chat name to reply to.

"""
import sys
import os

from docopt import docopt
from pprint import pprint as pp
from logbook import StreamHandler, RotatingFileHandler

if __name__ == '__main__':
    args = docopt(__doc__, version='0.9')
    pp(args)

    if args['start']:
        import asyncio
        from visionary.server import VisionServer

        if not os.path.exists(args['--image-dir']):
            os.makedirs(args['--image-dir'])

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        StreamHandler(sys.stdout, level='DEBUG', bubble=True).push_application()
        RotatingFileHandler('vision.log', backup_count=10, level='DEBUG', bubble=True).push_application()

        server = VisionServer(
            token=args['<token>'],
            chat_name=args['--listen-to'],
            reply_chat_name=args['--reply-to'],
            image_path=args['--image-dir'],
            workers=int(args['--workers'])
        )
        server.start()
