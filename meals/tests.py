from decimal import Decimal
import datetime
import json
import re
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Member, SubManagerPermission, Mess
from meals.models import MealMark, MealMarkDraft, MemberInputSettings, DayMealLock, DayConfig


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


class DayConfigModelTests(TestCase):
    """Per-meal-slot multiplier behaviour on the DayConfig/MealMark models."""

    def setUp(self):
        self.member = make_member('dcmember', phone='01788880001')

    def test_default_multipliers_are_one(self):
        dc = DayConfig.objects.create(mess=get_test_mess(), date=datetime.date(2026, 3, 1))
        self.assertEqual(dc.morning_multiplier, Decimal('1.00'))
        self.assertEqual(dc.lunch_multiplier, Decimal('1.00'))
        self.assertEqual(dc.dinner_multiplier, Decimal('1.00'))
        self.assertFalse(dc.is_special())

    def test_is_special_true_if_any_slot_differs(self):
        dc = DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 1),
            morning_multiplier=Decimal('1.00'), lunch_multiplier=Decimal('1.00'),
            dinner_multiplier=Decimal('2.00'),
        )
        self.assertTrue(dc.is_special())

    def test_is_uniform(self):
        dc_uniform = DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 1),
            morning_multiplier=Decimal('2.00'), lunch_multiplier=Decimal('2.00'),
            dinner_multiplier=Decimal('2.00'),
        )
        self.assertTrue(dc_uniform.is_uniform())
        dc_mixed = DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 2),
            morning_multiplier=Decimal('1.00'), lunch_multiplier=Decimal('2.00'),
            dinner_multiplier=Decimal('3.00'),
        )
        self.assertFalse(dc_mixed.is_uniform())

    def test_special_multiplier_backcompat_uniform(self):
        dc = DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 1),
            morning_multiplier=Decimal('2.00'), lunch_multiplier=Decimal('2.00'),
            dinner_multiplier=Decimal('2.00'),
        )
        self.assertEqual(dc.special_multiplier, Decimal('2.00'))

    def test_special_multiplier_backcompat_mixed_returns_max(self):
        dc = DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 1),
            morning_multiplier=Decimal('1.00'), lunch_multiplier=Decimal('1.50'),
            dinner_multiplier=Decimal('3.00'),
        )
        self.assertEqual(dc.special_multiplier, Decimal('3.00'))

    def test_effective_count_no_dayconfig_is_raw_total(self):
        mk = MealMark.objects.create(
            member=self.member, date=datetime.date(2026, 3, 10),
            morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        self.assertEqual(mk.effective_count(), Decimal('3.00'))

    def test_effective_count_applies_per_slot_multipliers_independently(self):
        DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 10),
            morning_multiplier=Decimal('1.00'),
            lunch_multiplier=Decimal('2.00'),
            dinner_multiplier=Decimal('3.00'),
        )
        mk = MealMark.objects.create(
            member=self.member, date=datetime.date(2026, 3, 10),
            morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        # 1*1 + 1*2 + 1*3 = 6
        self.assertEqual(mk.effective_count(), Decimal('6.00'))

    def test_effective_count_zero_meal_slot_unaffected_by_its_multiplier(self):
        DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 10),
            morning_multiplier=Decimal('5.00'),  # no morning meal marked, should contribute 0
            lunch_multiplier=Decimal('1.00'),
            dinner_multiplier=Decimal('1.00'),
        )
        mk = MealMark.objects.create(
            member=self.member, date=datetime.date(2026, 3, 10),
            morning=Decimal('0'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        self.assertEqual(mk.effective_count(), Decimal('2.00'))

    def test_slot_multipliers_helper(self):
        DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 3, 10),
            morning_multiplier=Decimal('1.50'),
            lunch_multiplier=Decimal('2.50'),
            dinner_multiplier=Decimal('3.50'),
        )
        mk = MealMark.objects.create(
            member=self.member, date=datetime.date(2026, 3, 10),
            morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        self.assertEqual(mk.slot_multipliers(), (Decimal('1.50'), Decimal('2.50'), Decimal('3.50')))

    def test_slot_multipliers_no_config_defaults_to_one(self):
        mk = MealMark.objects.create(
            member=self.member, date=datetime.date(2026, 3, 11),
            morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        self.assertEqual(mk.slot_multipliers(), (Decimal('1.00'), Decimal('1.00'), Decimal('1.00')))


class DayConfigSaveViewTests(TestCase):
    """The /meals/day-config/save/ endpoint with per-slot multiplier payloads."""

    def setUp(self):
        self.manager = make_member('dcmanager', role=Member.ROLE_MANAGER, phone='01788880002')
        self.sub_manager_allowed = make_member('dcsubok', role=Member.ROLE_SUB_MANAGER, phone='01788880003')
        self.sub_manager_denied = make_member('dcsubno', role=Member.ROLE_SUB_MANAGER, phone='01788880004')
        self.member = make_member('dcplain', phone='01788880005')

        from accounts.models import SubManagerPermission
        SubManagerPermission.objects.create(
            member=self.sub_manager_allowed, codename='day_config', granted=True
        )

    def save_url(self):
        return reverse('day_config_save')

    def test_manager_can_save_per_slot_multipliers(self):
        self.client.login(username='dcmanager', password='pass1234')
        resp = self.client.post(
            self.save_url(),
            data=json.dumps({
                'date': '2026-04-01',
                'morning_multiplier': 1.0,
                'lunch_multiplier': 2.0,
                'dinner_multiplier': 3.0,
                'label': 'Eid',
                'note': 'Special feast',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['morning_multiplier'], 1.0)
        self.assertEqual(body['lunch_multiplier'], 2.0)
        self.assertEqual(body['dinner_multiplier'], 3.0)

        dc = DayConfig.objects.get(date=datetime.date(2026, 4, 1))
        self.assertEqual(dc.morning_multiplier, Decimal('1.00'))
        self.assertEqual(dc.lunch_multiplier, Decimal('2.00'))
        self.assertEqual(dc.dinner_multiplier, Decimal('3.00'))
        self.assertEqual(dc.label, 'Eid')

    def test_sub_manager_with_perm_can_save(self):
        self.client.login(username='dcsubok', password='pass1234')
        resp = self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-02', 'morning_multiplier': 2.0,
                              'lunch_multiplier': 2.0, 'dinner_multiplier': 2.0}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(DayConfig.objects.filter(date=datetime.date(2026, 4, 2)).exists())

    def test_sub_manager_without_perm_denied(self):
        self.client.login(username='dcsubno', password='pass1234')
        resp = self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-02', 'morning_multiplier': 2.0,
                              'lunch_multiplier': 2.0, 'dinner_multiplier': 2.0}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DayConfig.objects.filter(date=datetime.date(2026, 4, 2)).exists())

    def test_plain_member_denied(self):
        self.client.login(username='dcplain', password='pass1234')
        resp = self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-02', 'morning_multiplier': 2.0,
                              'lunch_multiplier': 2.0, 'dinner_multiplier': 2.0}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DayConfig.objects.filter(date=datetime.date(2026, 4, 2)).exists())

    def test_zero_or_negative_multiplier_rejected(self):
        self.client.login(username='dcmanager', password='pass1234')
        for bad_value in [0, -1, -2.5]:
            resp = self.client.post(
                self.save_url(),
                data=json.dumps({'date': '2026-04-03', 'morning_multiplier': bad_value,
                                  'lunch_multiplier': 1.0, 'dinner_multiplier': 1.0}),
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 400)
            self.assertFalse(DayConfig.objects.filter(date=datetime.date(2026, 4, 3)).exists())

    def test_update_existing_day_config_changes_only_that_date(self):
        self.client.login(username='dcmanager', password='pass1234')
        self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-04', 'morning_multiplier': 1.0,
                              'lunch_multiplier': 1.0, 'dinner_multiplier': 1.0}),
            content_type='application/json',
        )
        self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-04', 'morning_multiplier': 1.0,
                              'lunch_multiplier': 5.0, 'dinner_multiplier': 1.0}),
            content_type='application/json',
        )
        self.assertEqual(DayConfig.objects.filter(date=datetime.date(2026, 4, 4)).count(), 1)
        dc = DayConfig.objects.get(date=datetime.date(2026, 4, 4))
        self.assertEqual(dc.lunch_multiplier, Decimal('5.00'))

    def test_missing_multiplier_defaults_to_one(self):
        """If the client omits a slot's multiplier entirely, it should fall back to 1.0."""
        self.client.login(username='dcmanager', password='pass1234')
        resp = self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-05', 'lunch_multiplier': 2.0}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        dc = DayConfig.objects.get(date=datetime.date(2026, 4, 5))
        self.assertEqual(dc.morning_multiplier, Decimal('1.00'))
        self.assertEqual(dc.lunch_multiplier, Decimal('2.00'))
        self.assertEqual(dc.dinner_multiplier, Decimal('1.00'))

    def test_single_meal_dropdown_workflow_dinner_only(self):
        """Mirrors the day-config UI: a single 'meal type' dropdown + one multiplier
        value; the other two slots are sent as 1.0 (their UI default)."""
        self.client.login(username='dcmanager', password='pass1234')
        resp = self.client.post(
            self.save_url(),
            data=json.dumps({
                'date': '2026-04-06',
                'morning_multiplier': 1.0,
                'lunch_multiplier': 1.0,
                'dinner_multiplier': 2.5,
                'label': 'Dinner Feast',
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        dc = DayConfig.objects.get(date=datetime.date(2026, 4, 6))
        self.assertEqual(dc.morning_multiplier, Decimal('1.00'))
        self.assertEqual(dc.lunch_multiplier, Decimal('1.00'))
        self.assertEqual(dc.dinner_multiplier, Decimal('2.50'))
        self.assertTrue(dc.is_special())

    def test_picking_a_new_meal_type_for_same_date_resets_previous_selection(self):
        """If the dropdown is used twice for the same date (different meal type each
        time), the second save overwrites the first — matches the UI's reset-others
        behaviour by design."""
        self.client.login(username='dcmanager', password='pass1234')
        # First: choose Lunch ×2.0
        self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-07', 'morning_multiplier': 1.0,
                              'lunch_multiplier': 2.0, 'dinner_multiplier': 1.0}),
            content_type='application/json',
        )
        # Then: choose Dinner ×3.0 for the same date (UI resets morning/lunch to 1.0)
        self.client.post(
            self.save_url(),
            data=json.dumps({'date': '2026-04-07', 'morning_multiplier': 1.0,
                              'lunch_multiplier': 1.0, 'dinner_multiplier': 3.0}),
            content_type='application/json',
        )
        dc = DayConfig.objects.get(date=datetime.date(2026, 4, 7))
        self.assertEqual(dc.lunch_multiplier, Decimal('1.00'))
        self.assertEqual(dc.dinner_multiplier, Decimal('3.00'))
        self.assertEqual(DayConfig.objects.filter(date=datetime.date(2026, 4, 7)).count(), 1)


class DayConfigDeleteTests(TestCase):
    def setUp(self):
        self.manager = make_member('dcdelmgr', role=Member.ROLE_MANAGER, phone='01788880006')
        self.dc = DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 5, 1),
            morning_multiplier=Decimal('1.0'), lunch_multiplier=Decimal('2.0'),
            dinner_multiplier=Decimal('2.0'), label='Test Day',
        )

    def test_delete_removes_config_and_reverts_to_default(self):
        self.client.login(username='dcdelmgr', password='pass1234')
        resp = self.client.delete(reverse('day_config_delete', args=[self.dc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DayConfig.objects.filter(pk=self.dc.pk).exists())

        member = make_member('dcdelmem', phone='01788880007')
        mk = MealMark.objects.create(
            member=member, date=datetime.date(2026, 5, 1),
            morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        # After deletion, no special multiplier applies — effective == raw count.
        self.assertEqual(mk.effective_count(), Decimal('3.00'))


class CombinedMonthStatsTests(TestCase):
    """End-to-end: per-slot multipliers must flow correctly into compute_month_stats,
    which underlies monthly closings, statements, and reports."""

    def setUp(self):
        self.member = make_member('statsmember', phone='01788880008')
        from finance.models import Deposit
        Deposit.objects.create(member=self.member, amount=Decimal('1000'),
                                date=datetime.date(2026, 6, 1))

    def test_compute_month_stats_uses_per_slot_multipliers(self):
        from meals.views import compute_month_stats
        DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 6, 1),
            morning_multiplier=Decimal('1.00'),
            lunch_multiplier=Decimal('1.00'),
            dinner_multiplier=Decimal('3.00'),  # only dinner boosted
        )
        MealMark.objects.create(
            member=self.member, date=datetime.date(2026, 6, 1),
            morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        stats = compute_month_stats(6, 2026)
        # eff = 1*1 + 1*1 + 1*3 = 5
        self.assertEqual(stats['total_eff'], Decimal('5.00'))
        self.assertEqual(stats['per_member'][0]['eff_meals'], Decimal('5.00'))
        # raw (unweighted) count must remain 3, unaffected by multipliers
        self.assertEqual(stats['per_member'][0]['raw_meals'], Decimal('3.0'))

    def test_compute_month_stats_without_dayconfig_matches_raw_count(self):
        from meals.views import compute_month_stats
        MealMark.objects.create(
            member=self.member, date=datetime.date(2026, 6, 2),
            morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
        )
        stats = compute_month_stats(6, 2026)
        self.assertEqual(stats['total_eff'], Decimal('3.00'))


class DayConfigPageRenderTests(TestCase):
    """Smoke tests that the day-config page and meal-mark page render without
    error after the per-slot-multiplier schema change."""

    def setUp(self):
        self.manager = make_member('dcrendermgr', role=Member.ROLE_MANAGER, phone='01788880009')
        DayConfig.objects.create(mess=get_test_mess(), 
            date=datetime.date(2026, 6, 5),
            morning_multiplier=Decimal('1.00'), lunch_multiplier=Decimal('2.00'),
            dinner_multiplier=Decimal('3.00'), label='Mixed Special Day',
        )

    def test_day_config_list_page_renders(self):
        self.client.login(username='dcrendermgr', password='pass1234')
        resp = self.client.get(reverse('day_config'), {'month': 6, 'year': 2026})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Mixed Special Day')
        self.assertContains(resp, '2.00')
        self.assertContains(resp, '3.00')

    def test_meal_mark_page_renders_with_mixed_day_config(self):
        self.client.login(username='dcrendermgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'), {'month': 6, 'year': 2026})
        self.assertEqual(resp.status_code, 200)
        # day_totals_js must include the per-slot multiplier fields
        self.assertContains(resp, 'multi_morning')
        self.assertContains(resp, 'multi_lunch')
        self.assertContains(resp, 'multi_dinner')


class MealMarkDraftModelTests(TestCase):
    """Date-independent draft storage behaviour."""

    def setUp(self):
        self.member = make_member('draftmember', phone='01788880010')

    def test_draft_defaults_to_zero(self):
        draft = MealMarkDraft.objects.create(member=self.member)
        self.assertEqual(draft.morning, Decimal('0.0'))
        self.assertEqual(draft.lunch, Decimal('0.0'))
        self.assertEqual(draft.dinner, Decimal('0.0'))
        self.assertEqual(draft.count, Decimal('0.0'))

    def test_one_draft_per_member(self):
        MealMarkDraft.objects.create(member=self.member, morning=Decimal('1.0'))
        with self.assertRaises(Exception):
            MealMarkDraft.objects.create(member=self.member, morning=Decimal('2.0'))


class MealSaveMemberDraftViewTests(TestCase):
    """The /meals/save-draft/ endpoint: per-member, date-independent."""

    def setUp(self):
        self.manager = make_member('draftmgr', role=Member.ROLE_MANAGER, phone='01788880011')
        self.sub_manager_allowed = make_member('draftsubok', role=Member.ROLE_SUB_MANAGER, phone='01788880012')
        self.sub_manager_denied = make_member('draftsubno', role=Member.ROLE_SUB_MANAGER, phone='01788880013')
        self.plain_member = make_member('draftplain', phone='01788880014')
        self.target = make_member('drafttarget', phone='01788880015')

        from accounts.models import SubManagerPermission
        SubManagerPermission.objects.create(
            member=self.sub_manager_allowed, codename='meal_mark', granted=True
        )

    def url(self):
        return reverse('meal_save_member_draft')

    def test_manager_can_save_draft_for_member(self):
        self.client.login(username='draftmgr', password='pass1234')
        resp = self.client.post(
            self.url(),
            data=json.dumps({'member_id': self.target.id, 'morning': 1, 'lunch': 2, 'dinner': 1}),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['morning'], 1.0)
        self.assertEqual(body['lunch'], 2.0)
        self.assertEqual(body['dinner'], 1.0)

        draft = MealMarkDraft.objects.get(member=self.target)
        self.assertEqual(draft.morning, Decimal('1.0'))
        self.assertEqual(draft.lunch, Decimal('2.0'))
        self.assertEqual(draft.dinner, Decimal('1.0'))

    def test_resave_overwrites_existing_draft_for_that_member_only(self):
        other = make_member('draftother', phone='01788880016')
        self.client.login(username='draftmgr', password='pass1234')
        self.client.post(self.url(), data=json.dumps(
            {'member_id': self.target.id, 'morning': 1, 'lunch': 1, 'dinner': 1}),
            content_type='application/json')
        self.client.post(self.url(), data=json.dumps(
            {'member_id': other.id, 'morning': 0, 'lunch': 1, 'dinner': 1}),
            content_type='application/json')
        # Rewrite target's draft
        self.client.post(self.url(), data=json.dumps(
            {'member_id': self.target.id, 'morning': 0, 'lunch': 0, 'dinner': 1}),
            content_type='application/json')

        target_draft = MealMarkDraft.objects.get(member=self.target)
        self.assertEqual(target_draft.morning, Decimal('0.0'))
        self.assertEqual(target_draft.lunch, Decimal('0.0'))
        self.assertEqual(target_draft.dinner, Decimal('1.0'))

        other_draft = MealMarkDraft.objects.get(member=other)
        self.assertEqual(other_draft.lunch, Decimal('1.0'))
        self.assertEqual(other_draft.dinner, Decimal('1.0'))

    def test_saving_draft_does_not_create_mealmark(self):
        self.client.login(username='draftmgr', password='pass1234')
        self.client.post(self.url(), data=json.dumps(
            {'member_id': self.target.id, 'morning': 1, 'lunch': 1, 'dinner': 1}),
            content_type='application/json')
        self.assertEqual(MealMark.objects.filter(member=self.target).count(), 0)

    def test_sub_manager_with_perm_can_save(self):
        self.client.login(username='draftsubok', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.target.id, 'morning': 1, 'lunch': 0, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)

    def test_sub_manager_without_perm_denied(self):
        self.client.login(username='draftsubno', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.target.id, 'morning': 1, 'lunch': 0, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(MealMarkDraft.objects.filter(member=self.target).exists())

    def test_plain_member_denied(self):
        self.client.login(username='draftplain', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.target.id, 'morning': 1, 'lunch': 0, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_negative_values_rejected(self):
        self.client.login(username='draftmgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.target.id, 'morning': -1, 'lunch': 0, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 400)


class MealMarkDraftIntegrationTests(TestCase):
    """End-to-end behaviour matching the date-independent draft workflow:
    saving a member's draft on one date, then viewing a different date,
    should show the draft values until Save Day commits them — at which
    point system calculations (compute_month_stats) must reflect them.
    """

    def setUp(self):
        self.manager = make_member('draftflowmgr', role=Member.ROLE_MANAGER, phone='01788880017')
        self.member = make_member('draftflowmem', phone='01788880018')

    def test_draft_values_appear_in_meal_mark_page_context(self):
        MealMarkDraft.objects.create(
            member=self.member, morning=Decimal('1.0'), lunch=Decimal('1.0'), dinner=Decimal('0.0'),
        )
        self.client.login(username='draftflowmgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'), {'month': 6, 'year': 2026})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '"morning": 1.0')

    def test_save_day_commits_independent_of_draft_and_draft_persists(self):
        # Member has a draft of M:1 L:1 D:0 (e.g. saved while looking at June 1)
        MealMarkDraft.objects.create(
            member=self.member, morning=Decimal('1.0'), lunch=Decimal('1.0'), dinner=Decimal('0.0'),
        )
        self.client.login(username='draftflowmgr', password='pass1234')

        # Save Day for June 2 using those same on-screen values (as the UI would
        # send after pre-filling inputs from the draft).
        resp = self.client.post(
            reverse('meal_save_day'),
            data=json.dumps({
                'day': 2, 'month': 6, 'year': 2026,
                'marks': {str(self.member.id): {'morning': 1, 'lunch': 1, 'dinner': 0}},
            }),
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 200)

        # System calculation (MealMark) now reflects June 2.
        mk = MealMark.objects.get(member=self.member, date=datetime.date(2026, 6, 2))
        self.assertEqual(mk.morning, Decimal('1.0'))
        self.assertEqual(mk.lunch, Decimal('1.0'))

        # June 1 was never committed — Save Day must not retroactively affect it.
        self.assertFalse(MealMark.objects.filter(member=self.member, date=datetime.date(2026, 6, 1)).exists())

        # The draft table is untouched by Save Day, so it still carries the
        # same pending values forward for whatever date is opened next.
        draft = MealMarkDraft.objects.get(member=self.member)
        self.assertEqual(draft.morning, Decimal('1.0'))
        self.assertEqual(draft.lunch, Decimal('1.0'))


class MemberInputSettingsModelTests(TestCase):
    """Time-window logic for the member self-input toggle — now PER MEAL
    TYPE (morning/lunch/dinner each independently configurable)."""

    def test_disabled_is_never_open(self):
        s = MemberInputSettings(morning_enabled=False,
                                 morning_start=datetime.time(21, 0), morning_end=datetime.time(23, 59))
        self.assertFalse(s._is_slot_open_at('morning', datetime.time(22, 0)))

    def test_enabled_simple_window_inside(self):
        s = MemberInputSettings(morning_enabled=True,
                                 morning_start=datetime.time(21, 0), morning_end=datetime.time(23, 59))
        self.assertTrue(s._is_slot_open_at('morning', datetime.time(22, 0)))
        self.assertTrue(s._is_slot_open_at('morning', datetime.time(21, 0)))
        self.assertTrue(s._is_slot_open_at('morning', datetime.time(23, 59)))

    def test_enabled_simple_window_outside(self):
        s = MemberInputSettings(morning_enabled=True,
                                 morning_start=datetime.time(21, 0), morning_end=datetime.time(23, 59))
        self.assertFalse(s._is_slot_open_at('morning', datetime.time(20, 59)))
        self.assertFalse(s._is_slot_open_at('morning', datetime.time(0, 0)))
        self.assertFalse(s._is_slot_open_at('morning', datetime.time(12, 0)))

    def test_window_crossing_midnight_9pm_to_midnight(self):
        # 9:00 PM to 12:00 AM (represented as 23:59:59, the latest valid TimeField value)
        s = MemberInputSettings(dinner_enabled=True,
                                 dinner_start=datetime.time(21, 0), dinner_end=datetime.time(23, 59, 59))
        self.assertTrue(s._is_slot_open_at('dinner', datetime.time(21, 30)))
        self.assertTrue(s._is_slot_open_at('dinner', datetime.time(23, 59, 59)))
        self.assertFalse(s._is_slot_open_at('dinner', datetime.time(20, 0)))
        self.assertFalse(s._is_slot_open_at('dinner', datetime.time(12, 0)))

    def test_window_crossing_midnight_into_next_day(self):
        # e.g. 9:00 PM to 1:00 AM — genuinely wraps past midnight
        s = MemberInputSettings(dinner_enabled=True,
                                 dinner_start=datetime.time(21, 0), dinner_end=datetime.time(1, 0))
        self.assertTrue(s._crosses_midnight(s.dinner_start, s.dinner_end))
        self.assertTrue(s._is_slot_open_at('dinner', datetime.time(22, 0)))   # before midnight
        self.assertTrue(s._is_slot_open_at('dinner', datetime.time(0, 30)))   # after midnight
        self.assertFalse(s._is_slot_open_at('dinner', datetime.time(12, 0)))  # daytime, closed

    def test_load_creates_singleton(self):
        self.assertEqual(MemberInputSettings.objects.count(), 0)
        s1 = MemberInputSettings.load(get_test_mess())
        s2 = MemberInputSettings.load(get_test_mess())
        self.assertEqual(MemberInputSettings.objects.count(), 1)
        self.assertEqual(s1.pk, s2.pk)

    def test_slots_are_fully_independent(self):
        # Morning open, lunch closed, dinner open with a different window —
        # each slot's enabled flag and window must not affect the others.
        s = MemberInputSettings(
            morning_enabled=True, morning_start=datetime.time(6, 0), morning_end=datetime.time(9, 0),
            lunch_enabled=False, lunch_start=datetime.time(11, 0), lunch_end=datetime.time(14, 0),
            dinner_enabled=True, dinner_start=datetime.time(20, 0), dinner_end=datetime.time(22, 0),
        )
        self.assertTrue(s._is_slot_open_at('morning', datetime.time(7, 0)))
        self.assertFalse(s._is_slot_open_at('lunch', datetime.time(12, 0)))  # disabled, even though time fits
        self.assertTrue(s._is_slot_open_at('dinner', datetime.time(21, 0)))
        self.assertFalse(s._is_slot_open_at('dinner', datetime.time(7, 0)))  # outside dinner's own window

    def test_open_slots_now_reflects_each_slot_independently(self):
        now = datetime.datetime.now().time()
        s = MemberInputSettings.objects.create(mess=get_test_mess(), 
            morning_enabled=True, morning_start=now, morning_end=now,
            lunch_enabled=False, lunch_start=now, lunch_end=now,
            dinner_enabled=True, dinner_start=now, dinner_end=now,
        )
        open_slots = s.open_slots_now()
        self.assertIn('morning', open_slots)
        self.assertNotIn('lunch', open_slots)
        self.assertIn('dinner', open_slots)


class MemberInputSettingsSaveViewTests(TestCase):
    """The /meals/member-input-settings/save/ endpoint and its permission
    gating (requirement: manager AND a permitted sub-manager can control
    each meal type's window independently)."""

    def setUp(self):
        self.manager = make_member('misavemgr', role=Member.ROLE_MANAGER, phone='01788880020')
        self.sub_allowed = make_member('misavesubok', role=Member.ROLE_SUB_MANAGER, phone='01788880021')
        self.sub_denied = make_member('misavesubno', role=Member.ROLE_SUB_MANAGER, phone='01788880022')
        self.plain = make_member('misaveplain', phone='01788880023')
        SubManagerPermission.objects.create(
            member=self.sub_allowed, codename='member_input_control', granted=True
        )

    def url(self):
        return reverse('member_input_settings_save')

    def _payload(self, morning=None, lunch=None, dinner=None):
        default = {'enabled': False, 'start': '00:00', 'end': '23:59'}
        return {
            'morning': morning or default,
            'lunch': lunch or default,
            'dinner': dinner or default,
        }

    def test_manager_can_turn_on_morning_with_window(self):
        self.client.login(username='misavemgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            self._payload(morning={'enabled': True, 'start': '06:00', 'end': '09:00'})
        ), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body['ok'])
        self.assertTrue(body['morning']['enabled'])
        self.assertEqual(body['morning']['start'], '06:00')
        self.assertEqual(body['morning']['end'], '09:00')
        # Lunch/dinner remain off by default in this payload.
        self.assertFalse(body['lunch']['enabled'])
        self.assertFalse(body['dinner']['enabled'])

        s = MemberInputSettings.load(get_test_mess())
        self.assertTrue(s.morning_enabled)
        self.assertEqual(s.morning_start, datetime.time(6, 0))
        self.assertEqual(s.morning_end, datetime.time(9, 0))
        self.assertEqual(s.updated_by, self.manager)

    def test_slots_are_set_independently_in_one_request(self):
        self.client.login(username='misavemgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(self._payload(
            morning={'enabled': True, 'start': '06:00', 'end': '09:00'},
            lunch={'enabled': False, 'start': '11:00', 'end': '14:00'},
            dinner={'enabled': True, 'start': '20:00', 'end': '22:00'},
        )), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        s = MemberInputSettings.load(get_test_mess())
        self.assertTrue(s.morning_enabled)
        self.assertFalse(s.lunch_enabled)
        self.assertTrue(s.dinner_enabled)
        self.assertEqual(s.dinner_start, datetime.time(20, 0))

    def test_permitted_sub_manager_can_toggle(self):
        self.client.login(username='misavesubok', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            self._payload(lunch={'enabled': True, 'start': '11:00', 'end': '14:00'})
        ), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(MemberInputSettings.load(get_test_mess()).lunch_enabled)

    def test_unpermitted_sub_manager_denied(self):
        self.client.login(username='misavesubno', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            self._payload(morning={'enabled': True, 'start': '06:00', 'end': '09:00'})
        ), content_type='application/json')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(MemberInputSettings.load(get_test_mess()).morning_enabled)

    def test_plain_member_denied(self):
        self.client.login(username='misaveplain', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            self._payload(morning={'enabled': True, 'start': '06:00', 'end': '09:00'})
        ), content_type='application/json')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(MemberInputSettings.load(get_test_mess()).morning_enabled)

    def test_can_turn_off(self):
        MemberInputSettings.objects.create(mess=get_test_mess(), morning_enabled=True,
                                            morning_start=datetime.time(6, 0), morning_end=datetime.time(9, 0))
        self.client.login(username='misavemgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            self._payload(morning={'enabled': False, 'start': '06:00', 'end': '09:00'})
        ), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(MemberInputSettings.load(get_test_mess()).morning_enabled)

    def test_invalid_time_rejected(self):
        self.client.login(username='misavemgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            self._payload(morning={'enabled': True, 'start': 'not-a-time', 'end': '09:00'})
        ), content_type='application/json')
        self.assertEqual(resp.status_code, 400)


class MemberSelfInputDraftTests(TestCase):
    """Requirement coverage: a plain Member can write into the SAME draft
    table used by Manager/Sub-Manager, but only their own row, PER MEAL
    TYPE — each of morning/lunch/dinner has its own independent time
    window, and is separately locked once the Manager/Sub-Manager
    commits that specific slot for that specific day."""

    def setUp(self):
        self.manager = make_member('selfinmgr', role=Member.ROLE_MANAGER, phone='01788880030')
        self.member = make_member('selfinmem', phone='01788880031')
        self.other_member = make_member('selfinother', phone='01788880032')

    def url(self):
        return reverse('meal_save_member_draft')

    def _open_window_now(self, slots=('morning', 'lunch', 'dinner')):
        now = datetime.datetime.now().time()
        start = (datetime.datetime.combine(datetime.date.today(), now)
                 - datetime.timedelta(minutes=5)).time()
        end = (datetime.datetime.combine(datetime.date.today(), now)
               + datetime.timedelta(minutes=5)).time()
        kwargs = {}
        for slot in slots:
            kwargs[f'{slot}_enabled'] = True
            kwargs[f'{slot}_start'] = start
            kwargs[f'{slot}_end'] = end
        MemberInputSettings.objects.update_or_create(mess=get_test_mess(), defaults=kwargs)

    def test_member_can_save_own_draft_when_all_windows_open(self):
        self._open_window_now()
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 1, 'lunch': 1, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body['slots_saved']), {'morning', 'lunch', 'dinner'})
        draft = MealMarkDraft.objects.get(member=self.member)
        self.assertEqual(draft.morning, Decimal('1.0'))
        self.assertEqual(draft.lunch, Decimal('1.0'))
        self.assertEqual(draft.updated_by, self.member)

    def test_member_cannot_save_when_all_windows_closed(self):
        MemberInputSettings.objects.create(mess=get_test_mess(), morning_enabled=False, lunch_enabled=False, dinner_enabled=False)
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 1, 'lunch': 1, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(MealMarkDraft.objects.filter(member=self.member).exists())

    def test_member_cannot_save_outside_configured_window(self):
        now = datetime.datetime.now().time()
        far_start = (datetime.datetime.combine(datetime.date.today(), now) + datetime.timedelta(hours=2)).time()
        far_end = (datetime.datetime.combine(datetime.date.today(), now) + datetime.timedelta(hours=3)).time()
        MemberInputSettings.objects.create(mess=get_test_mess(), 
            morning_enabled=True, morning_start=far_start, morning_end=far_end,
            lunch_enabled=True, lunch_start=far_start, lunch_end=far_end,
            dinner_enabled=True, dinner_start=far_start, dinner_end=far_end,
        )
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 1, 'lunch': 1, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 403)

    def test_member_cannot_save_draft_for_another_member_even_when_window_open(self):
        self._open_window_now()
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.other_member.id, 'morning': 1, 'lunch': 1, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(MealMarkDraft.objects.filter(member=self.other_member).exists())

    def test_manager_can_still_save_any_member_draft_regardless_of_window(self):
        MemberInputSettings.objects.create(mess=get_test_mess(), morning_enabled=False, lunch_enabled=False, dinner_enabled=False)
        self.client.login(username='selfinmgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 2, 'lunch': 2, 'dinner': 2}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        draft = MealMarkDraft.objects.get(member=self.member)
        self.assertEqual(draft.morning, Decimal('2.0'))

    def test_member_draft_save_shares_same_table_manager_writes_to(self):
        self.client.login(username='selfinmgr', password='pass1234')
        self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 1, 'lunch': 1, 'dinner': 1}),
            content_type='application/json')
        self.assertEqual(MealMarkDraft.objects.count(), 1)

        self._open_window_now()
        self.client.logout()
        self.client.login(username='selfinmem', password='pass1234')
        self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 0, 'lunch': 1, 'dinner': 0}),
            content_type='application/json')

        self.assertEqual(MealMarkDraft.objects.filter(member=self.member).count(), 1)
        draft = MealMarkDraft.objects.get(member=self.member)
        self.assertEqual(draft.morning, Decimal('0.0'))
        self.assertEqual(draft.lunch, Decimal('1.0'))
        self.assertEqual(draft.updated_by, self.member)

    def test_member_cannot_commit_save_day_even_when_window_open(self):
        # Requirement: only Manager/Sub-Manager can commit to MealMark.
        self._open_window_now()
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(reverse('meal_save_day'), data=json.dumps({
            'day': 10, 'month': 6, 'year': 2027,
            'marks': {str(self.member.id): {'morning': 1, 'lunch': 1, 'dinner': 0}},
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(MealMark.objects.filter(member=self.member).exists())

    def test_morning_locked_by_manager_blocks_only_morning_not_lunch_or_dinner(self):
        """Core requirement: if the Manager has already saved Morning for
        a day, the member can still update Lunch and Dinner for that same
        day (if those windows are open) — only Morning is off-limits."""
        self._open_window_now()
        target_date = datetime.date(2027, 6, 10)
        DayMealLock.objects.create(mess=get_test_mess(), date=target_date, morning_locked=True)

        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps({
            'member_id': self.member.id, 'morning': 9, 'lunch': 2, 'dinner': 1,
            'selected_day': 10, 'selected_month': 6, 'selected_year': 2027,
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body['slots_saved']), {'lunch', 'dinner'})
        self.assertNotIn('morning', body['slots_saved'])

        draft = MealMarkDraft.objects.get(member=self.member)
        # Morning request value (9) must be ignored/preserved as whatever
        # was there before (0, since this is a fresh draft) — NOT 9.
        self.assertEqual(draft.morning, Decimal('0.0'))
        self.assertEqual(draft.lunch, Decimal('2.0'))
        self.assertEqual(draft.dinner, Decimal('1.0'))

    def test_lunch_locked_by_manager_blocks_only_lunch(self):
        self._open_window_now()
        target_date = datetime.date(2027, 6, 11)
        DayMealLock.objects.create(mess=get_test_mess(), date=target_date, lunch_locked=True)
        # Pre-existing draft with some lunch value that must be preserved.
        MealMarkDraft.objects.create(member=self.member, morning=Decimal('0'),
                                      lunch=Decimal('5'), dinner=Decimal('0'))

        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps({
            'member_id': self.member.id, 'morning': 1, 'lunch': 99, 'dinner': 1,
            'selected_day': 11, 'selected_month': 6, 'selected_year': 2027,
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(set(body['slots_saved']), {'morning', 'dinner'})

        draft = MealMarkDraft.objects.get(member=self.member)
        self.assertEqual(draft.morning, Decimal('1.0'))
        self.assertEqual(draft.lunch, Decimal('5.0'))  # untouched, preserved
        self.assertEqual(draft.dinner, Decimal('1.0'))

    def test_all_three_locked_for_the_day_rejects_outright(self):
        self._open_window_now()
        target_date = datetime.date(2027, 6, 13)
        DayMealLock.objects.create(mess=get_test_mess(), date=target_date, morning_locked=True, lunch_locked=True, dinner_locked=True)
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps({
            'member_id': self.member.id, 'morning': 1, 'lunch': 1, 'dinner': 1,
            'selected_day': 13, 'selected_month': 6, 'selected_year': 2027,
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(MealMarkDraft.objects.filter(member=self.member).exists())

    def test_slot_with_closed_window_is_excluded_even_if_day_unlocked(self):
        # Only enable morning's window; lunch/dinner stay disabled.
        self._open_window_now(slots=('morning',))
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 1, 'lunch': 1, 'dinner': 1}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['slots_saved'], ['morning'])
        draft = MealMarkDraft.objects.get(member=self.member)
        self.assertEqual(draft.morning, Decimal('1.0'))
        self.assertEqual(draft.lunch, Decimal('0.0'))
        self.assertEqual(draft.dinner, Decimal('0.0'))

    def test_manager_can_still_save_draft_for_a_fully_locked_day_unaffected(self):
        target_date = datetime.date(2027, 6, 12)
        DayMealLock.objects.create(mess=get_test_mess(), date=target_date, morning_locked=True, lunch_locked=True, dinner_locked=True)
        self.client.login(username='selfinmgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps({
            'member_id': self.member.id, 'morning': 3, 'lunch': 3, 'dinner': 3,
            'selected_day': 12, 'selected_month': 6, 'selected_year': 2027,
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        draft = MealMarkDraft.objects.get(member=self.member)
        self.assertEqual(draft.morning, Decimal('3.0'))

    def test_member_save_without_selected_day_still_applies_open_window_slots(self):
        # If the client doesn't send a selected day (e.g. an older client),
        # there's no day to check locks against, so day_lock is just None —
        # the open-window slots still get saved.
        self._open_window_now()
        self.client.login(username='selfinmem', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps(
            {'member_id': self.member.id, 'morning': 1, 'lunch': 0, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)


class MealMarkPageSelfInputContextTests(TestCase):
    """meal_mark view's per-role context flags (can_edit_all / can_edit_self /
    can_control_input) drive what the template renders — verify they're
    computed correctly for each role and window state."""

    def setUp(self):
        self.manager = make_member('ctxmgr', role=Member.ROLE_MANAGER, phone='01788880040')
        self.sub_with_meal = make_member('ctxsubmeal', role=Member.ROLE_SUB_MANAGER, phone='01788880041')
        self.sub_with_control = make_member('ctxsubctrl', role=Member.ROLE_SUB_MANAGER, phone='01788880042')
        self.plain = make_member('ctxplain', phone='01788880043')
        SubManagerPermission.objects.create(member=self.sub_with_meal, codename='meal_mark', granted=True)
        SubManagerPermission.objects.create(member=self.sub_with_control, codename='member_input_control', granted=True)

    def _open_window_now(self):
        now = datetime.datetime.now().time()
        start = (datetime.datetime.combine(datetime.date.today(), now)
                 - datetime.timedelta(minutes=5)).time()
        end = (datetime.datetime.combine(datetime.date.today(), now)
               + datetime.timedelta(minutes=5)).time()
        MemberInputSettings.objects.update_or_create(mess=get_test_mess(), defaults={
            'morning_enabled': True, 'morning_start': start, 'morning_end': end,
            'lunch_enabled': True, 'lunch_start': start, 'lunch_end': end,
            'dinner_enabled': True, 'dinner_start': start, 'dinner_end': end,
        })

    def test_manager_always_can_edit_all_regardless_of_window(self):
        self.client.login(username='ctxmgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'const CAN_EDIT_ALL  = true')
        self.assertContains(resp, 'const CAN_EDIT_SELF = false')

    def test_plain_member_cannot_edit_all_even_with_window_open(self):
        self._open_window_now()
        self.client.login(username='ctxplain', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        self.assertContains(resp, 'const CAN_EDIT_ALL  = false')
        self.assertContains(resp, 'const CAN_EDIT_SELF = true')

    def test_plain_member_cannot_edit_self_when_all_windows_closed(self):
        MemberInputSettings.objects.create(mess=get_test_mess(), morning_enabled=False, lunch_enabled=False, dinner_enabled=False)
        self.client.login(username='ctxplain', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        self.assertContains(resp, 'const CAN_EDIT_ALL  = false')
        self.assertContains(resp, 'const CAN_EDIT_SELF = false')

    def test_plain_member_can_edit_self_with_only_one_slot_open(self):
        # CAN_EDIT_SELF should be true if even ONE slot's window is open.
        now = datetime.datetime.now().time()
        start = (datetime.datetime.combine(datetime.date.today(), now) - datetime.timedelta(minutes=5)).time()
        end = (datetime.datetime.combine(datetime.date.today(), now) + datetime.timedelta(minutes=5)).time()
        MemberInputSettings.objects.create(mess=get_test_mess(), 
            morning_enabled=True, morning_start=start, morning_end=end,
            lunch_enabled=False, dinner_enabled=False,
        )
        self.client.login(username='ctxplain', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        self.assertContains(resp, 'const CAN_EDIT_SELF = true')

    def test_sub_manager_with_meal_mark_perm_can_edit_all(self):
        self._open_window_now()
        self.client.login(username='ctxsubmeal', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        self.assertContains(resp, 'const CAN_EDIT_ALL  = true')

    def test_meal_mark_page_no_longer_has_control_toggle_ui(self):
        # The Member Self-Input control panel (time-window editor) moved
        # to its own page; Meal Mark should never render it anymore,
        # regardless of who's viewing.
        self.client.login(username='ctxsubctrl', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        self.assertNotContains(resp, 'id="mi-control-rows"')

    def test_sub_manager_with_control_perm_sees_toggle_ui_on_its_own_page(self):
        self.client.login(username='ctxsubctrl', password='pass1234')
        resp = self.client.get(reverse('member_input_settings_page'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Member Self-Input')
        self.assertContains(resp, 'id="mi-control-rows"')

    def test_sub_manager_without_control_perm_is_redirected_from_settings_page(self):
        self.client.login(username='ctxsubmeal', password='pass1234')
        resp = self.client.get(reverse('member_input_settings_page'))
        self.assertRedirects(resp, reverse('dashboard'))

    def test_own_member_id_present_for_plain_member(self):
        self.client.login(username='ctxplain', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        self.assertContains(resp, f'const OWN_MEMBER_ID = "{self.plain.id}"')

    def test_input_settings_js_present_with_per_slot_data(self):
        self._open_window_now()
        self.client.login(username='ctxplain', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertIn('const INPUT_SETTINGS', content)
        self.assertIn('"morning"', content)
        self.assertIn('"lunch"', content)
        self.assertIn('"dinner"', content)


class MealMarkVisibleMembersScopingTests(TestCase):
    """Requirement: a logged-in plain Member sees ONLY their own card on
    the Meal Mark page, never the full roster. Manager/Sub-Manager (with
    meal_mark) continue to see everyone, unchanged. House-wide day totals
    (aggregated effective meal counts) remain informational context and
    are unaffected by this scoping."""

    def setUp(self):
        self.manager = make_member('scopemgr', role=Member.ROLE_MANAGER, phone='01788880050')
        self.member_a = make_member('scopemema', phone='01788880051')
        self.member_b = make_member('scopememb', phone='01788880052')
        self.member_c = make_member('scopememc', phone='01788880053')

    def _members_js(self, content):
        m = re.search(r'const MEMBERS\s*=\s*(\[.*?\]);', content)
        return json.loads(m.group(1)) if m else None

    def _grid_js(self, content):
        m = re.search(r'const GRID\s*=\s*(\{.*?\});', content)
        return json.loads(m.group(1)) if m else None

    def _draft_js(self, content):
        m = re.search(r'const DRAFT\s*=\s*(\{.*?\});', content)
        return json.loads(m.group(1)) if m else None

    def test_plain_member_sees_only_own_card(self):
        self.client.login(username='scopemema', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        members_js = self._members_js(resp.content.decode())
        self.assertEqual(len(members_js), 1)
        self.assertEqual(members_js[0]['id'], str(self.member_a.id))

    def test_manager_still_sees_full_roster(self):
        self.client.login(username='scopemgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        members_js = self._members_js(resp.content.decode())
        ids = {m['id'] for m in members_js}
        self.assertIn(str(self.member_a.id), ids)
        self.assertIn(str(self.member_b.id), ids)
        self.assertIn(str(self.member_c.id), ids)
        self.assertIn(str(self.manager.id), ids)

    def test_plain_member_grid_excludes_other_members_marks(self):
        MealMark.objects.create(member=self.member_b, date=datetime.date(2027, 5, 5),
                                 morning=Decimal('1'), lunch=Decimal('1'), dinner=Decimal('1'),
                                 marked_by=self.manager)
        self.client.login(username='scopemema', password='pass1234')
        resp = self.client.get(reverse('meal_mark'), {'month': 5, 'year': 2027})
        grid = self._grid_js(resp.content.decode())
        self.assertEqual(list(grid.keys()), [str(self.member_a.id)])
        self.assertNotIn(str(self.member_b.id), grid)

    def test_plain_member_draft_excludes_other_members_drafts(self):
        MealMarkDraft.objects.create(member=self.member_b, morning=Decimal('2'), lunch=Decimal('2'), dinner=Decimal('2'))
        MealMarkDraft.objects.create(member=self.member_a, morning=Decimal('1'), lunch=Decimal('0'), dinner=Decimal('0'))
        self.client.login(username='scopemema', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        draft = self._draft_js(resp.content.decode())
        self.assertEqual(list(draft.keys()), [str(self.member_a.id)])

    def test_house_wide_day_totals_unaffected_by_scoping(self):
        # Day totals are an aggregate over everyone, even for a plain member's view —
        # only the per-card roster is restricted, not the informational totals.
        MealMark.objects.create(member=self.member_a, date=datetime.date(2027, 5, 6),
                                 morning=Decimal('1'), lunch=Decimal('0'), dinner=Decimal('0'),
                                 marked_by=self.manager)
        MealMark.objects.create(member=self.member_b, date=datetime.date(2027, 5, 6),
                                 morning=Decimal('1'), lunch=Decimal('0'), dinner=Decimal('0'),
                                 marked_by=self.manager)

        self.client.login(username='scopemema', password='pass1234')
        resp_member = self.client.get(reverse('meal_mark'), {'month': 5, 'year': 2027})
        self.client.logout()
        self.client.login(username='scopemgr', password='pass1234')
        resp_mgr = self.client.get(reverse('meal_mark'), {'month': 5, 'year': 2027})

        day_totals_member = json.loads(re.search(r'const DAY_TOTALS\s*=\s*(\{.*?\});',
                                                   resp_member.content.decode()).group(1))
        day_totals_mgr = json.loads(re.search(r'const DAY_TOTALS\s*=\s*(\{.*?\});',
                                               resp_mgr.content.decode()).group(1))
        # Same house-wide total (2 morning meals that day) regardless of viewer.
        self.assertEqual(day_totals_member['6']['morning'], day_totals_mgr['6']['morning'])
        self.assertEqual(day_totals_member['6']['morning'], 2.0)

    def test_plain_member_can_still_save_and_see_own_draft(self):
        self.client.login(username='scopemema', password='pass1234')
        now = datetime.datetime.now().time()
        start = (datetime.datetime.combine(datetime.date.today(), now) - datetime.timedelta(minutes=5)).time()
        end = (datetime.datetime.combine(datetime.date.today(), now) + datetime.timedelta(minutes=5)).time()
        MemberInputSettings.objects.create(mess=get_test_mess(), 
            morning_enabled=True, morning_start=start, morning_end=end,
            lunch_enabled=True, lunch_start=start, lunch_end=end,
            dinner_enabled=True, dinner_start=start, dinner_end=end,
        )

        resp = self.client.post(reverse('meal_save_member_draft'), data=json.dumps(
            {'member_id': self.member_a.id, 'morning': 1, 'lunch': 1, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        resp2 = self.client.get(reverse('meal_mark'))
        draft = self._draft_js(resp2.content.decode())
        self.assertEqual(draft[str(self.member_a.id)], {'morning': 1.0, 'lunch': 1.0, 'dinner': 0.0})


class MealMarkClientLockLogicRegressionTests(TestCase):
    """Regression guard for a real bug: the client-side 'day already
    confirmed by Manager' lock must be driven by the untouched,
    server-rendered GRID (actual MealMark commits) — never by localGrid,
    which also accumulates the member's own unsaved local edits. Using
    localGrid there made a normal +/- click on an UNcommitted day (one
    where only a draft existed) look like it was already locked, even
    though no MealMark commit existed at all.

    This test doesn't execute JS — it asserts the shipped template wires
    the lock checks to GRID so the bug can't silently come back."""

    def setUp(self):
        make_member('lockregmgr', role=Member.ROLE_MANAGER, phone='01788880060')
        self.member = make_member('lockregmem', phone='01788880061')

    def test_card_lock_flag_reads_from_server_day_totals_not_localgrid(self):
        # The per-slot editability check must be driven by the untouched,
        # server-rendered DAY_TOTALS lock flags (backed by DayMealLock) —
        # never by localGrid, which also accumulates the member's own
        # unsaved local edits.
        self.client.login(username='lockregmem', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertIn(
            "const lockedToday = Boolean(dayInfo[`${slot}_locked`]);",
            content,
        )
        # slotCanEdit is the function that gates per-slot editability; it
        # must not reference localGrid at all.
        m = re.search(r'function slotCanEdit\(slot\)\s*\{.*?\n\s*\}', content, re.S)
        self.assertIsNotNone(m, "slotCanEdit function not found in rendered page")
        self.assertNotIn('localGrid', m.group(0))

    def test_save_member_draft_no_longer_has_whole_day_preflight_block(self):
        # The per-meal-type redesign moved this check to the server,
        # which now enforces it PER SLOT (not per whole day) — a
        # client-side whole-day pre-check would incorrectly block saving
        # Lunch/Dinner just because Morning was already committed.
        self.client.login(username='lockregmem', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertNotIn(
            "if (!CAN_EDIT_ALL && GRID[mid] && GRID[mid][String(selDay)]) {",
            content,
        )

    def test_manager_saved_draft_only_does_not_create_a_mealmark(self):
        # Sanity: confirms the scenario reported — a Manager saving a
        # member's DRAFT never creates a MealMark row, so the server-side
        # commit check (which is correct and unaffected by this bug)
        # would never have rejected the member's later self-save either.
        mgr = Member.objects.get(user__username='lockregmgr')
        self.client.login(username='lockregmgr', password='pass1234')
        resp = self.client.post(reverse('meal_save_member_draft'), data=json.dumps(
            {'member_id': self.member.id, 'morning': 1, 'lunch': 1, 'dinner': 0}),
            content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(MealMark.objects.filter(member=self.member).exists())
        self.assertTrue(MealMarkDraft.objects.filter(member=self.member).exists())


class DayMealLockModelTests(TestCase):
    """Model-level behaviour of the per-date, per-slot commit lock."""

    def test_default_state_is_unlocked(self):
        lock = DayMealLock.objects.create(mess=get_test_mess(), date=datetime.date(2027, 7, 1))
        self.assertFalse(lock.morning_locked)
        self.assertFalse(lock.lunch_locked)
        self.assertFalse(lock.dinner_locked)
        self.assertFalse(lock.is_fully_locked())
        self.assertEqual(lock.locked_slots(), [])

    def test_is_fully_locked_requires_all_three(self):
        lock = DayMealLock.objects.create(mess=get_test_mess(), date=datetime.date(2027, 7, 2),
                                           morning_locked=True, lunch_locked=True)
        self.assertFalse(lock.is_fully_locked())
        lock.dinner_locked = True
        lock.save()
        self.assertTrue(lock.is_fully_locked())
        self.assertEqual(set(lock.locked_slots()), {'morning', 'lunch', 'dinner'})

    def test_one_lock_row_per_date(self):
        DayMealLock.objects.create(mess=get_test_mess(), date=datetime.date(2027, 7, 3))
        with self.assertRaises(Exception):
            DayMealLock.objects.create(mess=get_test_mess(), date=datetime.date(2027, 7, 3))


class MealSaveDaySlotCommitTests(TestCase):
    """The four-button commit workflow on /meals/save-day/:
    Save Morning / Save Lunch / Save Dinner / Save All Meal — each meal
    slot for a given day can only be committed once."""

    def setUp(self):
        self.manager = make_member('slotmgr', role=Member.ROLE_MANAGER, phone='01788880070')
        self.sub_with_meal = make_member('slotsub', role=Member.ROLE_SUB_MANAGER, phone='01788880071')
        SubManagerPermission.objects.create(member=self.sub_with_meal, codename='meal_mark', granted=True)
        self.member_a = make_member('slotmema', phone='01788880072')
        self.member_b = make_member('slotmemb', phone='01788880073')
        self.date = datetime.date(2027, 7, 10)

    def url(self):
        return reverse('meal_save_day')

    def _post(self, slots, marks=None, day=10, month=7, year=2027):
        marks = marks if marks is not None else {
            str(self.member_a.id): {'morning': 1, 'lunch': 1, 'dinner': 1},
            str(self.member_b.id): {'morning': 1, 'lunch': 1, 'dinner': 1},
        }
        return self.client.post(self.url(), data=json.dumps({
            'day': day, 'month': month, 'year': year, 'marks': marks, 'slots': slots,
        }), content_type='application/json')

    def test_save_morning_only_commits_morning_field(self):
        self.client.login(username='slotmgr', password='pass1234')
        resp = self._post(['morning'])
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['message'], 'Morning Saved')
        self.assertEqual(body['slots_saved'], ['morning'])

        mk = MealMark.objects.get(member=self.member_a, date=self.date)
        self.assertEqual(mk.morning, Decimal('1'))
        # Lunch/dinner were never part of this request — they should be 0,
        # not garbage, since this is a fresh row.
        self.assertEqual(mk.lunch, Decimal('0'))
        self.assertEqual(mk.dinner, Decimal('0'))

        lock = DayMealLock.objects.get(date=self.date)
        self.assertTrue(lock.morning_locked)
        self.assertFalse(lock.lunch_locked)
        self.assertFalse(lock.dinner_locked)

    def test_save_lunch_after_morning_preserves_morning_value(self):
        self.client.login(username='slotmgr', password='pass1234')
        self._post(['morning'], marks={str(self.member_a.id): {'morning': 2, 'lunch': 0, 'dinner': 0}})
        resp = self._post(['lunch'], marks={str(self.member_a.id): {'morning': 0, 'lunch': 3, 'dinner': 0}})
        self.assertEqual(resp.status_code, 200)

        mk = MealMark.objects.get(member=self.member_a, date=self.date)
        # Morning must still be 2 (committed earlier), even though this
        # request's payload sent morning=0 — only 'lunch' was in slots.
        self.assertEqual(mk.morning, Decimal('2'))
        self.assertEqual(mk.lunch, Decimal('3'))

    def test_cannot_save_morning_twice(self):
        self.client.login(username='slotmgr', password='pass1234')
        self._post(['morning'])
        resp = self._post(['morning'])
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertIn('Morning', body['error'])
        self.assertEqual(body['already_locked'], ['morning'])

    def test_save_all_meal_locks_all_three_slots(self):
        self.client.login(username='slotmgr', password='pass1234')
        resp = self._post(['morning', 'lunch', 'dinner'])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['message'], 'All Meal Saved')
        lock = DayMealLock.objects.get(date=self.date)
        self.assertTrue(lock.is_fully_locked())

    def test_after_save_all_meal_individual_slot_saves_are_rejected(self):
        self.client.login(username='slotmgr', password='pass1234')
        self._post(['morning', 'lunch', 'dinner'])
        for slot in ['morning', 'lunch', 'dinner']:
            resp = self._post([slot])
            self.assertEqual(resp.status_code, 409, f'{slot} should be rejected after Save All Meal')

    def test_save_all_meal_rejected_if_any_slot_already_locked(self):
        self.client.login(username='slotmgr', password='pass1234')
        self._post(['morning'])
        resp = self._post(['morning', 'lunch', 'dinner'])
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()['already_locked'], ['morning'])
        # Lunch/dinner must NOT have been silently saved either — the
        # whole request is rejected, no partial commit.
        lock = DayMealLock.objects.get(date=self.date)
        self.assertFalse(lock.lunch_locked)
        self.assertFalse(lock.dinner_locked)
        self.assertFalse(MealMark.objects.filter(member=self.member_a, date=self.date,
                                                   lunch__gt=0).exists())

    def test_locks_are_per_date_not_global(self):
        self.client.login(username='slotmgr', password='pass1234')
        self._post(['morning'], day=10)
        # A different day's morning must still be fully open.
        resp = self._post(['morning'], day=11)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(DayMealLock.objects.get(date=datetime.date(2027, 7, 11)).morning_locked is False and False)
        lock_11 = DayMealLock.objects.get(date=datetime.date(2027, 7, 11))
        self.assertTrue(lock_11.morning_locked)

    def test_omitting_slots_defaults_to_all_three_legacy_behavior(self):
        # Backward compatibility: older callers that don't send `slots`
        # at all should still commit all three slots in one go.
        self.client.login(username='slotmgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps({
            'day': 15, 'month': 7, 'year': 2027,
            'marks': {str(self.member_a.id): {'morning': 1, 'lunch': 1, 'dinner': 1}},
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        lock = DayMealLock.objects.get(date=datetime.date(2027, 7, 15))
        self.assertTrue(lock.is_fully_locked())

    def test_permitted_sub_manager_can_commit_slots(self):
        self.client.login(username='slotsub', password='pass1234')
        resp = self._post(['dinner'])
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(DayMealLock.objects.get(date=self.date).dinner_locked)

    def test_zero_total_after_partial_slot_save_does_not_delete_other_committed_slots(self):
        self.client.login(username='slotmgr', password='pass1234')
        # Commit morning=1 first.
        self._post(['morning'], marks={str(self.member_a.id): {'morning': 1, 'lunch': 0, 'dinner': 0}})
        # Now commit lunch=0 (e.g. member genuinely skips lunch that day).
        resp = self._post(['lunch'], marks={str(self.member_a.id): {'morning': 0, 'lunch': 0, 'dinner': 0}})
        self.assertEqual(resp.status_code, 200)
        # The row must still exist with morning preserved — total is 1, not 0.
        mk = MealMark.objects.get(member=self.member_a, date=self.date)
        self.assertEqual(mk.morning, Decimal('1'))
        self.assertEqual(mk.lunch, Decimal('0'))

    def test_invalid_slot_names_are_ignored_not_crashing(self):
        self.client.login(username='slotmgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps({
            'day': 20, 'month': 7, 'year': 2027,
            'marks': {str(self.member_a.id): {'morning': 1, 'lunch': 0, 'dinner': 0}},
            'slots': ['morning', 'brunch', 'elevenses'],
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['slots_saved'], ['morning'])

    def test_empty_slots_list_rejected(self):
        self.client.login(username='slotmgr', password='pass1234')
        resp = self.client.post(self.url(), data=json.dumps({
            'day': 21, 'month': 7, 'year': 2027, 'marks': {}, 'slots': [],
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_plain_member_still_cannot_commit_any_slot(self):
        plain = make_member('slotplain', phone='01788880074')
        self.client.login(username='slotplain', password='pass1234')
        resp = self._post(['morning'])
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(DayMealLock.objects.filter(date=self.date).exists())


class MealMarkSlotButtonsTemplateTests(TestCase):
    """The Meal Mark page renders 4 distinct save buttons (Morning / Lunch
    / Dinner / All Meal) for Manager/Sub-Manager, and their disabled state
    reflects DayMealLock for the currently selected day."""

    def setUp(self):
        self.manager = make_member('btnmgr', role=Member.ROLE_MANAGER, phone='01788880080')
        self.member = make_member('btnmem', phone='01788880081')

    def test_four_buttons_present_for_manager(self):
        self.client.login(username='btnmgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertIn('id="save-morning-btn"', content)
        self.assertIn('id="save-lunch-btn"', content)
        self.assertIn('id="save-dinner-btn"', content)
        self.assertIn('id="save-all-btn"', content)
        # The old single button must be gone.
        self.assertNotIn('id="save-btn"', content)

    def test_buttons_not_present_for_plain_member(self):
        self.client.login(username='btnmem', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertNotIn('id="save-morning-btn"', content)
        self.assertNotIn('id="save-all-btn"', content)

    def test_day_totals_js_includes_lock_flags(self):
        DayMealLock.objects.create(mess=get_test_mess(), date=datetime.date.today().replace(day=1),
                                    morning_locked=True)
        self.client.login(username='btnmgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'), {
            'month': datetime.date.today().month, 'year': datetime.date.today().year,
        })
        content = resp.content.decode()
        self.assertIn('"morning_locked": true', content)

    def test_per_member_draft_save_button_not_hidden_for_managers(self):
        """Regression guard: the per-member 'Save Draft' button (which
        stages a member's values into MealMarkDraft without committing
        them — separate from Save Morning/Lunch/Dinner/All Meal) must be
        available to Manager/Sub-Manager too, not just self-input members.
        A previous version of renderMealCards() conditionally hid this
        button whenever CAN_EDIT_ALL was true, silently dropping the
        draft workflow for managers even though the backend
        (meal_save_member_draft) always supported it for them."""
        self.client.login(username='btnmgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertIn('memberSaveBtn_${m.id}', content)
        self.assertIn('saveMemberDraft', content)
        # The old buggy pattern that hid the button behind CAN_EDIT_ALL
        # must not be present in this block.
        self.assertNotIn("${CAN_EDIT_ALL ? '' : `\n        <button id=\"memberSaveBtn_", content)


class SaveDayClientStatePreservationRegressionTests(TestCase):
    """Regression guard for a real, browser-confirmed bug: clicking Save
    Morning/Lunch/Dinner re-renders every visible member's card, and the
    post-save sync step that refreshes GRID/localGrid was seeding each
    member's row from GRID (committed-only) instead of localGrid
    (committed + their own pending edits). For any member who wasn't
    part of the slot just saved, that wiped out whatever they had typed
    but not yet saved in a DIFFERENT slot — e.g. clicking Save Morning
    for member A would silently erase member B's still-unsaved Lunch
    value the moment the cards re-rendered.

    This doesn't execute JS — it asserts the shipped template seeds that
    merge from localGrid so the bug can't silently come back."""

    def setUp(self):
        make_member('presmgr', role=Member.ROLE_MANAGER, phone='01788880090')

    def test_grid_merge_seeds_from_localgrid_not_grid(self):
        self.client.login(username='presmgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertIn(
            "const pending = (localGrid[m.id] && localGrid[m.id][String(selDay)]) || {};",
            content,
        )
        self.assertIn(
            "{ morning: 0, lunch: 0, dinner: 0, count: 0, eff: 0, note: '' },\n        pending",
            content,
        )
        # The old, buggy seed source must be gone from this merge block.
        self.assertNotIn(
            "const existing = GRID[m.id][String(selDay)] || { morning: 0, lunch: 0, dinner: 0, count: 0, eff: 0, note: '' };",
            content,
        )

class MonthlyMealRecordPageTests(TestCase):
    """The 'Monthly Meal Record' page (formerly the Monthly Matrix tab
    inside Meal Mark) is now its own dedicated page at
    /meals/monthly-record/. It must keep the exact same behavior the
    tab had: read-only, full-month M/L/D grid, sourced from committed
    MealMark data only, with the same visibility/scoping rules as
    Meal Mark itself (full roster for Manager/Sub-Manager with
    meal_mark perm, own-row-only for a plain Member) and NOT gated
    behind any special permission (any logged-in user can view it,
    matching the old tab's visibility)."""

    def setUp(self):
        self.manager  = make_member('mmrmgr', role=Member.ROLE_MANAGER, phone='01788880100')
        self.member_a = make_member('mmrmema', phone='01788880101')
        self.member_b = make_member('mmrmemb', phone='01788880102')

    def _members_js(self, content):
        m = re.search(r'const MEMBERS\s*=\s*(\[.*?\]);', content)
        return json.loads(m.group(1)) if m else None

    def _grid_js(self, content):
        m = re.search(r'const GRID\s*=\s*(\{.*?\});', content)
        return json.loads(m.group(1)) if m else None

    def test_page_loads_for_plain_member(self):
        # No special permission required — same as the old tab.
        self.client.login(username='mmrmema', password='pass1234')
        resp = self.client.get(reverse('monthly_meal_record'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Monthly Meal Record')

    def test_page_loads_for_manager(self):
        self.client.login(username='mmrmgr', password='pass1234')
        resp = self.client.get(reverse('monthly_meal_record'))
        self.assertEqual(resp.status_code, 200)

    def test_meal_mark_page_no_longer_contains_monthly_record_grid(self):
        self.client.login(username='mmrmgr', password='pass1234')
        resp = self.client.get(reverse('meal_mark'))
        content = resp.content.decode()
        self.assertNotIn('id="matrix-table"', content)
        self.assertNotIn('function buildMatrix', content)

    def test_plain_member_sees_only_own_row(self):
        self.client.login(username='mmrmema', password='pass1234')
        resp = self.client.get(reverse('monthly_meal_record'))
        members_js = self._members_js(resp.content.decode())
        self.assertEqual(len(members_js), 1)
        self.assertEqual(members_js[0]['id'], str(self.member_a.id))

    def test_manager_sees_full_roster(self):
        self.client.login(username='mmrmgr', password='pass1234')
        resp = self.client.get(reverse('monthly_meal_record'))
        members_js = self._members_js(resp.content.decode())
        ids = {m['id'] for m in members_js}
        self.assertIn(str(self.member_a.id), ids)
        self.assertIn(str(self.member_b.id), ids)
        self.assertIn(str(self.manager.id), ids)

    def test_grid_reflects_committed_marks_only_not_draft(self):
        MealMark.objects.create(member=self.member_a, date=datetime.date(2027, 6, 10),
                                 morning=Decimal('1'), lunch=Decimal('0'), dinner=Decimal('0'),
                                 marked_by=self.manager)
        MealMarkDraft.objects.create(member=self.member_a, morning=Decimal('9'), lunch=Decimal('9'), dinner=Decimal('9'))

        self.client.login(username='mmrmgr', password='pass1234')
        resp = self.client.get(reverse('monthly_meal_record'), {'month': 6, 'year': 2027})
        content = resp.content.decode()
        # The page must not reference drafts at all — it's a committed-data-only report.
        self.assertNotIn('DRAFT', content)
        grid = self._grid_js(content)
        self.assertEqual(grid[str(self.member_a.id)]['10']['morning'], 1.0)

    def test_nav_link_present_and_points_to_new_page(self):
        self.client.login(username='mmrmema', password='pass1234')
        resp = self.client.get(reverse('dashboard'))
        self.assertContains(resp, '/meals/monthly-record/')

    def test_header_and_name_column_are_sticky(self):
        # Date/M-L-D header rows stay fixed on vertical scroll; the member
        # name column stays fixed on horizontal scroll.
        self.client.login(username='mmrmgr', password='pass1234')
        resp = self.client.get(reverse('monthly_meal_record'))
        content = resp.content.decode()
        # Header cells (day numbers, M/L/D, TOTAL) stick to the top.
        self.assertIn("position:sticky;top:0;z-index:4", content)
        # The "#  Member" corner header cell sticks to both edges.
        self.assertIn("position:sticky;left:0;top:0;z-index:5", content)
        # The member-name column in the data rows sticks to the left.
        self.assertIn("position:sticky;left:0;background:var(--bg2);z-index:2", content)
        # Row2 (M/L/D sub-header) top offset is computed at render time,
        # not hardcoded, so it correctly sits just below row1.
        self.assertIn("mmr-header-row2", content)
        # border-collapse:collapse breaks sticky positioning for table
        # cells (a well-known cross-browser bug) — the Monthly Meal
        # Record matrix table specifically must use border-collapse:
        # separate for its sticky left column to actually stick while
        # scrolling horizontally. (border-collapse:collapse legitimately
        # still appears elsewhere in base.html's global .table-wrap
        # class for unrelated tables, so we check the matrix table's
        # own inline style rather than the whole page.)
        self.assertIn('id="matrix-table" style="border-collapse:separate;border-spacing:0;', content)

    def test_flex_ancestors_allow_inner_scroll_container_to_shrink(self):
        # position:sticky;left:0 on the name column only works if the
        # *inner* overflow-x:auto div actually scrolls. Without
        # min-width:0 on .main/.content (both flex items along the
        # horizontal axis), the wide matrix table's min-content size
        # propagates up through the flex ancestor chain and forces the
        # whole PAGE to grow wide and scroll at the body level instead
        # — leaving the inner div with nothing to scroll, which is why
        # the sticky column silently did nothing. This was confirmed
        # with a real headless-browser render, not just static CSS
        # inspection, before landing the fix.
        self.client.login(username='mmrmgr', password='pass1234')
        resp = self.client.get(reverse('dashboard'))
        content = resp.content.decode()
        self.assertIn('.main{margin-left:260px;width:calc(100% - 260px);display:flex;flex-direction:column;min-height:100vh;min-width:0;', content)
        self.assertIn('.content{padding:24px 28px;flex:1;min-width:0}', content)


class GlobalScrollbarStylingTests(TestCase):
    """The app-wide scrollbar (in base.html, applies to every scrollable
    element including the Monthly Meal Record matrix container) should
    render noticeably thicker than the old 5px hairline, with a Firefox
    fallback and a hover state for discoverability."""

    def setUp(self):
        make_member('scrollcssmgr', role=Member.ROLE_MANAGER, phone='01788880200')

    def test_scrollbar_is_thicker_with_firefox_fallback(self):
        self.client.login(username='scrollcssmgr', password='pass1234')
        resp = self.client.get(reverse('dashboard'))
        content = resp.content.decode()
        self.assertIn('::-webkit-scrollbar{width:12px;height:12px}', content)
        self.assertIn('scrollbar-width:auto;scrollbar-color:var(--bg5) transparent', content)
        self.assertIn('::-webkit-scrollbar-thumb:hover{background:var(--bd3)}', content)
        # The old 5px hairline scrollbar must be gone.
        self.assertNotIn('::-webkit-scrollbar{width:5px;height:5px}', content)
