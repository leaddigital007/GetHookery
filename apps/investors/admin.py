"""Django Admin configuration for the investor CRM."""
from __future__ import annotations

from django.contrib import admin, messages
from django.contrib.contenttypes.admin import GenericTabularInline
from django.utils import timezone
from django.utils.html import format_html
from import_export.admin import ImportExportModelAdmin

from .models import (
    Company,
    ContactSubmission,
    Deal,
    Fund,
    Investment,
    Note,
    Person,
    PipelineStage,
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
    updated = queryset.update(
        pipeline_stage=PipelineStage.REPLIED, pipeline_changed_at=timezone.now()
    )
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Mark as Passed")
def action_set_passed(modeladmin, request, queryset):
    updated = queryset.update(
        pipeline_stage=PipelineStage.PASSED, pipeline_changed_at=timezone.now()
    )
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Set warmth: Warm (1st-degree)")
def action_set_warm_1st(modeladmin, request, queryset):
    updated = queryset.update(warmth=Warmth.WARM_1ST)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.action(description="Set warmth: Warm (2nd-degree)")
def action_set_warm_2nd(modeladmin, request, queryset):
    updated = queryset.update(warmth=Warmth.WARM_2ND)
    modeladmin.message_user(request, f"{updated} updated", messages.SUCCESS)


@admin.register(Fund)
class FundAdmin(ImportExportModelAdmin):
    resource_classes = [FundResource]
    list_display = (
        "name",
        "tier",
        "hq_display",
        "check_range",
        "last_activity_at",
        "source",
        "people_count",
    )
    list_filter = ("tier", "source", "hq_country", "thesis_tags")
    search_fields = ("name", "slug", "website", "thesis_summary", "portfolio_notes")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("thesis_tags",)
    date_hierarchy = "last_activity_at"
    inlines = (PersonInline, InvestmentInlineForFund, NoteInline)
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

    @admin.display(description="People")
    def people_count(self, obj: Fund) -> int:
        return obj.people.count()


@admin.register(Person)
class PersonAdmin(ImportExportModelAdmin):
    resource_classes = [PersonResource]
    list_display = (
        "full_name",
        "fund",
        "role",
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
        action_set_warm_1st,
        action_set_warm_2nd,
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
        ("Notes", {"fields": ("internal_notes",)}),
        (
            "Audit",
            {"classes": ("collapse",), "fields": ("created_at", "updated_at")},
        ),
    )
    readonly_fields = ("created_at", "updated_at")

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
        "is_kubricon_competitor",
        "deal_count",
    )
    list_filter = ("is_kubricon_competitor", "category_tags")
    search_fields = ("name", "description", "relevance_to_kubricon")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("category_tags",)
    inlines = (DealInline, NoteInline)

    @admin.display(description="Deals")
    def deal_count(self, obj: Company) -> int:
        return obj.deals.count()


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
