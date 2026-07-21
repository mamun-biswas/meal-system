from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Member, Mess
from finance.models import MealClosing, ClosingRecord


def get_test_mess():
    mess, _ = Mess.objects.get_or_create(name='Test Mess')
    return mess


def make_member(username, role=Member.ROLE_MEMBER, phone=None):
    user = User.objects.create_user(username=username, password='pass1234')
    return Member.objects.create(
        mess=get_test_mess(),
        user=user,
        phone=phone or f'017{username[-8:].rjust(8, "0")}',
        name=username.title(),
        role=role,
    )


class ClosingDeleteTests(TestCase):
    """Covers the new 'delete meal closing' feature end-to-end."""

    def setUp(self):
        self.manager = make_member('manager01', role=Member.ROLE_MANAGER, phone='01711000001')
        self.sub_manager = make_member('submanager01', role=Member.ROLE_SUB_MANAGER, phone='01711000002')
        self.member = make_member('member01', role=Member.ROLE_MEMBER, phone='01711000003')

        self.open_closing = MealClosing.objects.create(
            mess=get_test_mess(), month=1, year=2026, status=MealClosing.STATUS_OPEN,
            total_exp=1000, total_dep=1200, total_eff=50,
            meal_rate=20, cook_cost=100, fund_balance=200,
        )
        ClosingRecord.objects.create(
            closing=self.open_closing, member=self.member,
            eff_meals=25, meal_only_cost=500, cook_cost=100,
            total_cost=600, deposit=600, balance=0,
        )

        self.closed_closing = MealClosing.objects.create(
            mess=get_test_mess(),
            month=2, year=2026, status=MealClosing.STATUS_CLOSED,
            total_exp=2000, total_dep=2200, total_eff=80,
            meal_rate=25, cook_cost=150, fund_balance=200,
            closed_by=self.manager,
        )
        ClosingRecord.objects.create(
            closing=self.closed_closing, member=self.member,
            eff_meals=40, meal_only_cost=1000, cook_cost=150,
            total_cost=1150, deposit=1150, balance=0,
            verdict=ClosingRecord.VERDICT_SETTLED,
        )

    def delete_url(self, pk):
        return reverse('closing_delete', args=[pk])

    # ── Permission checks ────────────────────────────────────────────────
    def test_manager_can_delete_open_closing(self):
        self.client.login(username='manager01', password='pass1234')
        resp = self.client.post(self.delete_url(self.open_closing.pk))
        self.assertRedirects(resp, reverse('closing_list'))
        self.assertFalse(MealClosing.objects.filter(pk=self.open_closing.pk).exists())

    def test_manager_can_delete_closed_closing(self):
        self.client.login(username='manager01', password='pass1234')
        resp = self.client.post(self.delete_url(self.closed_closing.pk))
        self.assertRedirects(resp, reverse('closing_list'))
        self.assertFalse(MealClosing.objects.filter(pk=self.closed_closing.pk).exists())

    def test_sub_manager_with_monthly_close_perm_cannot_delete(self):
        from accounts.models import SubManagerPermission
        SubManagerPermission.objects.create(
            member=self.sub_manager, codename='monthly_close', granted=True
        )
        self.client.login(username='submanager01', password='pass1234')
        resp = self.client.post(self.delete_url(self.open_closing.pk))
        # Deletion is Manager-only even though sub-manager has monthly_close perm
        self.assertTrue(MealClosing.objects.filter(pk=self.open_closing.pk).exists())
        self.assertRedirects(resp, reverse('closing_detail', args=[self.open_closing.pk]))

    def test_plain_member_cannot_delete(self):
        self.client.login(username='member01', password='pass1234')
        resp = self.client.post(self.delete_url(self.open_closing.pk))
        self.assertTrue(MealClosing.objects.filter(pk=self.open_closing.pk).exists())
        # closing_delete denies and redirects to closing_detail, which itself
        # denies plain members and redirects on to dashboard.
        resp2 = self.client.post(self.delete_url(self.open_closing.pk), follow=True)
        self.assertRedirects(resp2, reverse('dashboard'))
        self.assertTrue(MealClosing.objects.filter(pk=self.open_closing.pk).exists())

    def test_anonymous_redirected_to_login(self):
        resp = self.client.post(self.delete_url(self.open_closing.pk))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.url)
        self.assertTrue(MealClosing.objects.filter(pk=self.open_closing.pk).exists())

    # ── HTTP method checks ───────────────────────────────────────────────
    def test_get_request_does_not_delete(self):
        self.client.login(username='manager01', password='pass1234')
        resp = self.client.get(self.delete_url(self.open_closing.pk))
        self.assertRedirects(resp, reverse('closing_detail', args=[self.open_closing.pk]))
        self.assertTrue(MealClosing.objects.filter(pk=self.open_closing.pk).exists())

    def test_delete_nonexistent_closing_404(self):
        self.client.login(username='manager01', password='pass1234')
        resp = self.client.post(self.delete_url(99999))
        self.assertEqual(resp.status_code, 404)

    # ── Cascade / data-consistency checks ────────────────────────────────
    def test_delete_cascades_to_closing_records(self):
        self.client.login(username='manager01', password='pass1234')
        rec_pk = self.closed_closing.records.first().pk
        self.client.post(self.delete_url(self.closed_closing.pk))
        self.assertFalse(ClosingRecord.objects.filter(pk=rec_pk).exists())

    def test_delete_does_not_affect_other_closings(self):
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.open_closing.pk))
        self.assertTrue(MealClosing.objects.filter(pk=self.closed_closing.pk).exists())
        self.assertEqual(ClosingRecord.objects.filter(closing=self.closed_closing).count(), 1)

    def test_delete_does_not_touch_deposits_or_expenses(self):
        from finance.models import Deposit, Expense
        import datetime
        Deposit.objects.create(member=self.member, amount=500, date=datetime.date(2026, 2, 5))
        Expense.objects.create(mess=get_test_mess(), date=datetime.date(2026, 2, 6), amount=300)
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.closed_closing.pk))
        self.assertEqual(Deposit.objects.count(), 1)
        self.assertEqual(Expense.objects.count(), 1)

    # ── Downstream view consistency after deletion ───────────────────────
    def test_closing_list_renders_after_delete(self):
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.closed_closing.pk))
        resp = self.client.get(reverse('closing_list'))
        self.assertEqual(resp.status_code, 200)
        # The deleted closing's row (with its Closed-By name) must be gone;
        # "February" alone also appears in the unrelated month-selector dropdown,
        # so check for the manager's name tied to that closing instead.
        self.assertNotContains(resp, self.manager.name + '</td>')
        self.assertEqual(resp.context['closings'].count(), 1)

    def test_member_statement_falls_back_gracefully_after_delete(self):
        """statement view must not 500 once the backing closing+record are gone."""
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.closed_closing.pk))
        resp = self.client.get(
            reverse('member_statement', args=[self.member.pk]),
            {'month': 2, 'year': 2026},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['closing_record'])

    def test_my_statement_falls_back_gracefully_after_delete(self):
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.closed_closing.pk))
        self.client.login(username='member01', password='pass1234')
        resp = self.client.get(reverse('my_statement'), {'month': 2, 'year': 2026})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context['closing_record'])

    def test_dashboard_renders_after_delete(self):
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.closed_closing.pk))
        resp = self.client.get(reverse('dashboard'))
        self.assertEqual(resp.status_code, 200)

    def test_recreating_closing_after_delete_for_same_month(self):
        """Deleting should free up the unique (month, year) constraint."""
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.open_closing.pk))
        self.assertFalse(MealClosing.objects.filter(month=1, year=2026).exists())
        # Recreate directly (bypassing compute_month_stats dependency) to confirm
        # the unique_together constraint no longer blocks it.
        new_closing = MealClosing.objects.create(
            mess=get_test_mess(),
            month=1, year=2026, status=MealClosing.STATUS_OPEN,
        )
        self.assertEqual(MealClosing.objects.filter(month=1, year=2026).count(), 1)
        self.assertEqual(new_closing.records.count(), 0)

    def test_success_message_on_delete(self):
        self.client.login(username='manager01', password='pass1234')
        resp = self.client.post(self.delete_url(self.open_closing.pk), follow=True)
        msgs = [str(m) for m in resp.context['messages']]
        self.assertTrue(any('deleted' in m.lower() for m in msgs))

    def test_activity_log_created_on_delete(self):
        from accounts.models import ActivityLog
        self.client.login(username='manager01', password='pass1234')
        self.client.post(self.delete_url(self.open_closing.pk))
        self.assertTrue(
            ActivityLog.objects.filter(action__icontains='DELETED').exists()
        )
