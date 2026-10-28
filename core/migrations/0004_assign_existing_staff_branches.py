from django.db import migrations

def assign_existing_staff(apps, schema_editor):
    Membership = apps.get_model('core', 'Membership')
    Branch = apps.get_model('core', 'Branch')
    for membership in Membership.objects.exclude(role__in=['owner', 'manager']).iterator():
        branch = Branch.objects.filter(restaurant_id=membership.restaurant_id).order_by('id').first()
        if branch:
            membership.branches.add(branch)

class Migration(migrations.Migration):
    dependencies = [('core', '0003_branch_active_membership_branches_plan_max_branches')]
    operations = [migrations.RunPython(assign_existing_staff, migrations.RunPython.noop)]
