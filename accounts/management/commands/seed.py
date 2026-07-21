from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from accounts.models import Member, SubManagerPermission, Mess
from finance.models import Deposit, Expense
from meals.models import MealMark, DayMealLock, DayConfig, MonthlySettings
import datetime, random
from decimal import Decimal

COLORS = ['#6366f1','#8b5cf6','#ec4899','#ef4444','#f97316','#22c55e','#14b8a6','#3b82f6']

class Command(BaseCommand):
    help = 'Seed a demo Mess with sample data'

    def add_arguments(self, parser):
        parser.add_argument('--mess-name', type=str, default='Demo Mess',
                             help='Name of the demo mess to create/reuse')

    def handle(self, *args, **kwargs):
        today = datetime.date.today()
        m, y  = today.month, today.year
        mess_name = kwargs['mess_name']

        mess, mess_created = Mess.objects.get_or_create(name=mess_name)
        if mess_created:
            self.stdout.write(f'Created mess: {mess.name} (Mess ID: {mess.code})')
        else:
            self.stdout.write(f'Using existing mess: {mess.name} (Mess ID: {mess.code})')

        MonthlySettings.objects.get_or_create(mess=mess, month=m, year=y, defaults={'cooking_cost': 500})

        members_data = [
            ('01711000001', 'Shuvonkar Das',    'manager',     'A1'),
            ('01711000002', 'Tabibul Islam',    'sub_manager', 'A2'),
            ('01711000003', 'Ibrahim Hossain',  'member',      'B1'),
            ('01711000004', 'Sohel Rana',       'member',      'B2'),
            ('01711000005', 'Robin Ahmed',      'member',      'C1'),
            ('01711000006', 'Sharmin Akter',    'member',      'C2'),
            ('01711000007', 'Riaj Uddin',       'member',      'D1'),
            ('01711000008', 'Ismail Sheikh',    'member',      'D2'),
            ('01711000009', 'Mamun Rashid',     'member',      'E1'),
            ('01711000010', 'Masud Parvez',     'member',      'E2'),
            ('01711000011', 'Bimol Chandra',    'member',      'F1'),
            ('01711000012', 'Shahriar Kabir',   'member',      'F2'),
            ('01711000013', 'Saiful Islam',     'member',      'G1'),
        ]

        created_members = []
        for phone, name, role, room in members_data:
            existing = Member.all_objects.filter(mess=mess, phone=phone).first()
            if not existing:
                username = f'user_{mess.code}_{phone}'
                u = User.objects.create_user(username=username, password='1234')
                mem = Member.objects.create(mess=mess, user=u, phone=phone, name=name, role=role,
                                            room_number=room, avatar_color=random.choice(COLORS))
                if role == 'sub_manager':
                    for code, _ in SubManagerPermission.ALL_PERMS:
                        SubManagerPermission.objects.get_or_create(member=mem, codename=code,
                            defaults={'granted': code in ['meal_mark','bazar_entry','deposit_entry','view_reports','export_data']})
                created_members.append(mem)
                self.stdout.write(f'  Created: {name} ({role}) — phone: {phone} pass: 1234')
            else:
                created_members.append(existing)

        # Meal marks: only seed days 1–15 of the month (never seed future
        # dates either, if today is earlier than the 15th).
        seed_through_day = min(15, today.day)
        all_members = Member.all_objects.filter(mess=mess, is_active=True)
        for day in range(1, seed_through_day + 1):
            date = datetime.date(y, m, day)
            any_marked = False
            for mem in all_members:
                # Randomly mark 0-3 of the day's three meal slots
                morning = random.choice([Decimal('0'), Decimal('0'), Decimal('1')])
                lunch   = random.choice([Decimal('0'), Decimal('1'), Decimal('1')])
                dinner  = random.choice([Decimal('0'), Decimal('1'), Decimal('1')])
                if morning or lunch or dinner:
                    MealMark.all_objects.get_or_create(
                        member=mem, date=date,
                        defaults={'morning': morning, 'lunch': lunch, 'dinner': dinner},
                    )
                    any_marked = True
            # Seeded days are committed data, so lock all three slots —
            # otherwise the Save Morning/Lunch/Dinner/All Meal buttons
            # would let someone silently overwrite this seeded data
            # without the lock protection that real commits get.
            if any_marked:
                DayMealLock.objects.get_or_create(
                    mess=mess, date=date,
                    defaults={'morning_locked': True, 'lunch_locked': True, 'dinner_locked': True}
                )

        # Special day multiplier — dinner-only feast on day 5 (always within
        # the 1–15 seed window, so no date guard needed).
        DayConfig.objects.get_or_create(
            mess=mess, date=datetime.date(y, m, 5),
            defaults={
                'morning_multiplier': Decimal('1.0'),
                'lunch_multiplier':   Decimal('1.0'),
                'dinner_multiplier':  Decimal('2.0'),
                'label': 'Special Feast Day',
            }
        )

        # Expenses — same day-1-to-15 range as meal marks, so reports and
        # cost-per-meal figures line up with the days that actually have
        # meal data instead of showing expenses with no meals behind them.
        cats = ['rice','fish','meat','vegetables','oil_spices','dairy','fuel','other']
        for day in range(1, seed_through_day + 1):
            date = datetime.date(y, m, day)
            n_entries = random.randint(1, 3)
            for _ in range(n_entries):
                Expense.objects.get_or_create(
                    mess=mess, date=date, description=f'Day {day} purchase',
                    defaults={'amount': Decimal(str(random.randint(300,1500))),
                              'category': random.choice(cats)}
                )

        # Deposits — dated within the same days 1–15 window.
        for mem in all_members:
            n_deps = random.randint(1, 4)
            for i in range(n_deps):
                dep_day = random.randint(1, seed_through_day)
                Deposit.all_objects.create(
                    member=mem, amount=Decimal(str(random.randint(500,3000))),
                    date=datetime.date(y, m, dep_day),
                    method=random.choice(['cash','bkash','nagad']),
                    note='Sample deposit'
                )

        self.stdout.write(self.style.SUCCESS(
            f'\nSeed complete! Mess ID: {mess.code}  Login: phone=01711000001  pass=1234  (Manager)'))
