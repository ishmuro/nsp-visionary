
from selenium import webdriver
import selenium.webdriver.chrome.service as service
from selenium.webdriver.chrome.options import Options
from logbook import Logger
from visionary.util import hash_link

#
# Anything related to Selenium and WebClient class is synchronous
# and therefore to be launched wrapped in executor.
#


class WebClient(object):
    def __init__(self, binary_path: str, driver_path: str, image_path: str):
        self._log = Logger('WebClient')
        self.image_path = image_path

        self._wd_options = Options()
        self._wd_options.add_argument('--headless')
        self._wd_options.add_argument('--window-size=1280x720')
        self._wd_options.binary_location = binary_path

        self._wd_service = service.Service(driver_path)

        # This could fail and throw RemoteDriverServerException (probably)
        self._wd_service.start()
        # This can fail and throw WebDriverException (confirmed)
        self._wd = webdriver.Remote(
            self._wd_service.service_url,
            desired_capabilities=self._wd_options.to_capabilities()
        )

    def snap(self, url: str) -> str:
        """
        Captures page snapshot and saves it
        Args:
            url: URI to get screenshot from

        Returns:
            Resolved URI hash, which is used to name screenshot files
        """
        final_url = self.resolve(url)
        final_url_hash = hash_link(final_url)

        self._wd.save_screenshot(f"{self.image_path}{final_url_hash}")
        return final_url_hash

    def resolve(self, url: str) -> str:
        """
        Resolves the url, giving its final destination
        Args:
            url: URI to resolve

        Returns:
            Final destination URI
        """
        self._wd.get(url)
        return self._wd.current_url

    def stop(self):
        self._wd.stop_client()
        self._wd_service.stop()
