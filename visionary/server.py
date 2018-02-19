import asyncio
import functools
from logbook import Logger

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

        self._log = Logger('VServer')
        self._aioloop = asyncio.get_event_loop()
        self._vkapi = VKAPIHandle(self._aioloop, token, chatname=chat_name)
        self._web = WebClient(binary_path=binary_path, driver_path=driver_path, image_path=image_path)

    async def _execute_blocking(self, func, *args, **kwargs):
        self._log.debug(f"Running function {func.__name__}{args} in executor.")
        return await self._aioloop.run_in_executor(executor=None, func=functools.partial(func, *args, **kwargs))

    def _queue_task(self, func, *args, **kwargs):
        self._log.debug(f"Queuing function {func.__name__}{args} to execute.")
        task = asyncio.ensure_future(func(*args, **kwargs))
        self._aux_tasks.append(task)
        return task

    async def _process(self):
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
                    raise asyncio.CancelledError

                if link != resolved_link:
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=f"{EMOJI['processed']} {link} -> {resolved_link} ({resolved_hash})")
                else:
                    self._queue_task(
                        self._vkapi.edit_msg,
                        msg_id=message_id,
                        text=f"{EMOJI['processed']} {link}")

        except asyncio.CancelledError:
            self._log.info('Longpoll task cancelled')
            return

    def start(self):
        self._aioloop.run_until_complete(self._vkapi.register())    # API init subroutine

        try:
            self._server_task_pool = [asyncio.ensure_future(self._process()) for _ in range(self._workers)]
            self._aioloop.run_until_complete(asyncio.gather(*self._server_task_pool))

        except KeyboardInterrupt:
            pass
        finally:
            self._log.info('Shutting down')

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

            self._log.warn('Server has been shut down')
