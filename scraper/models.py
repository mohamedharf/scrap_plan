from django.db import models

class Plant(models.Model):
    common_name = models.CharField(max_length=255)
    scientific_name = models.CharField(max_length=255)
    alternative_name = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    cycle = models.CharField(max_length=255, null=True, blank=True)
    watering = models.CharField(max_length=255, null=True, blank=True)
    sunlight = models.CharField(max_length=255, null=True, blank=True)
    hardiness_zone = models.CharField(max_length=255, null=True, blank=True)
    flowers = models.CharField(max_length=255, null=True, blank=True)
    soil = models.CharField(max_length=255, null=True, blank=True)
    cones = models.CharField(max_length=255, null=True, blank=True)
    leaf = models.CharField(max_length=255, null=True, blank=True)
    growth_rate = models.CharField(max_length=255, null=True, blank=True)
    maintenance = models.CharField(max_length=255, null=True, blank=True)
    care_level = models.CharField(max_length=255, null=True, blank=True)
    source_url = models.URLField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.common_name} ({self.scientific_name})"

class PlantImage(models.Model):
    plant = models.ForeignKey(Plant, related_name='images', on_delete=models.CASCADE)
    image = models.ImageField(upload_to='plants/')
    source_image_url = models.URLField(max_length=1000)

    def __str__(self):
        return f"Image for {self.plant.common_name}"
