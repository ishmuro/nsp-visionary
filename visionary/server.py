import asyncio
import functools
from logbook import Logger
from typing import Coroutine
from furl.furl import furl

from aiovk.exceptions import VkCaptchaNeeded

from visionary.vkapi import VKAPIHandle
from visionary.webclient_puppet import PuppetClient
from visionary.util import find_link_br
from visionary.config import EMOJI


class VisionServer(object):
    _server_task_pool = []
    _aux_tasks = []

    def __init__(
            self,
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
        self._aioloop = asyncio.get_event_loop()
        self._vkapi = VKAPIHandle(
            self._aioloop,
            token,
            listen_chat_name=chat_name,
            reply_chat_name=reply_chat_name or None
        )
        self._web = PuppetClient(image_path, workers)

        self._webclient_lock = asyncio.Lock()

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
                link = find_link_br(message)
                if not link:
                    continue  # Nothing to do here

                self._log.info(f"Found link in message: {link}")
                message_task = self._queue_task(
                    self._vkapi.send_msg,
                    text=f"{EMOJI['process']} {link}")

                resolved = await self._web.process_link(link)

                try:
                    message_id = await message_task
                except VkCaptchaNeeded:
                    self._log.error('Captcha kicked in, unable to proceed')
                    raise RuntimeError('Captcha')

                if resolved is None:
                    self._log.warn('Skipping link')
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=f"{EMOJI['timeout']} {link}"
                    )
                    continue

                link = furl(link)

                if link.host != resolved.location.host:
                    message_text = f"{EMOJI['processed']} {resolved.redirect_path}"
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=message_text,
                    )
                else:
                    message_text = f"{EMOJI['processed']} {link}"
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=message_text,
                    )

                if resolved.snapshot:
                    photo_id = await self._vkapi.upload_photo(resolved.snapshot)
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=message_text,
                        attachment=photo_id
                    )

        except asyncio.CancelledError:
            self._log.info('Longpoll task cancelled')
            return

    def start(self):
        # Initialize components first
        self._aioloop.run_until_complete(self._vkapi.register())
        self._aioloop.run_until_complete(self._web.start())

        try:
            self._server_task_pool = [asyncio.ensure_future(self._process()) for _ in range(self._workers)]
            self._aioloop.run_until_complete(asyncio.gather(*self._server_task_pool))

        except KeyboardInterrupt:
            self._log.warn('Being shut down by keyboard interrupt or SIGINT')
        except RuntimeError:
            self._log.warn('Being shut down by server error')
        finally:
            # Send cancel exception to all server tasks
            for task in self._server_task_pool:
                task.cancel()

            # Collect pending coroutines and wait for them to finish
            pending_tasks = [task for task in self._aux_tasks if not task.done()]

            if len(pending_tasks) > 0:
                self._log.info(f"Waiting for {len(pending_tasks)} pending tasks to finish")
                self._aioloop.run_until_complete(asyncio.gather(*pending_tasks))
                self._log.info('Pending tasks finished')

            # Wait for server tasks to wrap up
            self._aioloop.run_until_complete(asyncio.gather(*self._server_task_pool))

            self._log.info('Stopping auxiliary services')
            self._vkapi.stop()
            self._aioloop.run_until_complete(self._web.stop())
            self._aioloop.close()

            self._log.warn('Server has been shut down!')
