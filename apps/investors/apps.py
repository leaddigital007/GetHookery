from django.apps import AppConfig


class InvestorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.investors"
    label = "investors"
    verbose_name = "Investors CRM"

    def ready(self):  # noqa: D401
        from . import signals  # noqa: F401
