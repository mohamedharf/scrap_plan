from django.contrib import admin

from .models import Plant, PlantImage


class PlantImageInline(admin.TabularInline):
    """Display downloaded plant images inline inside the Plant admin page."""

    model = PlantImage
    extra = 0
    readonly_fields = ('source_image_url',)


@admin.register(Plant)
class PlantAdmin(admin.ModelAdmin):
    """Admin configuration for scraped plant species."""

    list_display = (
        'common_name',
        'scientific_name',
        'cycle',
        'watering',
        'sunlight',
        'care_level',
        'created_at',
    )
    list_filter = ('cycle', 'watering', 'care_level')
    search_fields = ('common_name', 'scientific_name', 'alternative_name', 'description')
    readonly_fields = ('created_at',)
    inlines = [PlantImageInline]


@admin.register(PlantImage)
class PlantImageAdmin(admin.ModelAdmin):
    """Admin configuration for plant images."""

    list_display = ('plant', 'image', 'source_image_url')
    search_fields = ('plant__common_name', 'plant__scientific_name')
