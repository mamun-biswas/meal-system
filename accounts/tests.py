from decimal import Decimal
import datetime

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import Member, Mess


class SeedCommandTests(TestCase):
    """Regression test: `manage.py seed` used to call
    MealMark.objects.get_or_create(..., defaults={'count': ...}), but 'count'
    is a Python @property, not a real field, raising FieldError at seed time."""

    def test_seed_command_runs_without_field_error(self):
        call_command('seed')
        from meals.models import MealMark
        self.assertTrue(Member.objects.exists())
        self.assertTrue(MealMark.objects.exists())
        # Every seeded mark should have at least one of the three slots set,
        # and the derived `.count` property should work without DB errors.
        for mark in MealMark.objects.all()[:5]:
            self.assertGreaterEqual(mark.count, Decimal('0'))

    def test_seed_special_day_is_single_meal_type(self):
        """The seeded special day must only boost ONE meal type, since that's the
        only shape the day-config dropdown UI can actually produce (selecting one
        meal type resets the other two to ×1.0)."""
        call_command('seed')
        from meals.models import DayConfig
        dc = DayConfig.objects.filter(label='Special Feast Day').first()
        self.assertIsNotNone(dc)
        self.assertEqual(dc.morning_multiplier, Decimal('1.00'))
        self.assertEqual(dc.lunch_multiplier, Decimal('1.00'))
        self.assertEqual(dc.dinner_multiplier, Decimal('2.00'))
        self.assertFalse(dc.is_uniform())
        self.assertTrue(dc.is_special())

    def test_seed_command_is_idempotent(self):
        call_command('seed')
        member_count_first = Member.objects.count()
        call_command('seed')
        self.assertEqual(Member.objects.count(), member_count_first)

    def test_seed_only_fills_days_1_through_15(self):
        """Meal marks, day locks, expenses, and deposits should only ever
        be seeded for days 1–15 of the current month, regardless of
        today's actual date in the month."""
        call_command('seed')
        from meals.models import MealMark, DayMealLock
        from finance.models import Deposit, Expense

        today = datetime.date.today()
        m, y = today.month, today.year
        expected_max_day = min(15, today.day)

        for qs, label in [
            (MealMark.objects.filter(date__month=m, date__year=y), 'MealMark'),
            (DayMealLock.objects.filter(date__month=m, date__year=y), 'DayMealLock'),
            (Expense.objects.filter(date__month=m, date__year=y), 'Expense'),
            (Deposit.objects.filter(date__month=m, date__year=y), 'Deposit'),
        ]:
            days = list(qs.values_list('date__day', flat=True))
            if not days:
                continue
            self.assertLessEqual(max(days), expected_max_day,
                                  f'{label} has a seeded date beyond day {expected_max_day}')
            self.assertGreaterEqual(min(days), 1, f'{label} has a seeded date before day 1')

    def test_seed_locks_all_three_slots_for_every_seeded_meal_day(self):
        """Seeded meal data represents already-committed history, so every
        day that got a MealMark must also be fully locked in DayMealLock —
        otherwise the Save Morning/Lunch/Dinner/All Meal buttons could
        silently overwrite seeded data with no protection."""
        call_command('seed')
        from meals.models import MealMark, DayMealLock

        today = datetime.date.today()
        m, y = today.month, today.year
        seeded_days = set(MealMark.objects.filter(date__month=m, date__year=y)
                           .values_list('date', flat=True))
        for date in seeded_days:
            lock = DayMealLock.objects.filter(date=date).first()
            self.assertIsNotNone(lock, f'No DayMealLock row for seeded day {date}')
            self.assertTrue(lock.is_fully_locked(), f'Day {date} is not fully locked')


class MealMarkAggregationTests(TestCase):
    """Regression tests: MealMark.count is a Python @property, not a DB
    column, so any ORM-level Sum('count') raises FieldError. Both
    Member.get_total_meals_all_time() and profile_view used to hit this."""

    def setUp(self):
        mess = Mess.objects.create(name='Test Mess')
        user = User.objects.create_user(username='regmember', password='pass1234')
        self.member = Member.objects.create(
            mess=mess, user=user, phone='01799990000', name='Reg Member',
        )

    def _mark(self, date, morning=1, lunch=1, dinner=1):
        from meals.models import MealMark
        return MealMark.objects.create(
            member=self.member, date=date,
            morning=Decimal(str(morning)), lunch=Decimal(str(lunch)), dinner=Decimal(str(dinner)),
        )

    def test_get_total_meals_all_time_no_marks(self):
        self.assertEqual(self.member.get_total_meals_all_time(), Decimal('0'))

    def test_get_total_meals_all_time_sums_morning_lunch_dinner(self):
        self._mark(datetime.date(2026, 1, 1), morning=1, lunch=1, dinner=1)   # 3
        self._mark(datetime.date(2026, 1, 2), morning=0.5, lunch=1, dinner=0)  # 1.5
        self.assertEqual(self.member.get_total_meals_all_time(), Decimal('4.5'))

    def test_profile_view_does_not_raise_field_error(self):
        self._mark(datetime.date.today(), morning=1, lunch=1, dinner=1)
        self.client.login(username='regmember', password='pass1234')
        resp = self.client.get(reverse('profile'))
        self.assertEqual(resp.status_code, 200)

    def test_profile_view_this_month_meals_correct(self):
        today = datetime.date.today()
        self._mark(today, morning=1, lunch=1, dinner=1)  # 3 meals this month
        self.client.login(username='regmember', password='pass1234')
        resp = self.client.get(reverse('profile'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['this_month_meals'], Decimal('3'))
