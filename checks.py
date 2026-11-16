import json
import sys
from copy import deepcopy
from lab import load_samples, run_case, pair_multiset
from layout import bucket
from storage import MemoryObjects
from table import Table, write_table
from join import execute


def same(actual, expected):
    assert pair_multiset(actual) == pair_multiset(expected), (actual, expected)


def normal():
    samples = load_samples()
    result = run_case(samples['normal'])
    same(result['pairs'], [['f1', 'd1'], ['f1', 'd2'],
                           ['f2', 'd1'], ['f2', 'd2'],
                           ['fn', None], ['f3', None]])
    assert result['trace']['plan']['mode'] == 'reuse'
    result = run_case(samples['coalesce'])
    same(result['pairs'], [['a', 'x'], ['b', 'y']])
    assert result['trace']['plan']['mode'] == 'coalesce'
    reverse = deepcopy(samples['coalesce'])
    reverse['left'], reverse['right'] = reverse['right'], reverse['left']
    same(run_case(reverse)['pairs'], [['x', 'a'], ['y', 'b']])
    empty = deepcopy(samples['normal'])
    empty['right']['rows'] = []
    same(run_case(empty)['pairs'],
         [['f1', None], ['f2', None], ['fn', None], ['f3', None]])
    for encoding in ('le-v1', 'text-v1'):
        case = deepcopy(samples['normal'])
        for side in ('left', 'right'):
            case[side]['encoding'] = encoding
        same(run_case(case)['pairs'], run_case(samples['normal'])['pairs'])
    objects = MemoryObjects()
    left = write_table(objects, 'left', samples['normal']['left'])
    right = write_table(objects, 'right', samples['normal']['right'])
    reopened = Table(objects, 'left')
    assert sorted(reopened.read_all(), key=lambda r: r['id']) == sorted(
        samples['normal']['left']['rows'], key=lambda r: r['id'])
    for i in range(2):
        for row in left.read_bucket(i):
            assert bucket(left.manifest['schema'], row['key'], 'le-v1', 2) == i
    same(execute(reopened, right, 'left')['pairs'],
         run_case(samples['normal'])['pairs'])
    try:
        objects.put('left/manifest.json', b'changed')
    except FileExistsError:
        pass
    else:
        raise AssertionError('immutable object overwritten')
    print('NORMAL_REGRESSION_OK reuse/coalesce/duplicates/null/empty/reopen')


def incident():
    result = run_case(load_samples()['incident'])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    expected = [['fact', 'dim']]
    if pair_multiset(result['pairs']) != pair_multiset(expected):
        print('BUCKET_IDENTITY_LOSS expected=' + json.dumps(expected) +
              ' actual=' + json.dumps(result['pairs']))
        return 1
    return 0


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'normal'
    if mode == 'normal':
        normal()
    elif mode == 'incident':
        sys.exit(incident())
    elif mode == 'delivery':
        normal()
        from evidence_gate import verify
        verify()
    else:
        raise SystemExit('unknown check mode')
