from django.urls import path
from . import views

app_name = 'scraper'

urlpatterns = [
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('plants/', views.PlantListView.as_view(), name='plant_list'),
    path('plant/<int:pk>/', views.PlantDetailView.as_view(), name='plant_detail'),
    path('trigger/', views.trigger_scraping, name='trigger_scraping'),
    path('stop/', views.trigger_scraping_stop, name='trigger_scraping_stop'),
    path('status/', views.get_scraping_status, name='get_scraping_status'),
    path('logs/', views.get_scraping_logs, name='get_scraping_logs'),
    path('clear-logs/', views.clear_scraping_logs, name='clear_scraping_logs'),
    path('clear-data/', views.clear_all_plants, name='clear_all_plants'),
]
