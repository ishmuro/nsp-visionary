import asyncio
import aiovk
import re
import uuid
import os
import sys
import glob
import random
import json

from collections import deque
from halo import Halo
from blessed import Terminal
from aiohttp import ClientSession, FormData

from selenium import webdriver
import selenium.webdriver.chrome.service as service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException


BASE_PATH = os.path.dirname(os.path.realpath(sys.argv[0]))
IMAGE_PATH = BASE_PATH + '/img/'

TOKEN = '0e2a7a1be336af19a2ccfb9e3773e142e1de3ccbb0b83516b3ae3514af2f19f9adf8d1c285b5b818677cf'
CHAT_NAME = 'TEST_DLG'

CHROME_PATH = BASE_PATH + '/chromedriver'
CHROME_OPT = Options()
CHROME_OPT.add_argument('--headless')
CHROME_OPT.add_argument('--window-size=1280x720')
CHROME_OPT.binary_location = '/usr/bin/google-chrome-unstable'  # TODO: This should be a console argument

CACHE_SIZE = 5000
KNOWN_UID = deque(maxlen=CACHE_SIZE)

t = Terminal()
random.seed()


def scan_existing_hashes(directory):
    """
    Scans given directory for existing screenshots and adds them by names as known
    Args:
        directory: directory to search hashed images in (str)

    Returns:
        Nothing
    """
    os.chdir(directory)
    for file in glob.glob('*.png'):
        KNOWN_UID.append(file[:-4])


async def get_chat_id(api, name):
    dialogs = await api.messages.getDialogs()
    dialogs = dialogs['items']
    for dialog in dialogs:
        if dialog['message']['title'] == name:
            chat_id = 2000000000 + dialog['message']['chat_id']
            return chat_id
    return None


async def get_photo_server(api, peer_id):
    response = await api.photos.getMessagesUploadServer(peer_id=peer_id)
    return response['upload_url']


async def send_photo(api, filename, uri):
    data = FormData()
    data.add_field('photo', open(filename, 'rb'), filename=filename)
    async with ClientSession() as session:
        async with session.post(uri, data=data) as response:
            upload_data = await response.text()
            upload_data = json.loads(upload_data)
            photo_data = await api.photos.saveMessagesPhoto(
                server=upload_data['server'],
                hash=upload_data['hash'],
                photo=upload_data['photo']
            )
            photo_data = photo_data[0]
            return f"photo{photo_data['owner_id']}_{photo_data['id']}"


async def reply(api, chat_id, link, screen_hash, photo_server_uri):
    random_id = random.randint(0, 100000)
    screen_path = f"{IMAGE_PATH}{screen_hash}.png"
    attach = await send_photo(api, screen_path, photo_server_uri)
    return await api.messages.send(
        random_id=random_id,
        peer_id=chat_id,
        message=link,
        attachment=attach
    )


async def cleanup_hashes(directory):
    """
    Cleans excess files from image directory
    Args:
        directory: directory to clean up (str)

    Returns:
        Nothing
    """
    if len(KNOWN_UID) == CACHE_SIZE:
        os.chdir(directory)
        for file in glob.glob('*.png'):
            if file[:-4] not in KNOWN_UID:
                os.remove(file)


async def hash_link(link):
    """
    Creates a UUID hash of a link
    Args:
        link: link to be hashed (str)

    Returns:
        UUID3 hash of the link (str)
    """
    return str(uuid.uuid3(uuid.NAMESPACE_URL, link))


async def find_link(text):
    """
    Matches the main link in message
    Args:
        text: message to analyze (str)

    Returns:
        Found link (str) or None
    """
    match = re.search('^https?://[^<]+<br>', text)
    link = None

    if match:
        link = match.group(0)[:-4]

    return link


async def generate_screenshot(link, service):
    """
    Generates a screenshot of the page located by the provided link
    Args:
        link: page to screenshot (str)
        service: webdriver service

    Returns:
        Link UUID (str)
    """
    wd = webdriver.Remote(service.service_url, desired_capabilities=CHROME_OPT.to_capabilities())
    wd.get(link)

    link_id = await hash_link(link)

    wd.save_screenshot(f"{IMAGE_PATH}/{link_id}.png")
    return link_id


async def main(service):
    async with aiovk.TokenSession(access_token=TOKEN) as session:
        api = aiovk.API(session)
        chat_id = await get_chat_id(api, CHAT_NAME)
        photo_server_uri = await get_photo_server(api, chat_id)

        if chat_id is None:
            return -1
        else:
            print(t.cyan('Listening to chat id'), t.yellow(str(chat_id)))
        lp = aiovk.longpoll.LongPoll(api, mode=2)
        while True:
            new_data = await lp.wait()
            updates = new_data['updates']
            if len(updates) > 0:
                for update in updates:
                    if update[0] == 4 and update[3] == chat_id:
                        # This is a legit message
                        message_chat = update[5]
                        message_text = update[6]
                        print(t.green(f"New message received in chat {message_chat}:"), message_text)

                        link = await find_link(message_text)
                        if link:
                            link_hash = await hash_link(link)
                            link_hash = str(link_hash)

                            if link_hash in KNOWN_UID:
                                print(
                                    t.green('Found link: '),
                                    t.blue(link),
                                    t.yellow(f"({link_hash})"),
                                    t.green(', already known')
                                )
                            else:
                                print(
                                    t.green('Found new link: '),
                                    t.blue(link),
                                    t.yellow(f"({link_hash})")
                                )
                                screen_id = await generate_screenshot(link, service)
                                print(t.green('Generated screenshot for '), t.yellow(screen_id))

                            asyncio.ensure_future(reply(api, chat_id, link, link_hash, photo_server_uri))
                            await cleanup_hashes(IMAGE_PATH)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()

    with Halo(text='', spinner='dots') as halo:
        halo.color = 'red'
        halo.text = 'Setting up headless Chrome...'

        chrome_service = service.Service(CHROME_PATH)
        chrome_service.start()
        try:
            test_wd = webdriver.Remote(chrome_service.service_url, desired_capabilities=CHROME_OPT.to_capabilities())
            del test_wd
        except WebDriverException as e:
            halo.fail(f"Chrome init failed: {e}")
            sys.exit(-1)

        halo.text = 'Scanning existing screenshots...'
        scan_existing_hashes(IMAGE_PATH)

        halo.color = 'cyan'
        halo.text = ''

        try:
            loop.create_task(main(chrome_service))
            loop.run_forever()
        except KeyboardInterrupt:

            halo.color = 'red'
            halo.text = 'Killing Chrome instance...'

            chrome_service.stop()
            del chrome_service

            halo.succeed(text='Server terminated.')

        else:
            halo.fail(t.red('Something wrong happened'))
