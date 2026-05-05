"""Django Admin configuration for the investor CRM."""
from __future__ import annotations

import csv
import re

from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.db import models
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html
from import_export.admin import ImportExportModelAdmin

from .models import (
    Company,
    ContactSubmission,
    Deal,
    Fund,
    FundTier,
    Investment,
    Note,
    Person,
    PipelineStage,
    PortfolioMention,
    Tag,
    Task,
    Warmth,
)
from .resources import (
    CompanyResource,
    DealResource,
    FundResource,
    InvestmentResource,
    PersonResource,
    TagResource,
)


admin.site.site_header = "Kubricon Investor CRM"
admin.site.site_title = "Kubricon CRM"
admin.site.index_title = "Investor pipeline"
admin.site.index_template = "admin/kubricon_index.html"


class NoteInline(GenericTabularInline):
    model = Note
    extra = 0
    fields = ("body", "created_by", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("created_by",)


class PersonInline(admin.TabularInline):
    model = Person
    fk_name = "fund"
    extra = 0
    fields = (
        "full_name",
        "role",
        "email",
        "pipeline_stage",
        "warmth",
        "email_status",
    )
    show_change_link = True


class InvestmentInlineForFund(admin.TabularInline):
    model = Investment
    fk_name = "fund"
    extra = 0
    autocomplete_fields = ("deal",)
    fields = ("deal", "is_lead", "notes")


class InvestmentInlineForDeal(admin.TabularInline):
    model = Investment
    fk_name = "deal"
    extra = 0
    autocomplete_fields = ("fund",)
    fields = ("fund", "is_lead", "notes")


class DealInline(admin.TabularInline):
    model = Deal
    extra = 0
    fields = ("stage", "amount_usd", "announced_at", "source_url")
    show_change_link = True


@admin.register(Tag)
class TagAdmin(ImportExportModelAdmin):
    resource_classes = [TagResource]
    list_display = ("name", "kind", "slug", "created_at")
    list_filter = ("kind",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.action(description="Mark as Researched")
def action_set_researched(modeladmin, request, queryset):
    updated = queryset.update(
        pipeline_stage=PipelineStage.RESEARCHED, pipeline_changed_at=timezone.now()
    )
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Mark as Contacted")
def action_set_contacted(modeladmin, request, queryset):
    updated = queryset.update(
        pipeline_stage=PipelineStage.CONTACTED, pipeline_changed_at=timezone.now()
    )
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Mark as Replied")
def action_set_replied(modeladmin, request, queryset):
    now = timezone.now()
    updated = queryset.update(
        pipeline_stage=PipelineStage.REPLIED,
        pipeline_changed_at=now,
        replied_at=now,
    )
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Mark as Passed")
def action_set_passed(modeladmin, request, queryset):
    updated = queryset.update(
        pipeline_stage=PipelineStage.PASSED, pipeline_changed_at=timezone.now()
    )
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


def _make_outreach_action(channel_value: str, label: str, *, followup_days: int = 7):
    """Build an action that records outreach sent via a specific channel.

    Sets pipeline_stage=CONTACTED, outreach_sent_at=now, outreach_channel and
    schedules next_followup_at unless the row already has a manual override.
    """

    @admin.action(description=f"Sent via {label} (advance to Contacted)")
    def _action(modeladmin, request, queryset):
        now = timezone.now()
        followup = now + timezone.timedelta(days=followup_days)
        updated = queryset.update(
            outreach_channel=channel_value,
            outreach_sent_at=now,
            pipeline_stage=PipelineStage.CONTACTED,
            pipeline_changed_at=now,
            next_followup_at=followup,
        )
        modeladmin.message_user(
            request,
            f"{updated} marked sent via {label}, follow-up in {followup_days}d.",
            messages.SUCCESS,
        )

    _action.__name__ = f"action_outreach_{channel_value or 'none'}"
    return _action


action_outreach_form = _make_outreach_action("form", "submission form")
action_outreach_email = _make_outreach_action("email", "email")
action_outreach_li_dm = _make_outreach_action("li_dm", "LinkedIn DM")
action_outreach_x_dm = _make_outreach_action("x_dm", "X / Twitter DM")
action_outreach_intro = _make_outreach_action("intro", "warm intro")


@admin.action(description="Schedule follow-up: in 7 days")
def action_followup_7d(modeladmin, request, queryset):
    when = timezone.now() + timezone.timedelta(days=7)
    updated = queryset.update(next_followup_at=when)
    modeladmin.message_user(request, f"{updated} scheduled.", messages.SUCCESS)


@admin.action(description="Schedule follow-up: in 14 days")
def action_followup_14d(modeladmin, request, queryset):
    when = timezone.now() + timezone.timedelta(days=14)
    updated = queryset.update(next_followup_at=when)
    modeladmin.message_user(request, f"{updated} scheduled.", messages.SUCCESS)


@admin.action(description="Clear follow-up date")
def action_followup_clear(modeladmin, request, queryset):
    updated = queryset.update(next_followup_at=None)
    modeladmin.message_user(request, f"{updated} cleared.", messages.SUCCESS)


@admin.action(description="Export selected with channel context (CSV)")
def action_export_outreach_csv(modeladmin, request, queryset):
    """One row per Person with everything needed to draft outreach offline."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        'attachment; filename="kubricon-outreach-batch.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            "Person",
            "Role",
            "Fund",
            "Tier",
            "Submission URL",
            "Contact email",
            "Email",
            "Twitter",
            "LinkedIn",
            "Pipeline stage",
            "Channel",
            "Sent at",
            "Replied at",
            "Next follow-up",
            "Fund thesis (truncated)",
        ]
    )
    qs = queryset.select_related("fund")
    for p in qs.iterator():
        fund = p.fund
        writer.writerow(
            [
                p.full_name,
                p.role,
                fund.name if fund else "",
                fund.tier if fund else "",
                fund.submission_url if fund else "",
                fund.contact_email if fund else "",
                p.email,
                f"@{p.twitter_handle}" if p.twitter_handle else "",
                p.linkedin_url,
                p.pipeline_stage,
                p.outreach_channel,
                p.outreach_sent_at.isoformat() if p.outreach_sent_at else "",
                p.replied_at.isoformat() if p.replied_at else "",
                p.next_followup_at.isoformat() if p.next_followup_at else "",
                ((fund.thesis_summary or "")[:240]) if fund else "",
            ]
        )
    modeladmin.message_user(
        request, f"Exported {qs.count()} rows.", messages.SUCCESS
    )
    return response


@admin.action(description="Set warmth: Warm (1st-degree)")
def action_set_warm_1st(modeladmin, request, queryset):
    updated = queryset.update(warmth=Warmth.WARM_1ST)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Set warmth: Warm (2nd-degree)")
def action_set_warm_2nd(modeladmin, request, queryset):
    updated = queryset.update(warmth=Warmth.WARM_2ND)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


def _make_tier_action(tier, label):
    @admin.action(description=f"Set tier: {label}")
    def _action(modeladmin, request, queryset):
        updated = queryset.update(tier=tier)
        modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)

    _action.__name__ = f"action_tier_{tier}"
    return _action


action_tier_s = _make_tier_action(FundTier.S, "Tier S (direct fit)")
action_tier_1 = _make_tier_action(FundTier.T1, "Tier 1 (broad AI)")
action_tier_2 = _make_tier_action(FundTier.T2, "Tier 2 (pre-seed)")
action_tier_watch = _make_tier_action(FundTier.WATCH, "Watch list")


_LLM_SCORE_RE = re.compile(r"score=(\d+)")
_LLM_ACTIVE_RE = re.compile(r"active=(True|False)")


def _llm_score(text: str | None) -> int | None:
    if not text:
        return None
    m = _LLM_SCORE_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _llm_is_active(text: str | None) -> bool | None:
    if not text:
        return None
    m = _LLM_ACTIVE_RE.search(text)
    if not m:
        return None
    return m.group(1) == "True"


class LLMScoreFilter(admin.SimpleListFilter):
    """Bucket Funds by LLM relevance score (parsed from internal_notes)."""

    title = "LLM score"
    parameter_name = "llm_score"

    def lookups(self, request, model_admin):
        return (
            ("80plus", "80+ (top fit)"),
            ("60to79", "60–79 (strong fit)"),
            ("40to59", "40–59 (medium)"),
            ("under40", "Under 40"),
            ("missing", "No LLM score yet"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value is None:
            return queryset
        if value == "missing":
            return queryset.exclude(internal_notes__contains="LLM-score:")
        ranges = {
            "80plus": (80, 100),
            "60to79": (60, 79),
            "40to59": (40, 59),
            "under40": (0, 39),
        }
        lo, hi = ranges[value]
        # Best-effort scan: filter on internal_notes containing any
        # `score=NN` between lo and hi. We do a Python-side filter
        # because parsing inside SQL would be fragile.
        ids = [
            f.id
            for f in queryset.only("id", "internal_notes").iterator()
            if (s := _llm_score(f.internal_notes)) is not None and lo <= s <= hi
        ]
        return queryset.filter(id__in=ids)


class LLMActiveFilter(admin.SimpleListFilter):
    """Filter Funds the LLM marked as currently active vs dormant."""

    title = "LLM active"
    parameter_name = "llm_active"

    def lookups(self, request, model_admin):
        return (
            ("true", "Active"),
            ("false", "Dormant"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "true":
            return queryset.filter(internal_notes__contains="active=True")
        if value == "false":
            return queryset.filter(internal_notes__contains="active=False")
        return queryset


class SubmissionChannelFilter(admin.SimpleListFilter):
    """Filter Funds by whether we know how to submit a pitch to them."""

    title = "Submission channel"
    parameter_name = "submission"

    def lookups(self, request, model_admin):
        return (
            ("has_url", "Has submission URL"),
            ("has_email", "Has contact email"),
            ("has_any", "Has URL or email"),
            ("missing", "No URL and no email"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        has_url = ~models.Q(submission_url="")
        has_email = ~models.Q(contact_email="")
        if value == "has_url":
            return queryset.filter(has_url)
        if value == "has_email":
            return queryset.filter(has_email)
        if value == "has_any":
            return queryset.filter(has_url | has_email)
        if value == "missing":
            return queryset.filter(submission_url="", contact_email="")
        return queryset


@admin.action(description="Export selected to CSV")
def action_export_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        'attachment; filename="kubricon-funds-export.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(
        [
            "Name",
            "Tier",
            "LLM score",
            "LLM active",
            "HQ city",
            "HQ country",
            "Check min USD",
            "Check max USD",
            "Stages",
            "Thesis tags",
            "Website",
            "Source",
            "Last activity",
        ]
    )
    for f in queryset.iterator():
        writer.writerow(
            [
                f.name,
                f.tier,
                _llm_score(f.internal_notes) or "",
                {True: "active", False: "dormant", None: ""}[
                    _llm_is_active(f.internal_notes)
                ],
                f.hq_city,
                f.hq_country,
                f.check_min_usd or "",
                f.check_max_usd or "",
                ", ".join(str(s) for s in (f.stages or [])),
                ", ".join(t.slug for t in f.thesis_tags.all()),
                f.website,
                f.source,
                f.last_activity_at.isoformat() if f.last_activity_at else "",
            ]
        )
    modeladmin.message_user(
        request, f"Exported {queryset.count()} funds.", messages.SUCCESS
    )
    return response


class PortfolioMentionInlineForFund(admin.TabularInline):
    model = PortfolioMention
    fk_name = "fund"
    extra = 0
    fields = ("company", "source_url", "source_label")
    autocomplete_fields = ("company",)
    show_change_link = True
    verbose_name = "Portfolio mention"
    verbose_name_plural = "Portfolio mentions (heuristic)"


class PortfolioMentionInlineForCompany(admin.TabularInline):
    model = PortfolioMention
    fk_name = "company"
    extra = 0
    fields = ("fund", "source_url", "source_label")
    autocomplete_fields = ("fund",)
    show_change_link = True
    verbose_name = "Mentioned by"
    verbose_name_plural = "Mentioned by funds"


@admin.register(Fund)
class FundAdmin(ImportExportModelAdmin):
    resource_classes = [FundResource]
    list_display = (
        "name",
        "tier",
        "llm_score_display",
        "llm_active_display",
        "submission_display",
        "thesis_chips",
        "hq_display",
        "check_range",
        "stage_display",
        "people_count",
        "portfolio_count",
        "source",
    )
    list_filter = (
        "tier",
        LLMScoreFilter,
        LLMActiveFilter,
        SubmissionChannelFilter,
        "thesis_tags",
        "source",
        "hq_country",
    )
    search_fields = (
        "name",
        "slug",
        "website",
        "thesis_summary",
        "portfolio_notes",
        "submission_url",
        "contact_email",
    )
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("thesis_tags",)
    filter_horizontal = ("thesis_tags",)
    date_hierarchy = "last_activity_at"
    inlines = (
        PersonInline,
        InvestmentInlineForFund,
        PortfolioMentionInlineForFund,
        NoteInline,
    )
    actions = (
        action_tier_s,
        action_tier_1,
        action_tier_2,
        action_tier_watch,
        action_export_csv,
    )
    fieldsets = (
        (
            "Identity",
            {"fields": ("name", "slug", "website", "hq_country", "hq_city")},
        ),
        (
            "Investment profile",
            {
                "fields": (
                    "tier",
                    "stages",
                    "check_min_usd",
                    "check_max_usd",
                    "aum_text",
                    "thesis_tags",
                )
            },
        ),
        (
            "Thesis & portfolio",
            {"fields": ("thesis_summary", "portfolio_notes", "last_activity_at")},
        ),
        (
            "Outreach channel",
            {"fields": ("submission_url", "contact_email")},
        ),
        (
            "Source",
            {"fields": ("source", "source_url", "internal_notes")},
        ),
        (
            "Audit",
            {"classes": ("collapse",), "fields": ("created_at", "updated_at")},
        ),
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="HQ", ordering="hq_country")
    def hq_display(self, obj: Fund) -> str:
        bits = [b for b in (obj.hq_city, obj.hq_country) if b]
        return ", ".join(bits) or "-"

    @admin.display(description="Check $")
    def check_range(self, obj: Fund) -> str:
        if not obj.check_min_usd and not obj.check_max_usd:
            return "-"
        lo = f"${obj.check_min_usd:,}" if obj.check_min_usd else "?"
        hi = f"${obj.check_max_usd:,}" if obj.check_max_usd else "?"
        return f"{lo} – {hi}"

    @admin.display(description="Stages")
    def stage_display(self, obj: Fund) -> str:
        if not obj.stages:
            return "-"
        return ", ".join(str(s) for s in obj.stages)

    @admin.display(description="People")
    def people_count(self, obj: Fund) -> int:
        return obj.people.count()

    @admin.display(description="Portfolio")
    def portfolio_count(self, obj: Fund) -> int:
        return obj.portfolio_mentions.count()

    @admin.display(description="LLM score", ordering="updated_at")
    def llm_score_display(self, obj: Fund) -> str:
        score = _llm_score(obj.internal_notes)
        if score is None:
            return format_html('<span style="color:#bbb;">—</span>')
        if score >= 80:
            colour = "#b30000"
        elif score >= 60:
            colour = "#d97706"
        elif score >= 40:
            colour = "#2563eb"
        else:
            colour = "#666"
        return format_html(
            '<span style="font-weight:600;color:{};">{}</span>', colour, score
        )

    @admin.display(description="Active")
    def llm_active_display(self, obj: Fund) -> str:
        active = _llm_is_active(obj.internal_notes)
        if active is None:
            return format_html('<span style="color:#bbb;">—</span>')
        if active:
            return format_html('<span style="color:#15803d;">●</span>')
        return format_html('<span style="color:#991b1b;">●</span>')

    @admin.display(description="Submit")
    def submission_display(self, obj: Fund) -> str:
        chips = []
        if obj.submission_url:
            chips.append(
                format_html(
                    '<a href="{}" target="_blank" rel="noopener" '
                    'style="display:inline-block;padding:1px 6px;border-radius:8px;'
                    "background:#dcfce7;color:#166534;border:1px solid #bbf7d0;"
                    'font-size:11px;text-decoration:none;">form</a>',
                    obj.submission_url,
                )
            )
        if obj.contact_email:
            chips.append(
                format_html(
                    '<span style="display:inline-block;padding:1px 6px;border-radius:8px;'
                    "background:#dbeafe;color:#1e40af;border:1px solid #bfdbfe;"
                    'font-size:11px;">email</span>'
                )
            )
        if not chips:
            return format_html('<span style="color:#bbb;">—</span>')
        return format_html("".join(chips))

    @admin.display(description="Thesis")
    def thesis_chips(self, obj: Fund) -> str:
        tags = list(obj.thesis_tags.all()[:6])
        if not tags:
            return format_html('<span style="color:#999;">—</span>')
        chips = "".join(
            format_html(
                '<span style="display:inline-block;padding:1px 6px;margin:1px 2px;'
                "border-radius:10px;background:#eef;color:#225;border:1px solid #ccd;"
                'font-size:11px;">{}</span>',
                t.name,
            )
            for t in tags
        )
        return format_html(chips)


class FollowupDueFilter(admin.SimpleListFilter):
    """Surface persons whose follow-up is due now or overdue."""

    title = "Follow-up due"
    parameter_name = "followup_due"

    def lookups(self, request, model_admin):
        return (
            ("overdue", "Overdue (past)"),
            ("today", "Due today"),
            ("week", "Due this week"),
            ("any", "Has follow-up scheduled"),
            ("none", "No follow-up scheduled"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        now = timezone.now()
        if value == "overdue":
            return queryset.filter(next_followup_at__lt=now)
        if value == "today":
            return queryset.filter(
                next_followup_at__date=now.date()
            )
        if value == "week":
            in_a_week = now + timezone.timedelta(days=7)
            return queryset.filter(
                next_followup_at__gte=now, next_followup_at__lte=in_a_week
            )
        if value == "any":
            return queryset.filter(next_followup_at__isnull=False)
        if value == "none":
            return queryset.filter(next_followup_at__isnull=True)
        return queryset


class OutreachStatusFilter(admin.SimpleListFilter):
    title = "Outreach status"
    parameter_name = "outreach_status"

    def lookups(self, request, model_admin):
        return (
            ("never", "Never contacted"),
            ("sent_no_reply", "Sent, awaiting reply"),
            ("replied", "Replied"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "never":
            return queryset.filter(outreach_sent_at__isnull=True)
        if value == "sent_no_reply":
            return queryset.filter(
                outreach_sent_at__isnull=False, replied_at__isnull=True
            )
        if value == "replied":
            return queryset.filter(replied_at__isnull=False)
        return queryset


@admin.register(Person)
class PersonAdmin(ImportExportModelAdmin):
    resource_classes = [PersonResource]
    list_display = (
        "full_name",
        "fund",
        "role",
        "outreach_status_display",
        "channel_display",
        "next_followup_at",
        "email",
        "pipeline_stage",
        "warmth",
        "email_status",
        "links",
    )
    list_filter = (
        "pipeline_stage",
        "warmth",
        "email_status",
        OutreachStatusFilter,
        FollowupDueFilter,
        "outreach_channel",
        "fund__tier",
    )
    search_fields = (
        "full_name",
        "email",
        "twitter_handle",
        "linkedin_url",
        "role",
        "fund__name",
    )
    autocomplete_fields = ("fund",)
    list_select_related = ("fund",)
    inlines = (NoteInline,)
    actions = (
        action_set_researched,
        action_set_contacted,
        action_set_replied,
        action_set_passed,
        action_outreach_form,
        action_outreach_email,
        action_outreach_li_dm,
        action_outreach_x_dm,
        action_outreach_intro,
        action_followup_7d,
        action_followup_14d,
        action_followup_clear,
        action_set_warm_1st,
        action_set_warm_2nd,
        action_export_outreach_csv,
    )
    fieldsets = (
        (
            "Identity",
            {"fields": ("full_name", "fund", "role", "location", "bio_short")},
        ),
        (
            "Channels",
            {"fields": ("email", "email_status", "twitter_handle", "linkedin_url")},
        ),
        (
            "Pipeline",
            {"fields": ("pipeline_stage", "pipeline_changed_at", "warmth")},
        ),
        (
            "Outreach tracker",
            {
                "fields": (
                    "outreach_channel",
                    "outreach_sent_at",
                    "outreach_text",
                    "replied_at",
                    "next_followup_at",
                )
            },
        ),
        ("Notes", {"fields": ("internal_notes",)}),
        (
            "Audit",
            {"classes": ("collapse",), "fields": ("created_at", "updated_at")},
        ),
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Outreach", ordering="outreach_sent_at")
    def outreach_status_display(self, obj: Person) -> str:
        if obj.replied_at:
            return format_html(
                '<span style="color:#15803d;font-weight:600;">replied</span>'
            )
        if obj.outreach_sent_at:
            now = timezone.now()
            days = (now - obj.outreach_sent_at).days
            if obj.next_followup_at and obj.next_followup_at < now:
                colour = "#b91c1c"
                label = f"sent {days}d • follow-up overdue"
            else:
                colour = "#1d4ed8"
                label = f"sent {days}d ago"
            return format_html(
                '<span style="color:{};">{}</span>', colour, label
            )
        return format_html('<span style="color:#9ca3af;">—</span>')

    @admin.display(description="Channel")
    def channel_display(self, obj: Person) -> str:
        if not obj.outreach_channel:
            return format_html('<span style="color:#bbb;">—</span>')
        return obj.get_outreach_channel_display()

    @admin.display(description="Links")
    def links(self, obj: Person) -> str:
        bits: list[str] = []
        if obj.twitter_handle:
            bits.append(
                format_html(
                    '<a href="https://x.com/{}" target="_blank" rel="noopener">X</a>',
                    obj.twitter_handle,
                )
            )
        if obj.linkedin_url:
            bits.append(
                format_html(
                    '<a href="{}" target="_blank" rel="noopener">LI</a>',
                    obj.linkedin_url,
                )
            )
        return format_html(" · ".join(bits)) if bits else "-"


@admin.register(Company)
class CompanyAdmin(ImportExportModelAdmin):
    resource_classes = [CompanyResource]
    list_display = (
        "name",
        "hq",
        "category_chips",
        "mentioned_count",
        "deal_count",
        "is_kubricon_competitor",
    )
    list_filter = ("is_kubricon_competitor", "category_tags", "mentioned_by_funds__fund__tier")
    search_fields = ("name", "description", "relevance_to_kubricon")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("category_tags",)
    filter_horizontal = ("category_tags",)
    inlines = (DealInline, PortfolioMentionInlineForCompany, NoteInline)

    @admin.display(description="Deals")
    def deal_count(self, obj: Company) -> int:
        return obj.deals.count()

    @admin.display(description="In portfolio of")
    def mentioned_count(self, obj: Company) -> int:
        return obj.mentioned_by_funds.count()

    @admin.display(description="Categories")
    def category_chips(self, obj: Company) -> str:
        tags = list(obj.category_tags.all()[:6])
        if not tags:
            return format_html('<span style="color:#999;">—</span>')
        chips = "".join(
            format_html(
                '<span style="display:inline-block;padding:1px 6px;margin:1px 2px;'
                "border-radius:10px;background:#efe;color:#252;border:1px solid #cdc;"
                'font-size:11px;">{}</span>',
                t.name,
            )
            for t in tags
        )
        return format_html(chips)


@admin.register(PortfolioMention)
class PortfolioMentionAdmin(admin.ModelAdmin):
    list_display = ("fund", "company", "source_label", "created_at")
    list_filter = ("source_label", "fund__tier")
    search_fields = ("fund__name", "company__name", "source_url")
    autocomplete_fields = ("fund", "company")
    list_select_related = ("fund", "company")


@admin.register(Deal)
class DealAdmin(ImportExportModelAdmin):
    resource_classes = [DealResource]
    list_display = ("company", "stage", "amount_usd", "announced_at")
    list_filter = ("stage", "announced_at")
    search_fields = ("company__name", "notes", "source_url")
    autocomplete_fields = ("company",)
    list_select_related = ("company",)
    date_hierarchy = "announced_at"
    inlines = (InvestmentInlineForDeal, NoteInline)


@admin.register(Investment)
class InvestmentAdmin(ImportExportModelAdmin):
    resource_classes = [InvestmentResource]
    list_display = ("fund", "deal", "is_lead")
    list_filter = ("is_lead", "fund__tier")
    search_fields = ("fund__name", "deal__company__name", "notes")
    autocomplete_fields = ("fund", "deal")
    list_select_related = ("fund", "deal", "deal__company")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "status",
        "due_date",
        "related_fund",
        "related_person",
        "assignee",
    )
    list_filter = ("status", "assignee")
    search_fields = (
        "title",
        "body",
        "related_fund__name",
        "related_person__full_name",
    )
    autocomplete_fields = ("related_fund", "related_person", "assignee")
    list_select_related = ("related_fund", "related_person", "assignee")
    date_hierarchy = "due_date"


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "body_short", "created_by", "created_at")
    search_fields = ("body",)
    list_filter = ("content_type",)
    autocomplete_fields = ("created_by",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Body")
    def body_short(self, obj: Note) -> str:
        text = obj.body or ""
        return text if len(text) <= 80 else text[:77] + "..."


@admin.action(description="Mark as processed")
def action_mark_processed(modeladmin, request, queryset):
    updated = queryset.update(processed=True)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.register(ContactSubmission)
class ContactSubmissionAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "website", "processed", "created_at")
    list_filter = ("processed", "created_at")
    search_fields = ("name", "email", "website", "message")
    date_hierarchy = "created_at"
    readonly_fields = (
        "name",
        "email",
        "website",
        "revenue",
        "message",
        "user_agent",
        "ip_address",
        "created_at",
        "updated_at",
    )
    fields = (
        "processed",
        "name",
        "email",
        "website",
        "revenue",
        "message",
        "user_agent",
        "ip_address",
        "created_at",
        "updated_at",
    )
    actions = (action_mark_processed,)
