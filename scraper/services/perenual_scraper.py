import logging
import os
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.files.base import ContentFile
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from scraper.models import Plant, PlantImage

BASE_DIR = Path(settings.BASE_DIR)
LOG_FILE = BASE_DIR / 'scraper.log'
STOP_FLAG_FILE = BASE_DIR / 'stop_scraping.flag'

DETAIL_LABELS = {
    'cycle': 'Cycle:',
    'watering': 'Watering:',
    'sunlight': 'Sun:',
    'hardiness_zone': 'Hardiness Zone:',
    'flowers': 'Flowers:',
    'soil': 'Soil:',
    'cones': 'Cones:',
    'leaf': 'Leaf:',
    'growth_rate': 'Growth Rate:',
    'maintenance': 'Maintenance:',
    'care_level': 'Care Level:',
}

REQUEST_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
}


def get_scraper_logger():
    """Return the shared scraper logger configured to write to BASE_DIR/scraper.log."""
    scraper_logger = logging.getLogger('scraper')
    scraper_logger.setLevel(logging.INFO)
    resolved_log = LOG_FILE.resolve()
    has_handler = any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename).resolve() == resolved_log
        for handler in scraper_logger.handlers
    )
    if not has_handler:
        file_handler = logging.FileHandler(resolved_log, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
        scraper_logger.addHandler(file_handler)
    return scraper_logger


logger = get_scraper_logger()


class PerenualScraper:
    """Selenium + BeautifulSoup scraper for the Perenual plant species database."""

    def __init__(self, base_url='https://perenual.com', driver=None):
        self.base_url = base_url.rstrip('/')
        self.driver = driver

    def log(self, message):
        """Log a progress message to stdout and scraper.log."""
        print(message)
        logger.info(message)

    def check_stop(self):
        """Return True when a stop flag file has been created."""
        if STOP_FLAG_FILE.exists():
            self.log('Stop signal detected. Exiting...')
            return True
        return False

    def _create_driver(self):
        """Create a headless Chrome WebDriver instance."""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        return webdriver.Chrome(options=chrome_options)

    def close(self):
        """Quit the active WebDriver if running."""
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception as exc:
                self.log(f'Error closing WebDriver: {exc}')
            finally:
                self.driver = None

    @staticmethod
    def _extract_detail(soup, label_text):
        """Extract a single plant characteristic value by its label text."""
        label_h3 = soup.find(lambda tag: tag.name == 'h3' and label_text in tag.text)
        if label_h3:
            p_value = label_h3.find_next_sibling('p')
            if p_value:
                return p_value.get_text(strip=True) or None

        label_div = soup.find(
            lambda tag: tag.name == 'div' and label_text in tag.text and len(tag.text) < 50
        )
        if not label_div:
            strong_tag = soup.find(lambda tag: tag.name == 'strong' and label_text in tag.text)
            if strong_tag:
                label_div = strong_tag.parent

        if label_div:
            text = label_div.get_text(strip=True).replace(label_text, '').strip()
            if text:
                return text
            next_sibling = label_div.find_next_sibling(['div', 'p', 'span'])
            if next_sibling:
                return next_sibling.get_text(strip=True) or None

        return None

    def parse_plant_html(self, html, url):
        """Parse a plant detail page HTML into structured attributes and image URL."""
        soup = BeautifulSoup(html, 'html.parser')

        h1 = soup.find('h1')
        common_name = h1.get_text(strip=True) if h1 else 'Unknown'

        scientific_name = 'Unknown'
        if h1:
            sibling = h1.find_next_sibling(['p', 'div'])
            if sibling:
                i_tag = sibling.find('i')
                if i_tag and i_tag.get_text(strip=True):
                    scientific_name = i_tag.get_text(strip=True)

        h2 = soup.find('h2', class_='italic main-t-c my-2')
        h2_text = h2.get_text(strip=True) if h2 else ''

        # Perenual renders the Latin binomial inside <h2 class="italic main-t-c my-2">
        # when no italic subtitle sits directly under <h1>.
        if scientific_name == 'Unknown' and h2_text:
            scientific_name = h2_text
            alternative_name = ''
        elif h2_text and h2_text.lower() != scientific_name.lower():
            alternative_name = h2_text
        else:
            alternative_name = ''

        if not alternative_name:
            other_name = self._extract_detail(soup, 'Other Name:')
            if other_name:
                alternative_name = other_name

        desc_div = soup.find('div', class_='text-xs rounded-md my-2')
        description = desc_div.get_text(strip=True) if desc_div else ''

        details = {
            field: self._extract_detail(soup, label)
            for field, label in DETAIL_LABELS.items()
        }

        image_url = None
        for img in soup.find_all('img'):
            src = img.get('src')
            if src and 's3' in src and 'perenual' in src:
                image_url = urljoin(f'{self.base_url}/', src)
                break

        return {
            'common_name': common_name,
            'scientific_name': scientific_name,
            'alternative_name': alternative_name,
            'description': description,
            'source_url': url,
            'image_url': image_url,
            **details,
        }

    def _collect_plant_urls(self, max_scrolls, max_plants):
        """Load the search page, scroll to reveal cards, and return unique plant URLs."""
        target_url = f'{self.base_url}/plant-species-database-search-finder'
        self.driver.get(target_url)
        time.sleep(3)

        collected = []
        for _ in range(max_scrolls):
            if self.check_stop():
                break
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            cards = soup.find_all('a', class_='search-container-box')
            for card in cards:
                href = card.get('href')
                if href:
                    collected.append(urljoin(f'{self.base_url}/', href))

            unique_so_far = list(dict.fromkeys(collected))
            if len(unique_so_far) >= max_plants:
                return unique_so_far

            self.driver.execute_script('window.scrollTo(0, document.body.scrollHeight);')
            time.sleep(2)

        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        for card in soup.find_all('a', class_='search-container-box'):
            href = card.get('href')
            if href:
                collected.append(urljoin(f'{self.base_url}/', href))

        return list(dict.fromkeys(collected))

    def start_scraping(self, max_scrolls=2, max_plants=10):
        """Scrape up to max_plants species from Perenual and save them to the database."""
        if STOP_FLAG_FILE.exists():
            STOP_FLAG_FILE.unlink()

        if self.driver is None:
            self.driver = self._create_driver()

        count = 0
        try:
            unique_urls = self._collect_plant_urls(max_scrolls=max_scrolls, max_plants=max_plants)
            self.log(f'Found {len(unique_urls)} plants to scrape.')

            for url in unique_urls:
                if self.check_stop():
                    break
                if count >= max_plants:
                    self.log(f'Reached maximum requested plants ({max_plants}). Stopping.')
                    break

                if not Plant.objects.filter(source_url=url).exists():
                    if self.scrape_plant_details(url):
                        count += 1
                else:
                    self.log(f'Skipping already scraped URL: {url}')
        finally:
            self.close()
            if STOP_FLAG_FILE.exists():
                STOP_FLAG_FILE.unlink()

        return count

    def _download_plant_image(self, plant, image_url):
        """Download and attach the plant image without aborting plant creation on failure."""
        try:
            response = requests.get(image_url, headers=REQUEST_HEADERS, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.log(f'Failed to download image {image_url}: {exc}')
            return

        parsed_path = urlparse(image_url).path
        file_name = os.path.basename(parsed_path) or f'plant_{plant.pk}.jpg'
        plant_image = PlantImage(plant=plant, source_image_url=image_url)
        plant_image.image.save(file_name, ContentFile(response.content), save=True)
        self.log(f'Downloaded image: {file_name}')

    def scrape_plant_details(self, url):
        """Navigate to a plant detail page, extract fields, and persist new Plant records."""
        self.log(f'Scraping {url}')
        try:
            self.driver.get(url)
            time.sleep(1.5)
            data = self.parse_plant_html(self.driver.page_source, url)

            common_name = data['common_name']
            scientific_name = data['scientific_name']
            image_url = data.pop('image_url')

            name_exists = Plant.objects.filter(common_name__iexact=common_name).exists()
            sci_exists = (
                scientific_name != 'Unknown'
                and Plant.objects.filter(scientific_name__iexact=scientific_name).exists()
            )
            if name_exists or sci_exists:
                self.log(f'Skipping duplicate plant by name: {common_name} ({scientific_name})')
                return False

            plant = Plant.objects.create(**data)
            self.log(f'Saved: {plant.common_name}')

            if image_url:
                self._download_plant_image(plant, image_url)

            return True
        except Exception as exc:
            self.log(f'Error scraping {url}: {exc}')
            return False
