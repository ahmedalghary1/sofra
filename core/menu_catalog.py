"""One public representation for menu items and complementary-item cards."""


def serialize_menu_item(item):
    return {'id': item.pk, 'category': item.category_id, 'name': item.name, 'description': item.description,
        'price': str(item.price), 'image': item.image.url if item.image else '',
        'sizes': [{'id': size.pk, 'name': size.name, 'price': str(size.price)}
                  for size in item.sizes.all() if size.available],
        'groups': [{'name': group.name, 'min': group.min_select, 'max': group.max_select,
                    'options': [{'id': option.pk, 'name': option.name, 'price': str(option.price)}
                                for option in group.options.all() if option.available]}
                   for group in item.modifier_groups.all()]}
