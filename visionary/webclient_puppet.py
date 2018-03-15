import re
import asyncio as aio
import pyppeteer as pyp
import time
import tenacity

from collections import deque
from typing import NamedTuple, Optional
from furl.furl import furl
from logbook import Logger
from pyppeteer.page import Response, Request, Page
from pyppeteer.browser import Browser
from functools import partial

from visionary.config import WEBCLIENT_ALLOWED_FILES
from visionary.util import hash_link

WebRequest = NamedTuple('WebRequest', [
    ('tab_handle', Optional[Page]),
    ('coro_handle', Optional[aio.Future]),
    ('location', furl),
    ('is_busy', bool)
])

ResolvedLink = NamedTuple('ResolvedLink', [
    ('start_location', furl),
    ('location', furl),
    ('redirect_path', str),
    ('snapshot', Optional[str]),
    ('time_taken', float)
])


class PuppetClient(object):
    _browser = None
    _tasks = list()
    _open_tab_count = 0
    _fails = 0

    def __init__(self, image_path: str, max_tabs: int):
        self._log = Logger('WebClient')
        self.image_path = image_path
        self._max_tabs = max_tabs

    async def start(self):
        self._log.debug('Trying to start browser...')
        self._browser = await pyp.launch(options={
            'args': ['--no-sandbox', '--disable-setuid-sandbox']
        })
        self._log.debug('Browser OK')

    async def stop(self):
        self._log.debug('Stopping webclient...')
        await self._browser.close()
        self._log.debug('Browser closed.')

    @tenacity.retry(stop=tenacity.stop_after_attempt(3), retry=tenacity.retry_if_exception_type(aio.TimeoutError))
    async def _get_tab(self):
        if self._open_tab_count >= self._max_tabs:
            self._log.info(f"New tab request is suspended until previous tabs are finished")
        while self._open_tab_count >= self._max_tabs:
            aio.sleep(3)
        else:
            return await aio.wait_for(self._browser.newPage(), timeout=3)

    @tenacity.retry(stop=tenacity.stop_after_attempt(3), retry=tenacity.retry_if_exception_type(RuntimeError))
    async def _navigate(self, tab: Page, link: furl):
        self._log.debug(f"Navigating to {link.url}...")
        try:
            await tab.goto(link.url)
        except Exception as e:
            self._log.error(f"Failed to navigate tab to {link.url}: {e}")
            raise RuntimeError
        else:
            self._fails = 0

    async def _handle_response(self, resp: Response, queue: deque):
        status = resp.headers.get('status') or -1
        if status == -1:
            status = resp.headers.get('connection') or ''
        length = resp.headers.get('content-length') or ''
        ctype: str = resp.headers.get('content-type') or ''

        location = furl(resp.headers.get('location') or '')
        domain = location.host or ''

        if ctype.partition('/')[0] not in ('image', 'application'):
            self._log.debug(f"({status}) {location.tostr()}: {length} {ctype}")

        if domain != '' and queue.count(domain) == 0:
            queue.append(domain)
            self._log.debug(f"Redirect queue as of now: {queue}")

    async def process_link(self, link):
        start_time = time.monotonic()
        link = furl(link)
        redirect_queue = deque()

        if link.path.isfile:
            try:
                last_dot_pos = link.pathstr.rindex('.')
            except ValueError:
                pass
            else:
                extension = re.sub('[^A-Za-z.]+', '', link.pathstr[last_dot_pos:])
                if extension not in WEBCLIENT_ALLOWED_FILES:
                    return ResolvedLink(
                        start_location=link.tostr(),
                        location=link.tostr(),
                        redirect_path='',
                        time_taken=-1
                    )
        tab = await self._get_tab()
        tab.on(event='response', f=partial(self._handle_response, queue=redirect_queue))
        try:
            await self._navigate(tab, link)
        except tenacity.RetryError:
            self._fails += 1
            return None
        endpoint = furl(tab.url)
        snapshot = self.image_path + hash_link(endpoint.host+endpoint.pathstr) + '.png'
        await tab.screenshot({
            'path': snapshot
        })
        await tab.close()

        if link.host in redirect_queue:
            redirect_queue.popleft()
        redirect_queue.appendleft(link.host + link.pathstr)

        if endpoint.host in redirect_queue:
            redirect_queue.pop()
        redirect_queue.append(endpoint.host + endpoint.pathstr)

        redirect_str = ''
        for item in redirect_queue:
            if item in endpoint.url:
                redirect_str += item
                break
            else:
                redirect_str += f"{item} -> "

        return ResolvedLink(
            start_location=link,
            location=endpoint,
            redirect_path=redirect_str,
            snapshot=snapshot,
            time_taken=time.monotonic()-start_time
        )


redirect_queue = deque()


@tenacity.retry(stop=tenacity.stop_after_attempt(3))
async def get_page(browser: Browser):
    print('Trying to get page...')
    return await aio.wait_for(browser.newPage(), timeout=3)


async def handle_response(resp: Response):
    location = resp.headers.get('location') or ''
    location = furl(location)
    loc_url = str(location.host) + location.pathstr

    if redirect_queue.count(loc_url) == 0:
        redirect_queue.append(loc_url)
    ctype: str = resp.headers.get('content-type') or ''

    status = resp.headers.get('status') or -1
    if status == -1:
        status = resp.headers.get('connection')
    length = resp.headers.get('content-length')

    print(f"<- ({status}) {location}: {length}kB {ctype}")


async def handle_request(req: Request):
    location = req.url
    ref = req.headers.get('Referer') or ''

    print(f"-> {location} {ref}")


async def main(url: str):
    stime = time.monotonic()
    browser = await pyp.launch(options={
        'args': ['--no-sandbox', '--disable-setuid-sandbox']
    })
    etime = time.monotonic()
    eltime = (etime - stime) * 1000
    print(f'spawned browser in {eltime:.2f} ms')

    uri = furl(url)
    if uri.path.isfile:
        print(uri.pathstr)
        try:
            last_dot = uri.pathstr.rindex('.')
        except ValueError:
            pass
        else:
            print(uri.pathstr[last_dot:])
            if uri.pathstr[last_dot:] not in ['.html', '.php']:
                return
    stime = time.monotonic()
    page = await get_page(browser)
    etime = time.monotonic()
    eltime = (etime - stime) * 1000
    print(f'got new page in {eltime:.2f} ms')

    page.on(event='response', f=handle_response)
    page.on(event='request', f=handle_request)

    await page.goto(uri.url)
    final_url = page.url
    await page.screenshot({
        'path': f"{uri.host}.png"
    })
    await browser.close()

    if uri.host + uri.pathstr not in redirect_queue:
        redirect_queue.appendleft(uri.host + uri.pathstr)

    print('Redirects: ', end='')
    for item in redirect_queue:
        if item in final_url:
            print(item)
            break
        else:
            print(item, ' -> ', end='')


async def spawner(qty: int):
    stime = time.monotonic()
    browser = await pyp.launch(options={
        'args': ['--no-sandbox', '--disable-setuid-sandbox']
    })
    etime = time.monotonic()
    eltime = (etime - stime) * 1000
    print(f'spawned browser in {eltime:.2f} ms')

    for _ in range(qty):
        stime = time.monotonic()
        page = await get_page(browser)
        etime = time.monotonic()
        eltime = (etime - stime) * 1000
        print(f'spawned new page in {eltime:.2f} ms')
        pages = await browser.pages()
        print(f'Pages: {len(pages)} -> {pages}')

    for page in await browser.pages():
        await page.close()

    await browser.close()


if __name__ == '__main__':
    print('Testing for pyppeteer.')
    uri1 = 'https://www.tltsu.ru/about_the_university/voting/mr-and-miss-tsu/mr-tsu-2018.php'
    uri2 = 'https://goo.gl/Wex4k6'
    try:
        loop = aio.get_event_loop()
    except RuntimeError:
        loop = aio.new_event_loop()

    # loop.run_until_complete(main(uri1))
    # loop.run_until_complete(main(uri2))
    loop.run_until_complete(spawner(5))
