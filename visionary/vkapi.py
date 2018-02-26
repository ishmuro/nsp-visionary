import asyncio
import aiovk
import aiohttp
import json
import random

from asyncio import AbstractEventLoop
from logbook import Logger
from visionary.config import RAND_MAX, VKAPI_CHAT_OFFSET


class VKAPIHandle(object):
    _bound_peer_id = None
    _upload_uri = None
    _longpoll_cursor = 0

    def __init__(self, loop: AbstractEventLoop, token: str, chatname: str):
        self._aiohttp_client = aiohttp.ClientSession(loop=loop)
        self._vk_session = aiovk.TokenSession(access_token=token)
        self._api = aiovk.API(self._vk_session)
        self._log = Logger('VKAPI')
        self._bound_chatname = chatname
        self._longpoll = aiovk.LongPoll(self._api, mode=2)
        random.seed()

    async def register(self):
        is_chat_found = await self._get_chat_id()

        if not is_chat_found:
            raise ValueError('Invalid or non-existent chat name')

        await self._get_photo_upload_uri()
        # await self.send_msg(text="\U0001F535 Listening to this chat")

    async def _get_chat_id(self) -> bool:
        """
        Internal method to get peer ID of the bound chat name
        Returns:
            `True` if the chat is found. `False` elsewhere.
        """
        all_chats = await self._api.messages.getDialogs()
        all_chats = all_chats['items']
        self._log.debug(f"Got chat data from VKAPI: {all_chats}")

        self._log.debug(f"Searching for '{self._bound_chatname}'...")
        for chat in all_chats:
            if chat['message']['title'] == self._bound_chatname:
                self._bound_peer_id = VKAPI_CHAT_OFFSET + chat['message']['chat_id']
                self._log.info(f"Messaging bound to peer ID {self._bound_peer_id}")
                return True

        return False

    async def _get_photo_upload_uri(self):
        """
        Internal method to set the image upload URI using photos.getMessagesUploadServer

        References:
            https://vk.com/dev/upload_files
            https://vk.com/dev/photos.getMessagesUploadServer
        """
        resp = await self._api.photos.getMessagesUploadServer(peer_id=self._bound_peer_id)
        self._upload_uri = resp['upload_url']
        self._log.debug(f"Photo upload URI: {self._upload_uri}")

    async def upload_photo(self, filename: str) -> str:
        """
        Uploads image to VK to use it in future messages
        Args:
            filename: Path to the image

        Returns:
            VK attachment identifier.

        References:
            https://vk.com/dev/messages.send
        """
        if not self._upload_uri:
            await self._get_photo_upload_uri()

        send_data = aiohttp.FormData()
        send_data.add_field('photo', open(filename, 'rb'), filename=filename)

        async with self._aiohttp_client.post(self._upload_uri, data=send_data) as resp:
            recv_data = await resp.text()
            recv_data = json.loads(recv_data)

            uploaded = await self._api.photos.saveMessagesPhoto(
                server=recv_data['server'],
                hash=recv_data['hash'],
                photo=recv_data['photo']
            )
            uploaded = uploaded[0]

        return f"photo{uploaded['owner_id']}_{uploaded['id']}"

    async def send_msg(self, text: str, attachment: str=None) -> int:
        """
        Sends message to bound chat
        Args:
            text: Text part of the message
            attachment (optional): Image to attach. Defaults to None

        Returns:
            Sent message ID
        """
        random_id = random.randint(0, RAND_MAX)
        sent_msg_id = await self._api.messages.send(
            random_id=random_id,
            peer_id=self._bound_peer_id,
            message=text,
            attachment=attachment or ''
        )
        return sent_msg_id

    async def edit_msg(self, msg_id: int, text: str, attachment: str=None) -> int:
        """
        Edits the payload and attachments of a message
        Args:
            msg_id: ID of the message to edit
            text: Text payload
            attachment: Attachment

        Returns:
            1 if the edit was successful. 0 otherwise.
        """
        return await self._api.messages.edit(
            peer_id=self._bound_peer_id,
            message_id=msg_id,
            message=text,
            attachment=attachment or ''
        )

    async def wait_for_messages(self):
        """
        Generator function that yields new messages in the bound chat. Runs indefinitely.
        Yields:
            New message text
        """
        while True:
            await asyncio.sleep(0)
            new_data = await self._longpoll.wait()
            if new_data['ts'] <= self._longpoll_cursor:
                continue
            self._longpoll_cursor = new_data['ts']
            updates = new_data['updates']

            # Trivial case — no updates received
            if len(updates) == 0:
                continue

            for update in updates:
                if update[0] == 4 and update[3] == self._bound_peer_id:
                    yield update[6]  # Yield message text

    def stop(self):
        self._vk_session.close()
        self._aiohttp_client.close()
