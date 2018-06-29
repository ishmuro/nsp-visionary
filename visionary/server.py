import asyncio
import aioredis
import functools
import traceback
from logbook import Logger
from typing import Coroutine
from furl.furl import furl

from aiovk.exceptions import VkCaptchaNeeded

from visionary.vkapi import VKAPIHandle
from visionary.webclient_puppet import PuppetClient
from visionary.util import find_link_br
from visionary.config import EMOJI, REDIS_URI


class VisionServer(object):
    _server_task_pool = []
    _aux_tasks = []
    _redis = None

    def __init__(
            self,
            loop: asyncio.BaseEventLoop,
            workers: int,
            token: str,
            chat_name: str,
            image_path: str,
            reply_chat_name: str=None
    ):
        self._workers = workers

        # Fix trailing slash if not present
        if image_path[:-1] != '/':
            image_path += '/'

        self._log = Logger('VServer')
        self._aioloop = loop
        self._vkapi = VKAPIHandle(
            self._aioloop,
            token,
            listen_chat_name=chat_name,
            reply_chat_name=reply_chat_name or None
        )
        self._web = PuppetClient(image_path, workers)

    async def _execute_blocking(self, func, *args, **kwargs) -> Coroutine:
        """
        Execute blocking function in separate thread
        Args:
            func: function object to be called
            *args: positional arguments
            **kwargs: keyword arguments

        Returns:
            Asyncio task corresponding to the created coroutine
        """
        self._log.debug(f"Scheduling function {func.__name__}{args} call to separate thread")
        return await self._aioloop.run_in_executor(executor=None, func=functools.partial(func, *args, **kwargs))

    def _queue_task(self, func, *args, **kwargs) -> Coroutine:
        """
        Queue async task to be executed in the loop
        Args:
            func: function to be called
            *args: positional arguments
            **kwargs: keyword arguments

        Returns:
            asyncio task corresponding to the created coroutine
        """
        self._log.debug(f"Scheduling function {func.__name__}{args} to loop queue")
        task = asyncio.ensure_future(func(*args, **kwargs))
        self._aux_tasks.append(task)
        return task

    async def _process(self):
        """Main server coroutine"""
        try:
            async for message in self._vkapi.wait_for_messages():
                link_string = find_link_br(message)
                if not link_string:
                    continue  # Nothing to do here

                link = furl(link_string)

                self._log.info(f"Found link in message: {link.url}")
                message_task = self._queue_task(
                    self._vkapi.api_send_msg,
                    text=f"{EMOJI['process']} {link.url}")

                resolved = await self._web.process_link(link)

                try:
                    message_id = await message_task
                except VkCaptchaNeeded:
                    self._log.error('Captcha kicked in, unable to proceed')
                    raise RuntimeError('Captcha')

                # Link could not open
                if resolved is None:
                    self._log.warn('Skipping link')
                    self._queue_task(
                        self._vkapi.api_edit_msg,
                        msg_id=message_id,
                        text=f"{EMOJI['timeout']} {link.url}"
                    )
                    continue

                # Link is a file
                if resolved.time_taken == -1:
                    self._log.info(f"Treated {link.url} as a file link.")
                    self._queue_task(
                        self._vkapi.api_edit_msg,
                        msg_id=message_id,
                        text=f"{EMOJI['package']} {resolved.location}"
                    )
                    continue

                # Link has redirects
                if link.host != resolved.location.host:
                    message_text = f"{EMOJI['processed']} {resolved.redirect_path} ({resolved.time_taken:.2f}s)"
                    self._queue_task(
                        self._vkapi.api_edit_msg,
                        msg_id=message_id,
                        text=message_text,
                    )
                else:
                    message_text = f"{EMOJI['processed']} {link.url} ({resolved.time_taken:.2f}s)"
                    self._queue_task(
                        self._vkapi.api_edit_msg,
                        msg_id=message_id,
                        text=message_text,
                    )

                # Could take snapshot
                if resolved.snapshot:
                    photo_id = await self._vkapi.api_upload_photo(resolved.snapshot)
                    self._queue_task(
                        self._vkapi.api_edit_msg,
                        msg_id=message_id,
                        text=message_text,
                        attachment=photo_id
                    )
                else:
                    self._log.warn(f"No snapshot available for {link.url}")

                self._redis.execute('zadd')

        except asyncio.CancelledError:
            self._log.info('Longpoll task cancelled')
            return

    def start(self):
        # Initialize components first
        self._aioloop.run_until_complete(self._vkapi.register())
        self._aioloop.run_until_complete(self._web.start())
        self._redis = self._aioloop.run_until_complete(aioredis.create_connection(REDIS_URI, loop=self._aioloop))

        try:
            self._server_task_pool = [asyncio.ensure_future(self._process(), loop=self._aioloop) for _ in range(self._workers)]
            pool = asyncio.gather(*self._server_task_pool)
            self._aioloop.run_until_complete(pool)

        except KeyboardInterrupt:
            self._log.warn('Being shut down by keyboard interrupt or SIGINT')
        except RuntimeError as e:
            self._log.warn(f"Being shut down by server error: {e}")
            self._log.warn(traceback.format_exc())
        except Exception as e:
            self._log.error(f"Execution failed miserably due to an unknown error: {e}")
            self._log.error(traceback.format_exc())
        finally:
            # Send cancel exception to all server tasks
            # for task in self._server_task_pool:
            #     task.cancel()
            pool.cancel()

            # Collect pending coroutines and wait for them to finish
            pending_tasks = [task for task in self._aux_tasks if not task.done()]

            if len(pending_tasks) > 0:
                self._log.info(f"Waiting for {len(pending_tasks)} pending tasks to finish")
                self._aioloop.run_until_complete(asyncio.gather(*pending_tasks))
                self._log.info('Pending tasks finished')

            # Wait for server tasks to wrap up
            self._aioloop.run_until_complete(pool)

            self._log.info('Stopping auxiliary services')
            self._aioloop.run_until_complete(self._vkapi.stop())
            self._aioloop.run_until_complete(self._web.stop())
            self._aioloop.close()

            self._log.warn('Server has been shut down!')
