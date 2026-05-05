"""
Domain model for the Kubricon investor CRM.

The schema is intentionally narrow for phase 1: it captures the entities we
need to *collect data about* (funds, partners/angels, comparable companies,
their deals, who invested in them, internal notes/tasks, plus the public
landing contact submissions). Outreach-specific tables (sequences, emails,
opens, clicks, replies) are deferred to a later phase.
"""
from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class TagKind(models.TextChoices):
    THESIS = "thesis", "Thesis"
    CATEGORY = "category", "Category"


class Tag(TimestampedModel):
    """Reusable label applied to funds (thesis) or companies (category)."""

    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    kind = models.CharField(
        max_length=16, choices=TagKind.choices, default=TagKind.THESIS
    )

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_kind_display()})"


class FundTier(models.TextChoices):
    S = "S", "Tier S - direct thesis fit"
    T1 = "1", "Tier 1 - broad AI"
    T2 = "2", "Tier 2 - pre-seed friendly"
    WATCH = "watch", "Watch list"


class FundSource(models.TextChoices):
    MANUAL = "manual", "Manual"
    OPENVC = "openvc", "OpenVC"
    SEC_EDGAR = "sec_edgar", "SEC EDGAR"
    CRUNCHBASE = "crunchbase", "Crunchbase"
    SIGNAL_NFX = "signal_nfx", "Signal NFX"
    TWITTER = "twitter", "Twitter / X"
    GITHUB_AWESOME = "github_awesome", "GitHub awesome list"
    NEWSLETTER = "newsletter", "Newsletter"
    REFERRAL = "referral", "Referral"
    OTHER = "other", "Other"


class Fund(TimestampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=200, unique=True)
    website = models.URLField(blank=True)
    hq_country = models.CharField(max_length=80, blank=True)
    hq_city = models.CharField(max_length=120, blank=True)
    aum_text = models.CharField(
        max_length=120, blank=True, help_text="Free-form, e.g. $500M AUM"
    )
    check_min_usd = models.PositiveBigIntegerField(null=True, blank=True)
    check_max_usd = models.PositiveBigIntegerField(null=True, blank=True)
    tier = models.CharField(
        max_length=10, choices=FundTier.choices, default=FundTier.WATCH
    )
    stages = models.JSONField(
        default=list,
        blank=True,
        help_text="List of stages, e.g. ['pre-seed','seed','A']",
    )
    thesis_summary = models.TextField(blank=True)
    portfolio_notes = models.TextField(
        blank=True,
        help_text="Comparable bets in our category, e.g. led Runway Series B",
    )
    internal_notes = models.TextField(blank=True)
    last_activity_at = models.DateField(
        null=True, blank=True, help_text="Date of most recent known investment"
    )
    source = models.CharField(
        max_length=20, choices=FundSource.choices, default=FundSource.MANUAL
    )
    source_url = models.URLField(blank=True)
    thesis_tags = models.ManyToManyField(Tag, blank=True, related_name="funds")
    attached_notes = GenericRelation("Note", related_query_name="fund")

    class Meta:
        ordering = ["tier", "name"]
        indexes = [
            models.Index(fields=["tier"]),
            models.Index(fields=["last_activity_at"]),
        ]

    def __str__(self) -> str:
        return self.name


class EmailStatus(models.TextChoices):
    UNKNOWN = "unknown", "Unknown"
    VERIFIED = "verified", "Verified"
    BOUNCED = "bounced", "Bounced"
    UNSUBSCRIBED = "unsubscribed", "Unsubscribed"


class PipelineStage(models.TextChoices):
    IDENTIFIED = "identified", "Identified"
    RESEARCHED = "researched", "Researched"
    CONTACTED = "contacted", "Contacted"
    REPLIED = "replied", "Replied"
    MEETING = "meeting", "Meeting booked"
    DD = "dd", "Due diligence"
    TERM_SHEET = "term_sheet", "Term sheet"
    CLOSED_WON = "closed_won", "Closed - committed"
    CLOSED_LOST = "closed_lost", "Closed - passed"
    PASSED = "passed", "Passed (no fit)"


class Warmth(models.TextChoices):
    COLD = "cold", "Cold"
    WARM_2ND = "warm_2nd", "Warm (2nd-degree)"
    WARM_1ST = "warm_1st", "Warm (1st-degree)"


class Person(TimestampedModel):
    """Investor contact: a fund partner or a solo angel (fund=null)."""

    fund = models.ForeignKey(
        Fund,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="people",
    )
    full_name = models.CharField(max_length=200)
    role = models.CharField(
        max_length=120,
        blank=True,
        help_text="Partner / Principal / Founder / Angel / ...",
    )
    email = models.EmailField(blank=True)
    email_status = models.CharField(
        max_length=20, choices=EmailStatus.choices, default=EmailStatus.UNKNOWN
    )
    twitter_handle = models.CharField(
        max_length=80, blank=True, help_text="Without @"
    )
    linkedin_url = models.URLField(blank=True)
    location = models.CharField(max_length=120, blank=True)
    bio_short = models.TextField(blank=True)
    pipeline_stage = models.CharField(
        max_length=20,
        choices=PipelineStage.choices,
        default=PipelineStage.IDENTIFIED,
    )
    pipeline_changed_at = models.DateTimeField(null=True, blank=True)
    warmth = models.CharField(
        max_length=10, choices=Warmth.choices, default=Warmth.COLD
    )
    internal_notes = models.TextField(blank=True)
    attached_notes = GenericRelation("Note", related_query_name="person")

    class Meta:
        ordering = ["full_name"]
        indexes = [
            models.Index(fields=["pipeline_stage"]),
            models.Index(fields=["warmth"]),
            models.Index(fields=["email_status"]),
        ]

    def __str__(self) -> str:
        if self.fund:
            return f"{self.full_name} - {self.fund.name}"
        return f"{self.full_name} (angel)"


class Company(TimestampedModel):
    """Portfolio / comparable company we use as a thesis-fit signal."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=200, unique=True)
    website = models.URLField(blank=True)
    hq = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    is_kubricon_competitor = models.BooleanField(default=False)
    relevance_to_kubricon = models.TextField(
        blank=True,
        help_text="Why this portfolio company matters as a thesis-fit signal",
    )
    category_tags = models.ManyToManyField(Tag, blank=True, related_name="companies")
    attached_notes = GenericRelation("Note", related_query_name="company")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Companies"

    def __str__(self) -> str:
        return self.name


class Deal(TimestampedModel):
    """A funding round announcement for a Company."""

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="deals"
    )
    amount_usd = models.PositiveBigIntegerField(null=True, blank=True)
    stage = models.CharField(
        max_length=40, blank=True, help_text="e.g. Pre-seed, Seed, Series A"
    )
    announced_at = models.DateField(null=True, blank=True)
    source_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    attached_notes = GenericRelation("Note", related_query_name="deal")

    class Meta:
        ordering = ["-announced_at"]
        indexes = [
            models.Index(fields=["announced_at"]),
            models.Index(fields=["stage"]),
        ]

    def __str__(self) -> str:
        bits = [self.company.name]
        if self.stage:
            bits.append(self.stage)
        if self.announced_at:
            bits.append(self.announced_at.isoformat())
        return " - ".join(bits)


class Investment(TimestampedModel):
    """Through-table linking a Fund to a Deal it participated in."""

    fund = models.ForeignKey(
        Fund, on_delete=models.CASCADE, related_name="investments"
    )
    deal = models.ForeignKey(
        Deal, on_delete=models.CASCADE, related_name="investments"
    )
    is_lead = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-deal__announced_at"]
        constraints = [
            models.UniqueConstraint(fields=["fund", "deal"], name="uniq_fund_deal"),
        ]

    def __str__(self) -> str:
        flag = " (lead)" if self.is_lead else ""
        return f"{self.fund.name} -> {self.deal}{flag}"


class PortfolioMention(TimestampedModel):
    """
    A heuristic Fund <-> Company link harvested from `Fund.portfolio_notes`
    or other free-text sources (e.g. GitHub awesome lists, fund website).

    Distinct from `Investment` because we do not have a confirmed Deal — we
    just know the fund advertises this company in their portfolio. Useful
    for "what does this fund typically back?" and "which funds know about
    this company?" views in admin.
    """

    fund = models.ForeignKey(
        Fund, on_delete=models.CASCADE, related_name="portfolio_mentions"
    )
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="mentioned_by_funds"
    )
    source_url = models.URLField(
        blank=True,
        help_text="URL captured alongside the company name (usually the company website)",
    )
    source_label = models.CharField(
        max_length=40,
        default="github_awesome",
        help_text="Where this mention was harvested from",
    )
    raw_text = models.CharField(
        max_length=300,
        blank=True,
        help_text="Original text fragment that produced the mention",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["fund", "company"],
                name="uniq_fund_company_mention",
            ),
        ]
        indexes = [
            models.Index(fields=["fund"]),
            models.Index(fields=["company"]),
        ]

    def __str__(self) -> str:
        return f"{self.fund.name} mentions {self.company.name}"


class TaskStatus(models.TextChoices):
    OPEN = "open", "Open"
    DONE = "done", "Done"
    SNOOZED = "snoozed", "Snoozed"


class Task(TimestampedModel):
    title = models.CharField(max_length=255)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=TaskStatus.choices, default=TaskStatus.OPEN
    )
    related_fund = models.ForeignKey(
        Fund,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    related_person = models.ForeignKey(
        Person,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tasks",
    )
    body = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "due_date", "-created_at"]
        indexes = [
            models.Index(fields=["status", "due_date"]),
        ]

    def __str__(self) -> str:
        return self.title


class Note(TimestampedModel):
    """Free-form note attachable to any of: Fund, Person, Company, Deal."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    body = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="authored_notes",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"Note on {self.target}"


class ContactSubmission(TimestampedModel):
    """Submissions from the public landing's contact form.

    Phase 1 stores the payload; notifications and follow-up are added in the
    outreach phase.
    """

    name = models.CharField(max_length=200)
    email = models.EmailField()
    website = models.CharField(max_length=255, blank=True)
    revenue = models.CharField(max_length=120, blank=True)
    message = models.TextField(blank=True)
    processed = models.BooleanField(default=False)
    user_agent = models.CharField(max_length=500, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["processed", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"
