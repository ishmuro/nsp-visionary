import asyncio
import functools
from logbook import Logger
from typing import Coroutine

from aiovk.exceptions import VkCaptchaNeeded

from visionary.vkapi import VKAPIHandle
from visionary.webclient import WebClient
from visionary.util import find_link_br, hash_link
from visionary.config import EMOJI


class VisionServer(object):
    _server_task_pool = []
    _aux_tasks = []

    def __init__(self, workers: int, token: str, chat_name: str, binary_path: str, driver_path: str, image_path: str):
        self._workers = workers

        if image_path[:-1] != '/':
            image_path += '/'

        self._log = Logger('VServer')
        self._aioloop = asyncio.get_event_loop()
        self._vkapi = VKAPIHandle(self._aioloop, token, chatname=chat_name)
        self._web = WebClient(binary_path=binary_path, driver_path=driver_path, image_path=image_path)

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

                resolved_link = await self._execute_blocking(self._web.resolve, link)
                resolved_hash = hash_link(resolved_link)
                self._log.info(f"{link} -> {resolved_link} ({resolved_hash})")

                try:
                    message_id = await message_task
                except VkCaptchaNeeded:
                    self._log.error('Captcha kicked in, unable to proceed')
                    raise RuntimeError('Captcha')

                if resolved_link is None:
                    self._log.warn('Skipping link')
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=f"{EMOJI['timeout']} {link}"
                    )
                    continue

                if hash_link(link) != resolved_hash:
                    message_text = f"{EMOJI['processed']} {link} -> {resolved_link}"
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=message_text)
                else:
                    message_text = f"{EMOJI['processed']} {link}"
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=message_text)

                screen_path = await self._execute_blocking(self._web.snap, resolved_link)
                if screen_path:
                    photo_id = await self._vkapi.upload_photo(screen_path)
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
        self._aioloop.run_until_complete(self._vkapi.register())    # API init subroutine

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
            self._web.stop()
            self._aioloop.close()

            self._log.warn('Server has been shut down!')
