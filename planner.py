def plan(left, right):
    a, b = left.manifest['layout'], right.manifest['layout']
    small, large = sorted((a['buckets'], b['buckets']))
    compatible = (a['family'] == b['family'] and
                  a['hash'] == b['hash'] and large % small == 0)
    if compatible:
        return {
            'mode': 'reuse' if small == large else 'coalesce',
            'groups': small,
            'reason': 'equal key family/hash; divisible bucket counts'
        }
    return {'mode': 'gather', 'groups': 1,
            'reason': 'no reusable distribution declared'}
