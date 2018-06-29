# Redis server address
REDIS_URI = 'redis://localhost'

# Maximum random number for identifying messages
RAND_MAX = 100000

# VK API peer_id offset for chats. Reference: https://vk.com/dev/messages.send
VKAPI_CHAT_OFFSET = 2000000000

# Maximum VK API queries to be sent in a time unit
VKAPI_MAX_RATE = 3

# Time unit for the VK API rate limiter (in seconds)
VKAPI_RATE_PER = 1

# Webclient page load timeout in seconds
WEBCLIENT_TIMEOUT = 20

# File extensions to be treated as a web page by the webclient
WEBCLIENT_ALLOWED_FILES = ('.html', '.php')

# Emoji list for use in messages
EMOJI = {
    'blue_bubble':  '\U0001F535',
    'red_bubble':   '\U0001F534',
    'process':      '\U0001F504',
    'processed':    '\U000023FA',
    'check':        '\U00002705',
    'warn':         '\U000026A0',
    'cross':        '\U0000274C',
    'ok':           '\U0001F197',
    'timeout':      '\U0000231B',
    'package':      '\U0001F4E6'
}
