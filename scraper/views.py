import json
import shutil
import threading
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.http import JsonResponse
from django.views.generic import DetailView, ListView, TemplateView

from .models import Plant
from .services.perenual_scraper import (
    LOG_FILE,
    STOP_FLAG_FILE,
    PerenualScraper,
    logger,
)

MAX_PLANTS_LIMIT = 500
_scraper_lock = threading.Lock()


def _is_scraper_running():
    """Return True if a background scraper thread is currently alive."""
    return any(
        thread.name == 'scraper_thread' and thread.is_alive()
        for thread in threading.enumerate()
    )


def _read_log_tail(max_lines=100, default='No logs available yet.'):
    """Read the last `max_lines` lines from scraper.log safely."""
    if not LOG_FILE.exists():
        return default
    try:
        with LOG_FILE.open('r', encoding='utf-8') as log_fh:
            lines = log_fh.readlines()
        content = ''.join(lines[-max_lines:]).strip('\n')
        return content if content else default
    except OSError as exc:
        logger.warning('Unable to read scraper log file: %s', exc)
        return default


def _post_only_error():
    return JsonResponse(
        {'status': 'error', 'message': 'POST method required.'},
        status=405,
    )


class DashboardView(TemplateView):
    """Main control panel showing database stats, scraper controls, and live logs."""

    template_name = 'scraper/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_plants'] = Plant.objects.count()
        context['is_running'] = _is_scraper_running()
        context['initial_logs'] = _read_log_tail(
            max_lines=100,
            default='No logs available yet. Start a job to view live scraper logs.',
        )
        return context


class PlantListView(ListView):
    """Paginated, searchable grid of scraped plant species."""

    model = Plant
    template_name = 'scraper/plant_list.html'
    context_object_name = 'plants'
    paginate_by = 24

    def get_queryset(self):
        queryset = Plant.objects.prefetch_related('images').order_by('-created_at')
        query = self.request.GET.get('q', '').strip()
        watering = self.request.GET.get('watering', '').strip()

        if query:
            queryset = queryset.filter(
                Q(common_name__icontains=query)
                | Q(scientific_name__icontains=query)
                | Q(alternative_name__icontains=query)
                | Q(description__icontains=query)
            )
        if watering:
            queryset = queryset.filter(watering__iexact=watering)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search_query'] = self.request.GET.get('q', '').strip()
        context['selected_watering'] = self.request.GET.get('watering', '').strip()
        context['watering_options'] = (
            Plant.objects.exclude(watering__isnull=True)
            .exclude(watering='')
            .values_list('watering', flat=True)
            .distinct()
            .order_by('watering')
        )
        return context


class PlantDetailView(DetailView):
    """Detailed view displaying all scraped attributes and images for a plant."""

    model = Plant
    template_name = 'scraper/plant_detail.html'
    context_object_name = 'plant'

    def get_queryset(self):
        return Plant.objects.prefetch_related('images')


def trigger_scraping(request):
    """Start a background scraping job if one is not already active."""
    if request.method != 'POST':
        return _post_only_error()

    try:
        payload = json.loads(request.body) if request.body else {}
        max_plants = int(payload.get('max_plants', 10))
    except (json.JSONDecodeError, TypeError, ValueError):
        return JsonResponse(
            {'status': 'error', 'message': 'Invalid request payload.'},
            status=400,
        )

    if not 1 <= max_plants <= MAX_PLANTS_LIMIT:
        return JsonResponse(
            {
                'status': 'error',
                'message': f'max_plants must be between 1 and {MAX_PLANTS_LIMIT}.',
            },
            status=400,
        )

    def run_scraper(limit):
        try:
            scraper = PerenualScraper()
            scraper.start_scraping(max_scrolls=(limit // 10) + 2, max_plants=limit)
        except Exception as exc:
            logger.exception('Scraper job failed: %s', exc)

    with _scraper_lock:
        if _is_scraper_running():
            return JsonResponse(
                {'status': 'error', 'message': 'A scraping job is already running.'},
                status=409,
            )
        thread = threading.Thread(
            target=run_scraper,
            args=(max_plants,),
            name='scraper_thread',
            daemon=True,
        )
        thread.start()

    return JsonResponse(
        {
            'status': 'started',
            'message': f'Scraping started in background for up to {max_plants} plants.',
        }
    )


def get_scraping_logs(request):
    """Return the most recent lines from the scraper log file."""
    return JsonResponse({'logs': _read_log_tail(max_lines=50)})


def trigger_scraping_stop(request):
    """Create the stop flag file to gracefully halt the active scraping job."""
    if request.method != 'POST':
        return _post_only_error()

    STOP_FLAG_FILE.write_text('stop', encoding='utf-8')
    return JsonResponse(
        {'status': 'stopping', 'message': 'Stop signal sent to scraper.'}
    )


def get_scraping_status(request):
    """Return live scraper running status and current plant count."""
    return JsonResponse(
        {
            'is_running': _is_scraper_running(),
            'total_plants': Plant.objects.count(),
        }
    )


def clear_scraping_logs(request):
    """Truncate the scraper log file and remove any stale stop flag."""
    if request.method != 'POST':
        return _post_only_error()

    if LOG_FILE.exists():
        LOG_FILE.write_text('', encoding='utf-8')
    if STOP_FLAG_FILE.exists():
        STOP_FLAG_FILE.unlink()

    return JsonResponse({'status': 'cleared'})


def clear_all_plants(request):
    """Delete all stored Plant records and their downloaded media files."""
    if request.method != 'POST':
        return _post_only_error()

    Plant.objects.all().delete()

    plants_media_path = Path(settings.MEDIA_ROOT) / 'plants'
    if plants_media_path.exists():
        for entry in plants_media_path.iterdir():
            try:
                if entry.is_file() or entry.is_symlink():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
            except OSError as exc:
                logger.warning('Failed to delete %s: %s', entry, exc)

    return JsonResponse({'status': 'cleared'})
