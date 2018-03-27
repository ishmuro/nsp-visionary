import re
import asyncio as aio
import pyppeteer as pyp
import time
import tenacity

from collections import deque
from typing import NamedTuple, Optional
from furl.furl import furl
from logbook import Logger
from pyppeteer.page import Response, Page
from pyppeteer.errors import NetworkError
from functools import partial

from visionary.config import WEBCLIENT_ALLOWED_FILES
from visionary.util import hash_link, parse_http_refresh, retry_async


ResolvedLink = NamedTuple('ResolvedLink', [
    ('start_location', furl),
    ('location', furl),
    ('redirect_path', str),
    ('snapshot', Optional[str]),
    ('time_taken', float)
])


class PuppetClient(object):
    _browser = None
    _open_tab_count = 0
    _fails = 0
    _force_redirect = None

    def __init__(self, image_path: str, max_tabs: int):
        self._log = Logger('WebClient')
        self.image_path = image_path
        self._max_tabs = max_tabs

    async def start(self):
        self._log.debug('Trying to start browser...')
        self._browser = await pyp.launch(options={
            'args': ['--no-sandbox', '--disable-setuid-sandbox']
        })
        self._log.debug('Browser OK.')

    async def stop(self):
        self._log.debug('Stopping web client...')
        await self._browser.close()
        self._log.debug('Browser closed.')

    @tenacity.retry(stop=tenacity.stop_after_attempt(3), retry=tenacity.retry_if_exception_type(aio.TimeoutError))
    async def _get_tab(self):
        if self._open_tab_count >= self._max_tabs:
            self._log.info(f"New tab request is suspended until previous tabs are finished.")
        while self._open_tab_count >= self._max_tabs:
            aio.sleep(3)
        else:
            return await aio.wait_for(self._browser.newPage(), timeout=3)

    @tenacity.retry(stop=tenacity.stop_after_attempt(3), retry=tenacity.retry_if_exception_type(RuntimeError))
    async def _navigate(self, tab: Page, link: furl, referer: Optional[furl] = None):
        self._log.debug(f"Navigating to {link.url}...")
        try:
            if referer is not None:
                await tab.setExtraHTTPHeaders({'referer': referer.url})

            await tab.goto(link.url)
        except Exception as e:
            self._log.error(f"Failed to navigate tab to {link.url}: {e}")
            raise RuntimeError
        else:
            self._fails = 0

        final_url = furl(tab.url)

        try:
            content = await retry_async(tab.content, 1, 3)
        except NetworkError:
            self._log.error('Failed to fetch body in 3 retries, dropping URL as is (missing redirect possible)')
        else:
            redirect = parse_http_refresh(content)
            if redirect is not None:
                self._navigate(tab, furl(redirect), final_url)

    async def _handle_response(self, resp: Response, queue: deque):
        location = furl(resp.headers.get('location') or '')
        domain = location.host or ''

        if domain != '' and queue.count(domain) == 0:
            queue.append(domain)
            self._log.debug(f"Redirect queue as of now: {queue}")

    async def process_link(self, link: str) -> Optional[ResolvedLink]:
        start_time = time.monotonic()
        link = furl(link)
        redirect_queue = deque()

        # Check if this is a file link
        if link.path.isfile:

            # If so, we are interested only in HTML and PHP files.
            try:
                last_dot_pos = link.pathstr.rindex('.')
            except ValueError:
                # It's just a link, albeit missing a trailing slash
                pass
            else:
                extension = re.sub('[^A-Za-z.]+', '', link.pathstr[last_dot_pos:])
                if extension not in WEBCLIENT_ALLOWED_FILES:
                    return ResolvedLink(
                        start_location=link.url,
                        location=link.url,
                        redirect_path='',
                        snapshot=None,
                        time_taken=-1
                    )

        tab = await self._get_tab()

        # Register callback fired on incoming response
        tab.on(event='response', f=partial(self._handle_response, queue=redirect_queue))
        try:
            await self._navigate(tab, link)
        except tenacity.RetryError:
            self._fails += 1
            self._log.warn(f"Failed {self._fails} time in a row.")
            return None
        endpoint = furl(tab.url)

        snapshot = self.image_path + hash_link(endpoint.host+endpoint.pathstr) + '.png'
        await tab.screenshot({
            'path': snapshot
        })
        await tab.close()

        # We do need full enter and exit links for clarity. Other hops may display as domains only.
        if link.host in redirect_queue:
            redirect_queue.popleft()
        redirect_queue.appendleft(link.host + link.pathstr)

        if endpoint.host in redirect_queue:
            redirect_queue.pop()
        redirect_queue.append(endpoint.host + endpoint.pathstr)

        # Compose a string out of redirect queue
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

