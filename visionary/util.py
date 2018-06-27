import asyncio
import time
import lxml.html as html
import uuid
import re

from furl.furl import furl
from enum import Enum
from math import floor
from typing import Optional, NamedTuple
from types import FunctionType
from datetime import datetime


class ModuleState(Enum):
    blocked = 'blocked'
    stopped = 'stopped'
    starting = 'starting'
    ready = 'ready'
    stopping = 'stopping'


ModuleLock = NamedTuple('ModuleLock', [
    ('locked', bool),
    ('locked_at', datetime),
    ('last_check', datetime)
])


ResolvedLink = NamedTuple('ResolvedLink', [
    ('start_location', furl),
    ('location', furl),
    ('redirect_path', str),
    ('snapshot', Optional[str]),
    ('time_taken', float)
])


def hash_link(uri: str) -> Optional[str]:
    """
    Create a UUID3 hash of a link
    Args:
        uri: link to be hashed

    Returns:
        UUID3 hash of this link
    """
    if uri is None:
        return None
    uri = re.sub('\W+', '', uri)
    return str(uuid.uuid3(uuid.NAMESPACE_URL, uri.lower()))


def find_link_br(text: str) -> Optional[str]:
    """
    Match the main link in message (denoted by line break)
    Args:
        text: text to process

    Returns:
        Found link or `None`
    """
    match = re.search('^https?://[^<]+<br>', text)
    if match:
        return match.group(0)[:-4].strip()
    else:
        return None


def parse_http_refresh(html_string: str) -> Optional[str]:
    link: Optional[str] = None
    page = html.document_fromstring(html_string)
    meta_tags = page.head.findall('meta')
    for tag in meta_tags:
        if tag.get('http-equiv') == 'refresh':
            link = tag.get('content')

    if link is None:
        return None

    if '\"' in link:
        delimiter = '\"'
    elif '\'' in link:
        delimiter = '\''
    else:
        delimiter = '='

    return link.split(delimiter)[1].strip()


async def retry_async(func: FunctionType, wait_for: int, retries: int):
    for retry in range(retries):
        try:
            result = await func()
        except Exception:
            await asyncio.sleep(wait_for)
        else:
            return result


class RateLimiter:
    slept_for = 0

    def __init__(self, *, max_tokens: int, refresh_time: float = 1.0):
        self.value = self.max = max_tokens
        self.refresh_time = refresh_time
        self.last_updated = time.monotonic()

    def tick(self):
        ref = (time.monotonic() - self.last_updated) / self.refresh_time
        refill = floor(ref)
        self.value = min(self.value + refill, self.max)
        if self.value < 1:
            return False

        self.value -= 1
        self.last_updated = time.monotonic()
        return True

    async def wait_for_token(self):
        while not self.tick():
            await asyncio.sleep(0.1)
            self.slept_for += 0.1

        return self.slept_for
