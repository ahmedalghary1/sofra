from django.db.models import Count, Q

from .models import Branch, KitchenStation, Membership


def preview_plan_usage(restaurant, plan):
    branches = list(Branch.objects.filter(restaurant=restaurant, archived=False).annotate(
        table_count=Count('tables', filter=Q(tables__archived=False), distinct=True),
        active_table_count=Count('tables', filter=Q(tables__active=True, tables__archived=False), distinct=True),
        item_count=Count('categories__items', filter=Q(categories__items__archived=False), distinct=True),
        active_item_count=Count('categories__items',
                                filter=Q(categories__items__available=True, categories__items__archived=False), distinct=True),
    ).order_by('id'))
    staff_count = Membership.objects.filter(restaurant=restaurant, archived=False).count()
    active_staff_count = Membership.objects.filter(restaurant=restaurant, active=True, archived=False).count()
    active_branch_count = sum(branch.active for branch in branches)
    for branch in branches:
        branch.table_excess = max(0, branch.table_count - plan.max_tables)
        branch.item_excess = max(0, branch.item_count - plan.max_items)
        branch.active_table_excess = max(0, branch.active_table_count - plan.max_tables)
        branch.active_item_excess = max(0, branch.active_item_count - plan.max_items)
        branch.station_count = KitchenStation.objects.filter(branch=branch, archived=False).count()
        branch.station_excess = max(0, branch.station_count - plan.max_stations)
    return {
        'plan': plan,
        'branches': branches,
        'branch_count': len(branches),
        'branch_excess': max(0, len(branches) - plan.max_branches),
        'active_branch_excess': max(0, active_branch_count - plan.max_branches),
        'staff_count': staff_count,
        'staff_excess': max(0, staff_count - plan.max_staff),
        'active_staff_excess': max(0, active_staff_count - plan.max_staff),
    }
