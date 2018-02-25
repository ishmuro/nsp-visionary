import uuid
import re

from typing import Optional


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
        return match.group(0)[:-4]
    else:
        return None
