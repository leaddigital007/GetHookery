from django.apps import AppConfig


class SiteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.site"
    label = "site_landing"
    verbose_name = "Public landing"
