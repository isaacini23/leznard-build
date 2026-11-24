from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.organizations.models import Organization


@receiver(post_save, sender=Organization, dispatch_uid="accounting.create_default_chart")
def create_default_chart_for_new_organization(sender, instance, created, **kwargs):
    if created:
        from apps.accounting.services import create_default_chart_of_accounts
        create_default_chart_of_accounts(instance)
