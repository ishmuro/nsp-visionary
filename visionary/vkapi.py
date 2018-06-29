import json
import random

import aiohttp
import aiovk
import asyncio as aio
import logbook

from typing import Optional
from visionary import config
from visionary.util import ModuleState, RateLimiter


class VKAPIHandle(object):
    _aiohttp_client = None      # AIOHTTP client instance used to communicate with the API
    _vk_session = None          # VK API session object
    _api = None                 # VK API negotiation object. All methods are bound to this.
    _longpoll = None            # VK API Longpoll handler. Used for receiving message events
    _upload_uri = None          # Image upload URI generated by VK API.
    _longpoll_cursor = -1       # Longpoll events cursor. Stores last processed event ID.
    _chat_cache = None
    _rate_limiter = RateLimiter(max_tokens=3)

    def __init__(self, loop: aio.AbstractEventLoop, token: str, listen_chat_name: str, reply_chat_name: str=None):
        self._log = logbook.Logger('VKAPI')
        self._token = token
        self._aio_loop = loop
        self._listen_to = listen_chat_name
        self._reply_to = reply_chat_name or None
        random.seed()

    async def register(self):
        """
        Initialize the handle to get it to working state. Initiate sessions, resolve chat names.
        Raises:
            ValueError if no chat found with the name specified at creation.
        """
        self._state = ModuleState.starting
        self._aiohttp_client = aiohttp.ClientSession(loop=self._aio_loop)
        self._vk_session = aiovk.TokenSession(access_token=self._token)
        self._api = aiovk.API(self._vk_session)
        self._longpoll = aiovk.LongPoll(self._api, mode=2)

        self._listen_to = await self._api_get_chat_id(self._listen_to)
        self._log.debug(f"Listening to peer {self._listen_to}")
        if self._listen_to is None:
            raise ValueError('No chat found')

        if self._reply_to is None:
            self._reply_to = self._listen_to
        else:
            self._reply_to = await self._api_get_chat_id(self._reply_to)
            self._log.debug(f"Replying to peer {self._reply_to}")

        await self._api_get_photo_upload_uri()
        self._state = ModuleState.ready
        self._log.info('VKAPI handle OK.')

    async def _rate(self):
        """
        Wait for rate limiter to have spare token
        """
        delay = await self._rate_limiter.wait_for_token()
        if delay > 0:
            self._log.info(f"Waited for rate limiter token: {delay:.2f}s")

    async def _api_get_chat_id(self, chat_name: str) -> Optional[int]:
        """
        Resolve chat names to its respective peer_ids.
        Returns:
            List of VK chat peer_ids
        Raises:
            `ValueError` if no chat is found with the name given.
        """
        if self._chat_cache is None:
            await self._rate()

            chat_data = await self._api.messages.getDialogs()
            self._chat_cache = chat_data['items']
            self._log.debug(f"Chat cache populated: {self._chat_cache}")

        for item in self._chat_cache:
            if item['message']['title'] == chat_name:
                return config.VKAPI_CHAT_OFFSET + int(item['message']['chat_id'])

        return None

    async def _api_get_photo_upload_uri(self):
        """
        Set the image upload URI using photos.getMessagesUploadServer

        References:
            https://vk.com/dev/upload_files
            https://vk.com/dev/photos.getMessagesUploadServer
        """
        await self._rate()
        resp = await self._api.photos.getMessagesUploadServer(peer_id=self._listen_to)
        self._upload_uri = resp['upload_url']
        self._log.debug(f"Photo upload URI: {self._upload_uri}")

    async def api_upload_photo(self, filename: str) -> str:
        """
        Upload an image by filename to VK servers
        Args:
            filename: Path to the image

        Returns:
            VK attachment identifier.

        References:
            https://vk.com/dev/messages.send
        """
        if not self._upload_uri:
            await self._rate()
            await self._api_get_photo_upload_uri()

        send_data = aiohttp.FormData()
        send_data.add_field('photo', open(filename, 'rb'), filename=filename)

        async with self._aiohttp_client.post(self._upload_uri, data=send_data) as resp:
            recv_data = await resp.text()
            recv_data = json.loads(recv_data)

            await self._rate()
            uploaded = await self._api.photos.saveMessagesPhoto(
                server=recv_data['server'],
                hash=recv_data['hash'],
                photo=recv_data['photo']
            )
            uploaded = uploaded[0]

        return f"photo{uploaded['owner_id']}_{uploaded['id']}"

    async def api_send_msg(self, text: str, attachment: str=None) -> int:
        """
        Send message to `reply_to` peer ID
        Args:
            text: Text part of the message
            attachment (optional): Image to attach. Defaults to None

        Returns:
            Sent message ID

        References:
            https://vk.com/dev/messages.send
        """
        random_id = random.randint(0, config.RAND_MAX)
        await self._rate()
        sent_msg_id = await self._api.messages.send(
            random_id=random_id,
            peer_id=self._reply_to,
            message=text,
            attachment=attachment or ''
        )
        return sent_msg_id

    async def api_edit_msg(self, msg_id: int, text: str, attachment: str=None) -> int:
        """
        Edit an already sent message
        Args:
            msg_id: ID of the message to edit
            text: Text payload
            attachment: Attachment

        Returns:
            1 if the edit was successful. 0 otherwise.

        References:
            https://vk.com/dev/messages.edit
        """
        await self._rate()
        return await self._api.messages.edit(
            peer_id=self._reply_to,
            message_id=msg_id,
            message=text,
            attachment=attachment or ''
        )

    async def wait_for_messages(self):
        """
        Get message events from `listen_to` peer ID. Generator, runs indefinitely.
        Yields:
            New message text
        """
        while True:
            await self._rate()
            new_data = await self._longpoll.wait()
            if new_data['ts'] <= self._longpoll_cursor:
                continue
            self._longpoll_cursor = new_data['ts']
            updates = new_data['updates']

            # Trivial case — no updates received
            if len(updates) == 0:
                continue
            for update in updates:
                if update[0] == 4 and update[3] == self._listen_to:
                    self._log.debug(f"Message recv: {update[6]}")
                    yield update[6]  # Yield message text

    async def stop(self):
        """
        Gracefully shut down the handle
        """
        await self._vk_session.close()
        await self._aiohttp_client.close()

