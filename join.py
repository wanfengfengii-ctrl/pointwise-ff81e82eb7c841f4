from layout import numeric_value
from planner import plan


def execute(left, right, how='inner'):
    if how not in ('inner', 'left'):
        raise ValueError('join mode')
    decision = plan(left, right)
    trace = {'plan': decision, 'groups': []}
    pairs = []
    ls, rs = left.manifest['schema'], right.manifest['schema']
    groups = decision['groups']
    for group in range(groups):
        li = list(range(group, len(left.manifest['files']), groups))
        ri = list(range(group, len(right.manifest['files']), groups))
        lrows = [r for i in li for r in left.read_bucket(i)]
        rrows = [r for i in ri for r in right.read_bucket(i)]
        trace['groups'].append({
            'group': group, 'left_buckets': li, 'right_buckets': ri,
            'left_ids': [r['id'] for r in lrows],
            'right_ids': [r['id'] for r in rrows]
        })
        for lrow in lrows:
            lv = numeric_value(ls, lrow['key'])
            matched = False
            for rrow in rrows:
                rv = numeric_value(rs, rrow['key'])
                if lv is not None and rv is not None and lv == rv:
                    pairs.append([lrow['id'], rrow['id']])
                    matched = True
            if how == 'left' and not matched:
                pairs.append([lrow['id'], None])
    return {'pairs': pairs, 'trace': trace}
