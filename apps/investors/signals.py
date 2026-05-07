"""
Signals to keep Person.outreach_sent_at / outreach_channel in sync with
OutreachEvent rows.

Person.outreach_sent_at = MIN(events.sent_at) where direction = outbound
Person.outreach_channel = channel of LATEST outbound event
Person.replied_at = MAX(events.sent_at) where direction = reply
"""
from __future__ import annotations

from django.db.models import Max, Min
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import OutreachDirection, OutreachEvent, Person


def _resync_person(person_id: int) -> None:
    try:
        person = Person.objects.get(pk=person_id)
    except Person.DoesNotExist:
        return

    outbound = OutreachEvent.objects.filter(
        person_id=person_id, direction=OutreachDirection.OUTBOUND
    )
    reply = OutreachEvent.objects.filter(
        person_id=person_id, direction=OutreachDirection.REPLY
    )

    first_sent = outbound.aggregate(m=Min("sent_at"))["m"]
    latest_outbound = outbound.order_by("-sent_at").first()
    last_reply = reply.aggregate(m=Max("sent_at"))["m"]

    update_fields: list[str] = []
    new_sent_at = first_sent
    if person.outreach_sent_at != new_sent_at:
        person.outreach_sent_at = new_sent_at
        update_fields.append("outreach_sent_at")

    new_channel = latest_outbound.channel if latest_outbound else ""
    if person.outreach_channel != new_channel:
        person.outreach_channel = new_channel
        update_fields.append("outreach_channel")

    if person.replied_at != last_reply:
        person.replied_at = last_reply
        update_fields.append("replied_at")

    if update_fields:
        update_fields.append("updated_at")
        person.save(update_fields=update_fields)


@receiver(post_save, sender=OutreachEvent)
def on_event_saved(sender, instance: OutreachEvent, **kwargs):
    _resync_person(instance.person_id)


@receiver(post_delete, sender=OutreachEvent)
def on_event_deleted(sender, instance: OutreachEvent, **kwargs):
    _resync_person(instance.person_id)
