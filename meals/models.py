from django.db import models
from decimal import Decimal
import datetime, calendar

from accounts.models import Mess, MessScopedManager, MemberMessScopedManager


class MonthlySettings(models.Model):
    mess         = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='monthly_settings')
    month        = models.PositiveSmallIntegerField()
    year         = models.PositiveSmallIntegerField()
    cooking_cost = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('500'))
    is_closed    = models.BooleanField(default=False)
    closed_at    = models.DateTimeField(null=True, blank=True)
    notes        = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ('mess', 'month','year')
        ordering = ['-year','-month']

    def __str__(self):
        return f"{calendar.month_name[self.month]} {self.year}"

    def month_name(self):
        return calendar.month_name[self.month]


class DayConfig(models.Model):
    mess               = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='day_configs')
    date               = models.DateField()
    morning_multiplier = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'))
    lunch_multiplier   = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'))
    dinner_multiplier  = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('1.00'))
    label              = models.CharField(max_length=100, blank=True)
    note               = models.TextField(blank=True)
    created_by         = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True)
    created_at         = models.DateTimeField(auto_now_add=True)

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ('mess', 'date')
        ordering = ['-date']

    def __str__(self):
        return (f"{self.date} M×{self.morning_multiplier} "
                f"L×{self.lunch_multiplier} D×{self.dinner_multiplier} {self.label}")

    def is_special(self):
        one = Decimal('1.00')
        return (self.morning_multiplier != one or
                self.lunch_multiplier != one or
                self.dinner_multiplier != one)

    def is_uniform(self):
        """True when all three slots share the same multiplier (simple display case)."""
        return self.morning_multiplier == self.lunch_multiplier == self.dinner_multiplier

    @property
    def special_multiplier(self):
        """Backward-compat helper: single representative multiplier.

        Returns the common value when all three slots match; otherwise the
        highest of the three, which is the most conservative single number
        to surface anywhere a single multiplier is still displayed.
        """
        if self.is_uniform():
            return self.morning_multiplier
        return max(self.morning_multiplier, self.lunch_multiplier, self.dinner_multiplier)


class MealCountSettings(models.Model):
    """Per-mess settings controlling how many *effective* meals a single
    meal MARK is worth, independently per slot.

    Example: morning_weight = 0.5 means every 1 morning meal mark counts
    as 0.5 effective meals (1 x 0.5 = 0.5). lunch_weight = 2 means every
    1 lunch meal mark counts as 2 effective meals. This is one row per
    Mess (each mess configures its own conversion factors) - NOT
    month-scoped and NOT date-scoped.

    It combines MULTIPLICATIVELY with any per-day DayConfig multiplier
    already in effect for a special day:

        effective_count = mark_value x slot_weight x day_multiplier

    So on a normal day (DayConfig multiplier x1.0) with morning_weight
    0.5, marking 1 morning meal yields 0.5 effective meals. On a special
    day where the Manager also set a x2 morning multiplier for that
    date, the same 1 morning mark would yield 1 x 0.5 x 2 = 1.0
    effective meals.

    Changing these weights affects the ENTIRE calculation system
    immediately for every past and future date within that mess, because
    every effective meal computation (dashboard, meal mark grid, monthly
    matrix, reports, exports, member/my statements, API) reads from the
    single choke point `MealMark.effective_count()`, which reads this
    row live for the mark's mess.
    """
    mess           = models.OneToOneField(Mess, on_delete=models.CASCADE, related_name='meal_count_settings')
    morning_weight = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('1.000'))
    lunch_weight   = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('1.000'))
    dinner_weight  = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal('1.000'))

    updated_by = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Meal Count Settings'
        verbose_name_plural = 'Meal Count Settings'

    def __str__(self):
        return (f"Meal Count Weights: Morning x{self.morning_weight} "
                f"Lunch x{self.lunch_weight} Dinner x{self.dinner_weight}")

    @classmethod
    def load(cls, mess):
        """Always returns the settings row for this mess, creating it on first use."""
        obj, _ = cls.objects.get_or_create(mess=mess)
        return obj

    def is_uniform(self):
        """True when all three slots share the same weight (1 mark = same value everywhere)."""
        return self.morning_weight == self.lunch_weight == self.dinner_weight


class MealMark(models.Model):
    member    = models.ForeignKey('accounts.Member', on_delete=models.CASCADE, related_name='meal_marks')
    date      = models.DateField()
    morning   = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'))
    lunch     = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'))
    dinner    = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'))
    note      = models.CharField(max_length=200, blank=True)
    marked_by = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='marked_meals')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MemberMessScopedManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ('member','date')
        ordering = ['date']
        indexes = [models.Index(fields=['date']), models.Index(fields=['member','date'])]

    def __str__(self):
        return f"{self.member.name} | {self.date} | {self.count}"

    @property
    def count(self):
        """Total meals = morning + lunch + dinner"""
        return self.morning + self.lunch + self.dinner

    def effective_count(self, day_config_map=None, weights=None):
        """Total effective meals = sum over slots of (marked count x
        per-mess count-setting weight x per-day special multiplier).

        The per-mess weight (MealCountSettings, e.g. morning 1 mark = 0.5
        meal) and the per-day special multiplier (DayConfig, e.g. Eid
        day = x2) combine multiplicatively, independently per slot.
        Looked up explicitly by this mark's own mess (not the request's
        current mess) so it's correct even outside a request context.

        By default (no args) this looks up the DayConfig and
        MealCountSettings with a fresh query each call — correct but
        expensive (2+ extra queries) when called once per row across a
        loop of many MealMark rows, i.e. an N+1 pattern. Callers that
        iterate over many marks for the *same* mess should instead build
        the lookups once via `MealMark.preload_calc_context(mess)` and
        pass the results in here as `day_config_map`/`weights`, which
        makes every call in the loop query-free.
        """
        mess = self.member.mess
        if day_config_map is not None:
            cfg = day_config_map.get(self.date)
        else:
            cfg = DayConfig.all_objects.filter(mess=mess, date=self.date).first()
        if cfg:
            d_morning, d_lunch, d_dinner = cfg.morning_multiplier, cfg.lunch_multiplier, cfg.dinner_multiplier
        else:
            d_morning = d_lunch = d_dinner = Decimal('1.00')

        if weights is None:
            weights = MealCountSettings.load(mess)
        m_morning = weights.morning_weight * d_morning
        m_lunch   = weights.lunch_weight   * d_lunch
        m_dinner  = weights.dinner_weight  * d_dinner

        total = (self.morning * m_morning) + (self.lunch * m_lunch) + (self.dinner * m_dinner)
        return total.quantize(Decimal('0.01'))

    def is_special_day(self, day_config_map=None):
        """True only when THIS date has a Special Meal Entry (DayConfig)
        multiplier in effect that differs from x1.00 on at least one
        slot. Deliberately independent of the per-mess Meal Count
        Settings weight — a lunch weight of 2.0 makes every day's
        effective count differ from the raw count, but that's not a
        "special day" and should NOT trigger the ⚡ special-day
        indicator anywhere in the UI (My Statement, Member Statement,
        etc). Only an actual Special Meal Entry for this specific date
        should.

        Accepts an optional preloaded `day_config_map` (see
        `effective_count`) to avoid a query per call in loops.
        """
        if day_config_map is not None:
            cfg = day_config_map.get(self.date)
        else:
            cfg = DayConfig.all_objects.filter(mess=self.member.mess, date=self.date).first()
        return bool(cfg and cfg.is_special())

    def slot_multipliers(self, day_config_map=None, weights=None):
        """Returns (morning, lunch, dinner) COMBINED multipliers (per-mess
        count-setting weight x per-day DayConfig multiplier) in effect
        for this mark's date. This is what effective_count() actually
        multiplies the marked morning/lunch/dinner values by."""
        mess = self.member.mess
        if day_config_map is not None:
            cfg = day_config_map.get(self.date)
        else:
            cfg = DayConfig.all_objects.filter(mess=mess, date=self.date).first()
        if cfg:
            d_morning, d_lunch, d_dinner = cfg.morning_multiplier, cfg.lunch_multiplier, cfg.dinner_multiplier
        else:
            d_morning = d_lunch = d_dinner = Decimal('1.00')
        if weights is None:
            weights = MealCountSettings.load(mess)
        return (weights.morning_weight * d_morning,
                weights.lunch_weight * d_lunch,
                weights.dinner_weight * d_dinner)

    def multiplier(self, day_config_map=None):
        """Backward-compat: a single representative multiplier for this date.
        Prefer slot_multipliers() for anything that needs per-meal-type detail."""
        if day_config_map is not None:
            cfg = day_config_map.get(self.date)
        else:
            cfg = DayConfig.all_objects.filter(mess=self.member.mess, date=self.date).first()
        return cfg.special_multiplier if cfg else Decimal('1.00')

    @classmethod
    def preload_calc_context(cls, mess, dates=None):
        """Builds the (day_config_map, weights) pair used by
        `effective_count()` / `is_special_day()` / `slot_multipliers()` /
        `multiplier()` to avoid the N+1 query-per-row pattern that occurs
        when those methods are called in a loop over many MealMark rows
        for the same mess.

        Fetches every DayConfig for the mess in a single query (optionally
        narrowed to `dates`, an iterable of `datetime.date`) plus the
        mess's single MealCountSettings row, and returns them ready to be
        passed straight into the methods above. Two queries total,
        regardless of how many MealMark rows are subsequently processed.
        """
        qs = DayConfig.all_objects.filter(mess=mess)
        if dates is not None:
            qs = qs.filter(date__in=list(dates))
        day_config_map = {cfg.date: cfg for cfg in qs}
        weights = MealCountSettings.load(mess)
        return day_config_map, weights


class DayMealLock(models.Model):
    """Tracks which meal slots (morning/lunch/dinner) have already been
    committed ("saved") for a given date, across the whole house.

    A MealMark row is per-member, so it can't by itself answer "has
    morning been committed for everyone on June 29 yet?" — this is the
    single source of truth for that, one row per date, with the three
    slots locked independently. Once a slot is locked it can only be
    saved again after a Manager/Sub-Manager explicitly unlocks it (e.g.
    via the existing Update History tool) — the Save buttons on the Meal
    Mark page will not re-save an already-locked slot for that day.
    """
    mess            = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='day_meal_locks')
    date            = models.DateField()
    morning_locked  = models.BooleanField(default=False)
    lunch_locked    = models.BooleanField(default=False)
    dinner_locked   = models.BooleanField(default=False)
    morning_by      = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    lunch_by        = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    dinner_by       = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    morning_at      = models.DateTimeField(null=True, blank=True)
    lunch_at        = models.DateTimeField(null=True, blank=True)
    dinner_at       = models.DateTimeField(null=True, blank=True)

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ('mess', 'date')
        ordering = ['-date']

    def __str__(self):
        locks = []
        if self.morning_locked: locks.append('M')
        if self.lunch_locked:   locks.append('L')
        if self.dinner_locked:  locks.append('D')
        return f"{self.date} locked: {''.join(locks) or '—'}"

    def is_fully_locked(self):
        return self.morning_locked and self.lunch_locked and self.dinner_locked

    def locked_slots(self):
        slots = []
        if self.morning_locked: slots.append('morning')
        if self.lunch_locked:   slots.append('lunch')
        if self.dinner_locked:  slots.append('dinner')
        return slots


class MealMarkDraft(models.Model):
    """Date-independent 'pending' meal counts per member.

    This is intentionally NOT tied to a date. It represents whatever was
    last typed in the Meal Mark screen for a member via the per-member
    Save button. Selecting any date in the Day Entry tab ALWAYS pre-fills
    the morning/lunch/dinner inputs from this draft — regardless of
    whether that date already has a committed MealMark. Saving a whole
    day (Save Day) commits the on-screen values into MealMark for that
    specific date — it never writes to this table, so the draft keeps
    carrying forward to whatever date is opened next until someone
    changes it again.
    """
    member     = models.OneToOneField('accounts.Member', on_delete=models.CASCADE, related_name='meal_draft')
    morning    = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'))
    lunch      = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'))
    dinner     = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('0.0'))
    updated_by = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['member__name']

    def __str__(self):
        return f"Draft | {self.member.name} | M:{self.morning} L:{self.lunch} D:{self.dinner}"

    @property
    def count(self):
        return self.morning + self.lunch + self.dinner


class MemberInputSettings(models.Model):
    """Per-mess switch controlling whether plain Members are allowed to
    input their own meals (into MealMarkDraft) from the Meal Mark page —
    with an INDEPENDENT on/off + daily time window for each of morning,
    lunch, and dinner. A Manager/Sub-Manager can, for example, allow
    lunch input 8 AM–11 AM while keeping dinner input closed until 6 PM,
    entirely separately per meal type.

    One row per Mess — this is an access-control toggle each mess
    configures for itself, not month-scoped data.
    """
    mess = models.OneToOneField(Mess, on_delete=models.CASCADE, related_name='member_input_settings')

    morning_enabled = models.BooleanField(default=False)
    morning_start   = models.TimeField(default=datetime.time(6, 0))
    morning_end     = models.TimeField(default=datetime.time(9, 0))

    lunch_enabled   = models.BooleanField(default=False)
    lunch_start     = models.TimeField(default=datetime.time(11, 0))
    lunch_end       = models.TimeField(default=datetime.time(14, 0))

    dinner_enabled  = models.BooleanField(default=False)
    dinner_start    = models.TimeField(default=datetime.time(21, 0))
    dinner_end      = models.TimeField(default=datetime.time(23, 59, 59))

    updated_by   = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Member Input Settings'
        verbose_name_plural = 'Member Input Settings'

    def __str__(self):
        parts = []
        for slot in ('morning', 'lunch', 'dinner'):
            enabled = getattr(self, f'{slot}_enabled')
            start = getattr(self, f'{slot}_start')
            end = getattr(self, f'{slot}_end')
            parts.append(f"{slot}={'ON' if enabled else 'OFF'} ({start}–{end})")
        return "Member Input: " + ", ".join(parts)

    @classmethod
    def load(cls, mess):
        """Always returns the settings row for this mess, creating it on first use."""
        obj, _ = cls.objects.get_or_create(mess=mess)
        return obj

    @staticmethod
    def _crosses_midnight(start, end):
        return end <= start

    def _is_slot_open_at(self, slot, dt_time):
        enabled = getattr(self, f'{slot}_enabled')
        if not enabled:
            return False
        start = getattr(self, f'{slot}_start')
        end = getattr(self, f'{slot}_end')
        if self._crosses_midnight(start, end):
            return dt_time >= start or dt_time <= end
        return start <= dt_time <= end

    def is_slot_open_now(self, slot):
        return self._is_slot_open_at(slot, datetime.datetime.now().time())

    def is_morning_open_now(self):
        return self.is_slot_open_now('morning')

    def is_lunch_open_now(self):
        return self.is_slot_open_now('lunch')

    def is_dinner_open_now(self):
        return self.is_slot_open_now('dinner')

    def open_slots_now(self):
        """Returns which of ['morning','lunch','dinner'] are currently
        open for self-input, independent of any per-day commit locks."""
        return [s for s in ('morning', 'lunch', 'dinner') if self.is_slot_open_now(s)]


class Announcement(models.Model):
    PRIORITY = [('low','Low'),('normal','Normal'),('high','High'),('urgent','Urgent')]
    mess      = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='announcements')
    title     = models.CharField(max_length=200)
    body      = models.TextField()
    priority  = models.CharField(max_length=10, choices=PRIORITY, default='normal')
    posted_by = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-created_at']
