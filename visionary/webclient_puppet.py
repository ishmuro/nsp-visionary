import re
import asyncio as aio
import pyppeteer as pyp
import time
import tenacity

from collections import deque
from typing import Optional
from furl.furl import furl
from logbook import Logger
from pyppeteer.page import Response, Page
from pyppeteer.errors import NetworkError
from functools import partial
from websockets.exceptions import ConnectionClosed

from visionary.config import WEBCLIENT_ALLOWED_FILES, WEBCLIENT_TIMEOUT
from visionary.util import hash_link, parse_http_refresh, retry_async, ResolvedLink, ModuleStates


class PuppetClient(object):
    _state = ModuleStates.stopped
    _browser = None
    _open_tab_count = 0
    _fails = 0
    _recent_fails = deque()

    def __init__(self, image_path: str, max_tabs: int):
        self._log = Logger('WebClient')
        self.image_path = image_path
        self._max_tabs = max_tabs

    async def start(self):
        self._state = ModuleStates.starting
        self._log.debug('Trying to start browser...')
        self._browser = await pyp.launch(options={
            'args': ['--no-sandbox', '--disable-setuid-sandbox']
        })
        self._log.debug('Browser OK.')
        self._state = ModuleStates.ready

    async def stop(self):
        self._state = ModuleStates.stopping
        self._log.debug('Stopping web client...')
        await self._browser.close()
        self._browser = None
        self._log.debug('Browser closed.')
        self._state = ModuleStates.stopped

    async def _restart(self, msg: str):
        self._log.warn(msg)
        self._fails = 0
        await self.stop()
        await self.start()

    async def _wait_for_ready(self):
        self._log.info('Waiting until service is ready...')

        if self._state is ModuleStates.stopped:
            await self.start()

        while self._state is not ModuleStates.ready:
            await aio.sleep(1)

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

            await aio.wait_for(tab.goto(link.url), timeout=WEBCLIENT_TIMEOUT)
        except ConnectionClosed:
            self._log.error("Looks like the browser has crashed.")
            raise ConnectionClosed
        except Exception as e:
            self._log.error(f"Failed to navigate tab to {link.url}: {e}")
            raise RuntimeError
        else:
            self._fails = 0

        final_url = furl(tab.url)

        # This section is basically a dirty hack, since self-refreshing pages tend to break on requesting
        # their contents, since the page we are trying to load is already gone.
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

    async def process_link(self, link: furl) -> Optional[ResolvedLink]:
        await self._wait_for_ready()
        start_time = time.monotonic()
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

                # If the extension gets too long, it's probably some weird link with dots in the slug.
                # Advert tracking doorways could use those

                if extension not in WEBCLIENT_ALLOWED_FILES and len(extension) < 15:
                    return ResolvedLink(
                        start_location=link,
                        location=link,
                        redirect_path='',
                        snapshot=None,
                        time_taken=-1
                    )
        try:
            tab = await self._get_tab()
        except tenacity.RetryError:
            aio.ensure_future(self._restart('Restarting due to inability to allocate new tab.'))
            return None

        # Register callback fired on incoming response
        tab.on(event='response', f=partial(self._handle_response, queue=redirect_queue))
        try:
            await self._navigate(tab, link)
        except ConnectionClosed:
            aio.ensure_future(self._restart('Restarting due to browser hang up'))
        except tenacity.RetryError:
            if self._fails >= 4:
                aio.ensure_future(self._restart(f"Restarting due to accumulated failures ({self._fails})"))

            if self._recent_fails.count(link.host) == 0:            # Domain is brand new and presumably working
                self._recent_fails.append(link.host)
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

