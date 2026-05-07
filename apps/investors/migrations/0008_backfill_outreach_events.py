"""Create one OutreachEvent per Person that already has outreach_sent_at."""
from django.db import migrations


def backfill(apps, schema_editor):
    Person = apps.get_model("investors", "Person")
    OutreachEvent = apps.get_model("investors", "OutreachEvent")
    qs = Person.objects.filter(outreach_sent_at__isnull=False)
    created = 0
    for p in qs.iterator():
        channel = p.outreach_channel or "other"
        if not OutreachEvent.objects.filter(
            person=p, channel=channel, direction="out"
        ).exists():
            OutreachEvent.objects.create(
                person=p,
                channel=channel,
                direction="out",
                sent_at=p.outreach_sent_at,
                notes="(backfilled from Person.outreach_sent_at)",
            )
            created += 1
        if p.replied_at and not OutreachEvent.objects.filter(
            person=p, direction="in"
        ).exists():
            OutreachEvent.objects.create(
                person=p,
                channel=channel,
                direction="in",
                sent_at=p.replied_at,
                notes="(backfilled from Person.replied_at)",
            )
            created += 1
    print(f"  Backfilled {created} OutreachEvent rows.")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("investors", "0007_outreachevent"),
    ]
    operations = [
        migrations.RunPython(backfill, noop),
    ]
