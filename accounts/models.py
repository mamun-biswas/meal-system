from django.db import models
from django.contrib.auth.models import User
from decimal import Decimal
import datetime, random, string

from .mess_context import get_current_mess


def generate_mess_code():
    """Short, unique, human-typeable code used to identify a mess at login
    (e.g. 'AB3K7Q'). Regenerated on collision."""
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choice(alphabet) for _ in range(6))
        if not Mess.objects.filter(code=code).exists():
            return code


class Mess(models.Model):
    """A single, independent mess / hostel household. Every Member, and all
    meal/finance data, belongs to exactly one Mess. Different messes never
    see each other's data."""
    name       = models.CharField(max_length=150)
    code       = models.CharField(max_length=12, unique=True, editable=False)
    address    = models.CharField(max_length=255, blank=True)
    is_active  = models.BooleanField(default=True)
    # Approval gate: a brand-new mess (created via the public "Register
    # your mess" page) starts unapproved and its members can't use the
    # app until a Global Admin (Django superuser) approves it from the
    # admin panel. Defaults to True so any mess that already existed
    # before this feature was added is automatically grandfathered in as
    # approved — only NEW registrations are explicitly set to False.
    is_approved  = models.BooleanField(default=True)
    approved_at  = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = generate_mess_code()
        super().save(*args, **kwargs)

    def manager(self):
        return self.members.filter(role=Member.ROLE_MANAGER).first()


class AdminMessage(models.Model):
    """A message sent by the Global Admin to a specific mess's Manager,
    shown as a banner the next time anyone from that mess logs in."""
    mess       = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='admin_messages')
    message    = models.TextField()
    sent_by    = models.CharField(max_length=150, blank=True)  # admin username, for reference
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"To {self.mess.name}: {self.message[:50]}"


class MessScopedManager(models.Manager):
    """Default manager for models with a direct `mess` FK. Automatically
    filters to the current request's mess (set by MessMiddleware). Outside
    a request context (management commands, admin, tests without a logged
    in member) it returns everything unfiltered."""
    def get_queryset(self):
        qs = super().get_queryset()
        mess = get_current_mess()
        if mess is not None:
            qs = qs.filter(mess=mess)
        return qs


class MemberMessScopedManager(models.Manager):
    """Same as MessScopedManager but for models scoped via `member__mess`
    (i.e. models that link to Member rather than Mess directly)."""
    def get_queryset(self):
        qs = super().get_queryset()
        mess = get_current_mess()
        if mess is not None:
            qs = qs.filter(member__mess=mess)
        return qs


class MemberManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        mess = get_current_mess()
        if mess is not None:
            qs = qs.filter(mess=mess)
        return qs


class Member(models.Model):
    ROLE_MANAGER     = 'manager'
    ROLE_SUB_MANAGER = 'sub_manager'
    ROLE_MEMBER      = 'member'
    ROLE_CHOICES = [
        (ROLE_MANAGER,     'Manager'),
        (ROLE_SUB_MANAGER, 'Sub Manager'),
        (ROLE_MEMBER,      'Member'),
    ]
    mess        = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='members')
    user        = models.OneToOneField(User, on_delete=models.CASCADE, related_name='member')
    phone       = models.CharField(max_length=20)
    name        = models.CharField(max_length=100)
    name_bn     = models.CharField(max_length=100, blank=True, verbose_name='Bangla Name')
    room_number = models.CharField(max_length=20, blank=True)
    role        = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    is_active   = models.BooleanField(default=True)
    joined_date = models.DateField(default=datetime.date.today)
    note        = models.TextField(blank=True)
    avatar_color= models.CharField(max_length=7, default='#6366f1')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    objects     = MemberManager()
    all_objects = models.Manager()   # unfiltered — use for login/registration/admin

    class Meta:
        ordering = ['joined_date','name']
        unique_together = ('mess', 'phone')

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    def initials(self):
        parts = self.name.strip().split()
        return ''.join(p[0] for p in parts[:2]).upper() or '?'

    def is_manager(self):
        return self.role == self.ROLE_MANAGER

    def is_sub_manager(self):
        return self.role == self.ROLE_SUB_MANAGER

    def has_perm_code(self, codename):
        if self.role == self.ROLE_MANAGER:
            return True
        if self.role == self.ROLE_SUB_MANAGER:
            p = SubManagerPermission.objects.filter(member=self, codename=codename).first()
            return p.granted if p else False
        return False

    def can_edit(self):
        return self.role in [self.ROLE_MANAGER, self.ROLE_SUB_MANAGER]

    def get_total_meals_all_time(self):
        from meals.models import MealMark
        from django.db.models import Sum, F
        result = MealMark.objects.filter(member=self).aggregate(
            t=Sum(F('morning') + F('lunch') + F('dinner'))
        )['t']
        return result or Decimal('0')

    def get_total_deposited_all_time(self):
        from finance.models import Deposit
        from django.db.models import Sum
        result = Deposit.objects.filter(member=self).aggregate(t=Sum('amount'))['t']
        return result or Decimal('0')


class SubManagerPermission(models.Model):
    ALL_PERMS = [
        ('meal_mark',      'Mark / Edit Meals'),
        ('bazar_entry',    'Add / Edit Expenses'),
        ('deposit_entry',  'Add Deposits'),
        ('delete_deposit', 'Delete Deposits'),
        ('view_reports',   'View Reports'),
        ('export_data',    'Export Data'),
        ('manage_members', 'Manage Members'),
        ('day_config',     'Configure Special Meal Entry'),
        ('announcements',  'Post Announcements'),
        ('monthly_close',  'Close Monthly Accounts'),
        ('member_input_control', 'Turn Member Self-Input On/Off'),
    ]
    member   = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='sub_permissions')
    codename = models.CharField(max_length=50)
    granted  = models.BooleanField(default=False)

    class Meta:
        unique_together = ('member','codename')

    def __str__(self):
        return f"{self.member.name} | {self.codename} | {'✓' if self.granted else '✗'}"


class Notification(models.Model):
    TYPE_INFO    = 'info'
    TYPE_WARNING = 'warning'
    TYPE_SUCCESS = 'success'
    TYPE_URGENT  = 'urgent'
    TYPE_CHOICES = [(TYPE_INFO,'Info'),(TYPE_WARNING,'Warning'),(TYPE_SUCCESS,'Success'),(TYPE_URGENT,'Urgent')]

    mess       = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='notifications')
    recipient  = models.ForeignKey(Member, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    broadcast  = models.BooleanField(default=False)   # True = all members
    title      = models.CharField(max_length=200)
    message    = models.TextField()
    ntype      = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_INFO)
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Links this notification back to the Announcement that spawned it (if any),
    # so that deleting the Announcement can also clean up its Notification.
    # String reference avoids a circular import (meals.models imports accounts.models).
    announcement = models.ForeignKey(
        'meals.Announcement', on_delete=models.CASCADE, null=True, blank=True,
        related_name='notifications'
    )

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-created_at']


class ActivityLog(models.Model):
    mess      = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='activity_logs')
    member    = models.ForeignKey(Member, on_delete=models.SET_NULL, null=True, blank=True)
    action    = models.CharField(max_length=300)
    detail    = models.TextField(blank=True)
    ip        = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.member} | {self.action}"
