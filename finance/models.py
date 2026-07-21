from django.db import models
from decimal import Decimal
import datetime

from accounts.models import Mess, MessScopedManager, MemberMessScopedManager


class Deposit(models.Model):
    METHOD = [('cash','Cash'),('bkash','bKash'),('nagad','Nagad'),
              ('rocket','Rocket'),('bank','Bank Transfer'),('other','Other')]
    member   = models.ForeignKey('accounts.Member', on_delete=models.CASCADE, related_name='deposits')
    amount   = models.DecimalField(max_digits=10, decimal_places=2)
    date     = models.DateField(default=datetime.date.today)
    method   = models.CharField(max_length=20, choices=METHOD, default='cash')
    note     = models.CharField(max_length=300, blank=True)
    added_by = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='deposits_added')
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MemberMessScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-date','-created_at']
        indexes = [models.Index(fields=['member','date']), models.Index(fields=['date'])]

    def __str__(self):
        return f"{self.member.name} | ৳{self.amount} | {self.date}"


class Expense(models.Model):
    CATEGORY = [
        ('rice','Rice & Grain'),('fish','Fish & Seafood'),('meat','Meat & Poultry'),
        ('vegetables','Vegetables'),('oil_spices','Oil & Spices'),('dairy','Dairy & Eggs'),
        ('fuel','Gas & Fuel'),('cleaning','Cleaning'),('other','Other'),
    ]
    mess       = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='expenses')
    date       = models.DateField()
    amount     = models.DecimalField(max_digits=10, decimal_places=2)
    category   = models.CharField(max_length=20, choices=CATEGORY, default='other')
    description= models.CharField(max_length=300, blank=True)
    receipt    = models.ImageField(upload_to='receipts/%Y/%m/', blank=True, null=True)
    bought_by  = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_bought')
    added_by   = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses_added')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['-date','-created_at']
        indexes = [models.Index(fields=['date']), models.Index(fields=['category'])]

    def __str__(self):
        return f"Expense {self.date} | ৳{self.amount} | {self.get_category_display()}"


class MealClosing(models.Model):
    """Represents a finalized month-end closing for the mess."""
    STATUS_OPEN   = 'open'
    STATUS_CLOSED = 'closed'
    STATUS_CHOICES = [(STATUS_OPEN, 'Open'), (STATUS_CLOSED, 'Closed')]

    mess       = models.ForeignKey(Mess, on_delete=models.CASCADE, related_name='closings')
    month      = models.PositiveSmallIntegerField()
    year       = models.PositiveSmallIntegerField()
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN)
    total_exp  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_dep  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_eff  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    meal_rate  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cook_cost  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fund_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    note       = models.TextField(blank=True)
    closed_by  = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='closings_done')
    closed_at  = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = MessScopedManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ('mess', 'month', 'year')
        ordering = ['-year', '-month']

    def __str__(self):
        import calendar
        return f"Closing {calendar.month_name[self.month]} {self.year} [{self.status}]"

    def is_closed(self):
        return self.status == self.STATUS_CLOSED


class ClosingRecord(models.Model):
    """Per-member verdict for a MealClosing."""
    VERDICT_PAID     = 'paid'      # member owed money → they paid it
    VERDICT_RECEIVED = 'received'  # member overpaid → they received refund
    VERDICT_SETTLED  = 'settled'   # exact zero balance
    VERDICT_PENDING  = 'pending'   # not yet acted upon
    VERDICT_CHOICES = [
        (VERDICT_PAID,     'Paid'),
        (VERDICT_RECEIVED, 'Received'),
        (VERDICT_SETTLED,  'Settled'),
        (VERDICT_PENDING,  'Pending'),
    ]

    closing       = models.ForeignKey(MealClosing, on_delete=models.CASCADE,
                                       related_name='records')
    member        = models.ForeignKey('accounts.Member', on_delete=models.CASCADE,
                                       related_name='closing_records')
    eff_meals     = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    meal_only_cost= models.DecimalField(max_digits=10, decimal_places=2, default=0)
    cook_cost     = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_cost    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    deposit       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    balance       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    # balance > 0 → member should RECEIVE money back
    # balance < 0 → member should PAY the shortfall
    verdict       = models.CharField(max_length=10, choices=VERDICT_CHOICES,
                                      default=VERDICT_PENDING)
    verdict_note  = models.CharField(max_length=300, blank=True)
    verdict_by    = models.ForeignKey('accounts.Member', on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='verdicts_given')
    verdict_at    = models.DateTimeField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    objects = MemberMessScopedManager()
    all_objects = models.Manager()

    class Meta:
        unique_together = ('closing', 'member')
        ordering = ['member__name']

    def __str__(self):
        return f"{self.member.name} | {self.closing} | {self.verdict}"

    def amount_display(self):
        """Absolute balance amount for display."""
        from decimal import Decimal
        return abs(self.balance)

    def direction(self):
        """'receive' if owed money back, 'pay' if they owe."""
        if self.balance > 0:
            return 'receive'
        elif self.balance < 0:
            return 'pay'
        return 'settled'
