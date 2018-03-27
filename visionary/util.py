import asyncio
import lxml.html as html
import uuid
import re

from typing import Optional
from types import FunctionType


def hash_link(uri: str) -> Optional[str]:
    """
    Creates a UUID3 hash of a link
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
    Matches the main link in message (denoted by line break)
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
