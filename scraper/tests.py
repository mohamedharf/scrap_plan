import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from scraper import views
from scraper.models import Plant, PlantImage
from scraper.services.perenual_scraper import PerenualScraper


class PlantModelTests(TestCase):
    """Tests for Plant and PlantImage database models."""

    def test_plant_str_and_image_cascade(self):
        plant = Plant.objects.create(
            common_name='European Silver Fir',
            scientific_name='Abies alba',
            source_url='https://perenual.com/plant-species-database-search-finder/species/1',
        )
        image = PlantImage.objects.create(
            plant=plant,
            image='plants/silver_fir.jpg',
            source_image_url='https://perenual.com/storage/species_image/1.jpg',
        )
        self.assertEqual(str(plant), 'European Silver Fir (Abies alba)')
        self.assertEqual(str(image), 'Image for European Silver Fir')

        plant.delete()
        self.assertEqual(PlantImage.objects.count(), 0)


class PerenualScraperParserTests(TestCase):
    """Tests for HTML parsing and WebDriver lifecycle in PerenualScraper."""

    def setUp(self):
        self.scraper = PerenualScraper()

    def test_parse_plant_html_uses_h2_when_italic_sibling_missing(self):
        html = """
        <html><body>
            <h1>European Silver Fir</h1>
            <h2 class="italic main-t-c my-2">Abies alba</h2>
            <div class="text-xs rounded-md my-2">Evergreen coniferous tree.</div>
            <div><h3>Cycle:</h3><p>Perennial</p></div>
            <div><h3>Watering:</h3><p>Frequent</p></div>
            <div><h3>Sun:</h3><p>full sun</p></div>
            <div><h3>Hardiness Zone:</h3><p>7</p></div>
            <div><h3>Care Level:</h3><p>Medium</p></div>
            <img src="//perenual.s3.us-east-2.amazonaws.com/species/fir.jpg" alt="Fir">
        </body></html>
        """
        data = self.scraper.parse_plant_html(
            html,
            'https://perenual.com/plant-species-database-search-finder/species/1',
        )

        self.assertEqual(data['common_name'], 'European Silver Fir')
        self.assertEqual(data['scientific_name'], 'Abies alba')
        self.assertEqual(data['alternative_name'], '')
        self.assertEqual(data['description'], 'Evergreen coniferous tree.')
        self.assertEqual(data['cycle'], 'Perennial')
        self.assertEqual(data['watering'], 'Frequent')
        self.assertEqual(data['sunlight'], 'full sun')
        self.assertEqual(data['hardiness_zone'], '7')
        self.assertEqual(data['care_level'], 'Medium')
        self.assertEqual(
            data['image_url'],
            'https://perenual.s3.us-east-2.amazonaws.com/species/fir.jpg',
        )

    def test_parse_plant_html_preserves_distinct_alternative_name(self):
        html = """
        <html><body>
            <h1>Japanese Maple</h1>
            <p><i>Acer palmatum</i></p>
            <h2 class="italic main-t-c my-2">Smooth Japanese Maple</h2>
            <div><strong>Soil:</strong> Loamy</div>
        </body></html>
        """
        data = self.scraper.parse_plant_html(
            html,
            'https://perenual.com/plant-species-database-search-finder/species/2',
        )
        self.assertEqual(data['scientific_name'], 'Acer palmatum')
        self.assertEqual(data['alternative_name'], 'Smooth Japanese Maple')
        self.assertEqual(data['soil'], 'Loamy')

    def test_start_scraping_always_quits_driver_on_error(self):
        mock_driver = MagicMock()
        mock_driver.get.side_effect = RuntimeError('Browser crashed')
        scraper = PerenualScraper(driver=mock_driver)

        with self.assertRaises(RuntimeError):
            scraper.start_scraping(max_scrolls=1, max_plants=5)

        mock_driver.quit.assert_called_once()


class ScraperViewsTests(TestCase):
    """Integration tests for dashboard, catalog, detail, and API endpoints."""

    def setUp(self):
        self.fir = Plant.objects.create(
            common_name='European Silver Fir',
            scientific_name='Abies alba',
            cycle='Perennial',
            watering='Frequent',
            sunlight='full sun',
            source_url='https://perenual.com/plant-species-database-search-finder/species/1',
        )
        self.maple = Plant.objects.create(
            common_name='Red Filigree Lace Japanese Maple',
            scientific_name="Acer palmatum 'Red Filigree Lace'",
            cycle='Perennial',
            watering='Average',
            sunlight='full sun,part shade',
            source_url='https://perenual.com/plant-species-database-search-finder/species/104',
        )

    def test_dashboard_view(self):
        response = self.client.get(reverse('scraper:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_plants'], 2)

    def test_plant_list_view_search_and_filter(self):
        response = self.client.get(reverse('scraper:plant_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['page_obj'].paginator.count, 2)

        filtered = self.client.get(reverse('scraper:plant_list'), {'q': 'Abies'})
        self.assertEqual(filtered.context['page_obj'].paginator.count, 1)
        self.assertContains(filtered, 'European Silver Fir')

        watered = self.client.get(reverse('scraper:plant_list'), {'watering': 'Average'})
        self.assertEqual(watered.context['page_obj'].paginator.count, 1)
        self.assertContains(watered, 'Red Filigree Lace Japanese Maple')

    def test_plant_detail_view(self):
        response = self.client.get(reverse('scraper:plant_detail', args=[self.fir.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Abies alba')
        self.assertContains(response, 'Frequent')

    def test_trigger_scraping_validation_and_method(self):
        get_resp = self.client.get(reverse('scraper:trigger_scraping'))
        self.assertEqual(get_resp.status_code, 405)

        bad_resp = self.client.post(
            reverse('scraper:trigger_scraping'),
            data=json.dumps({'max_plants': 0}),
            content_type='application/json',
        )
        self.assertEqual(bad_resp.status_code, 400)

        with patch('scraper.views._is_scraper_running', return_value=True):
            conflict_resp = self.client.post(
                reverse('scraper:trigger_scraping'),
                data=json.dumps({'max_plants': 5}),
                content_type='application/json',
            )
            self.assertEqual(conflict_resp.status_code, 409)

        with patch('scraper.views._is_scraper_running', return_value=False), patch(
            'scraper.views.threading.Thread'
        ) as mock_thread:
            ok_resp = self.client.post(
                reverse('scraper:trigger_scraping'),
                data=json.dumps({'max_plants': 5}),
                content_type='application/json',
            )
            self.assertEqual(ok_resp.status_code, 200)
            mock_thread.return_value.start.assert_called_once()

    def test_status_logs_stop_and_clear_endpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_log = Path(tmpdir) / 'scraper.log'
            tmp_flag = Path(tmpdir) / 'stop_scraping.flag'
            tmp_log.write_text('line 1\nline 2\n', encoding='utf-8')

            with patch.object(views, 'LOG_FILE', tmp_log), patch.object(
                views, 'STOP_FLAG_FILE', tmp_flag
            ), override_settings(MEDIA_ROOT=tmpdir):
                status_resp = self.client.get(reverse('scraper:get_scraping_status'))
                self.assertEqual(status_resp.status_code, 200)
                self.assertEqual(status_resp.json()['total_plants'], 2)

                logs_resp = self.client.get(reverse('scraper:get_scraping_logs'))
                self.assertIn('line 2', logs_resp.json()['logs'])

                stop_resp = self.client.post(reverse('scraper:trigger_scraping_stop'))
                self.assertEqual(stop_resp.status_code, 200)
                self.assertTrue(tmp_flag.exists())

                clear_logs_resp = self.client.post(reverse('scraper:clear_scraping_logs'))
                self.assertEqual(clear_logs_resp.status_code, 200)
                self.assertEqual(tmp_log.read_text(encoding='utf-8'), '')
                self.assertFalse(tmp_flag.exists())

                plants_dir = Path(tmpdir) / 'plants'
                plants_dir.mkdir(parents=True)
                dummy_img = plants_dir / 'sample.jpg'
                dummy_img.write_bytes(b'img')

                clear_data_resp = self.client.post(reverse('scraper:clear_all_plants'))
                self.assertEqual(clear_data_resp.status_code, 200)
                self.assertEqual(Plant.objects.count(), 0)
                self.assertFalse(dummy_img.exists())
