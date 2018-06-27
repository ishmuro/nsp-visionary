import selenium.webdriver.chrome.service as service
import threading

from typing import Optional
from logbook import Logger
from selenium import webdriver

from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException

from visionary.util import hash_link

#
# Anything related to Selenium and WebClient class is synchronous
# and therefore to be launched wrapped in executor.
#


class WebClient(object):
    def __init__(self, binary_path: str, driver_path: str, image_path: str, timeout: int):
        self._log = Logger('WebClient')
        self.image_path = image_path
        self.timeout = timeout

        self._wd_options = Options()
        # self._wd_options.set_headless(True)
        self._wd_options.add_argument('--window-size=1280x720')
        self._wd_options.binary_location = binary_path

        self._wd_service = service.Service(driver_path)

        # This could fail and throw RemoteDriverServerException (probably)
        self._wd_service.start()
        self._log.debug(f"Started Webdriver service: {self._wd_service}")
        # This can fail and throw WebDriverException (confirmed)
        self._wd = webdriver.Remote(
            self._wd_service.service_url,
            desired_capabilities=self._wd_options.to_capabilities()
        )
        self._log.debug(f"Started chrome instance: {self._wd}")
        self._wd.set_page_load_timeout(self.timeout)
        self._wd.set_script_timeout(self.timeout)

    def snap(self, url: str) -> Optional[str]:
        """
        Captures page snapshot and saves it
        Args:
            url: URI to get screenshot from

        Returns:
            Resolved URI hash, which is used to name screenshot files (or None if resolve failed)
        """
        url_hash = hash_link(url)
        path = f"{self.image_path}{url_hash}.png"

        self._log.debug(f"Snap start. Service {self._wd_service}, client {self._wd}. current_url {self._wd.current_url}")

        if self._wd.current_url != url:
            link = self.resolve(url)
            if link is None:
                return None

        self._wd.save_screenshot(path)

        return path or None

    def resolve(self, url: str) -> Optional[str]:
        """
        Resolves the url, giving its final destination
        Args:
            url: URI to resolve

        Returns:
            Final destination URI or None if failed
        """
        self._log.debug(f"Resolving {url}. Service {self._wd_service}, client {self._wd}, current url {self._wd.current_url}")

        try:
            self._wd.get(url)
        except WebDriverException as e:
            self._log.error(f"Error resolving {url}: {e}")
        else:
            return self._wd.current_url

        return None

    def stop(self):
        self._wd.stop_client()
        self._wd_service.stop()
