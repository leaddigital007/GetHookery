from django.urls import path

from . import views

urlpatterns = [
    path("api/contact", views.contact_submit, name="contact_submit"),
]
