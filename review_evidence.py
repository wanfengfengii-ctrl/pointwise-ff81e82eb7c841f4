"""Independent expectations and replayable evidence for the bucket-layout review.

The oracle below reads only the original case rows/schema.  It deliberately does
not import layout/table/planner/join, so it cannot inherit the production key
encoding, bucketing, planning or join predicate it is meant to check.
"""

import argparse
import json
import re
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    # Script directory is not on sys.path under `python3 -I`; lab/join are local.
    sys.path.insert(0, str(ROOT))
CASES = ROOT / 'review_cases.json'
EVIDENCE = ROOT / 'evidence.json'

# (source path, 1-based line, why the review cites it)
LOCATION_SPECS = [
    ('layout.py', 43, 'numeric_value: exact Fraction comparison domain'),
    ('layout.py', 52, 'key_bytes: type and scale are part of the hashed prefix'),
    ('layout.py', 53, 'key_bytes: encoding selects the physical byte form'),
    ('layout.py', 54, 'key_bytes: le-v1 stores an 8-byte little-endian coefficient'),
    ('layout.py', 55, 'key_bytes: text-v1 stores an ASCII coefficient'),
    ('layout.py', 61, 'bucket: hash covers the encoded bytes'),
    ('layout.py', 62, 'bucket: bucket index is digest modulo count'),
    ('table.py', 28, 'write_table: rows are placed by bucket(schema,key,encoding,count)'),
    ('table.py', 34, "manifest: layout.family is only declared as 'numeric'"),
    ('table.py', 35, 'manifest: encoding and bucket count are published'),
    ('planner.py', 4, 'plan: compatibility only checks family and hash'),
    ('planner.py', 5, 'plan: compatibility adds divisibility, not key signature'),
    ('planner.py', 8, 'plan: reuse/coalesce is chosen from that weak predicate'),
    ('join.py', 14, 'execute: left bucket group is built by stride'),
    ('join.py', 16, 'execute: assumed-aligned left buckets are read'),
    ('join.py', 17, 'execute: assumed-aligned right buckets are read'),
    ('join.py', 28, 'execute: per-group exact numeric predicate'),
    ('join.py', 31, 'execute: left join tracks unmatched rows'),
    ('join.py', 32, 'execute: unmatched left rows are emitted with null'),
    ('lab.py', 26, 'run_case: actual placements are exposed for inspection'),
]


def _decimal_coefficient(key, scale):
    """Parse a fixed-scale decimal independently of layout.coefficient."""
    if type(key) is not str:
        raise ValueError('decimal key must be a string')
    pattern = r'-?(0|[1-9][0-9]*)'
    if scale:
        pattern += r'\.[0-9]{' + str(scale) + r'}'
    if re.fullmatch(pattern, key) is None:
        raise ValueError('not a fixed-scale decimal: ' + repr(key))
    negative = key.startswith('-')
    digits = key[1:] if negative else key
    if scale:
        whole, frac = digits.split('.')
    else:
        whole, frac = digits, ''
    value = int(whole) * (10 ** scale) + (int(frac) if scale else 0)
    return -value if negative else value


def numeric_value(schema, key):
    """Exact rational value, or None for NULL; independent implementation."""
    if key is None:
        return None
    kind, scale = schema['kind'], schema['scale']
    if kind == 'i64':
        if type(key) is not int or type(key) is bool:
            raise ValueError('i64 key must be an int')
        return Fraction(key, 1)
    if kind != 'decimal':
        raise ValueError('unknown kind')
    return Fraction(_decimal_coefficient(key, scale), 10 ** scale)


def expected(case):
    """Join rows pairwise by exact numeric value, preserving duplicates."""
    left, right, how = case['left'], case['right'], case['how']
    if how not in ('inner', 'left'):
        raise ValueError('join mode')
    out = []
    for lrow in left['rows']:
        lv = numeric_value(left['schema'], lrow['key'])
        matched = False
        if lv is not None:
            for rrow in right['rows']:
                rv = numeric_value(right['schema'], rrow['key'])
                if rv is not None and rv == lv:
                    out.append([lrow['id'], rrow['id']])
                    matched = True
        if how == 'left' and not matched:
            out.append([lrow['id'], None])
    # Stable ordering only; the comparison is by multiset.
    return sorted(out, key=lambda p: (p[0], p[1] is not None, p[1] or ''))


def load_cases():
    return json.loads(CASES.read_text(encoding='utf-8'))


def locations():
    out = []
    cached = {}
    for path, line, note in LOCATION_SPECS:
        if path not in cached:
            cached[path] = (ROOT / path).read_text(encoding='utf-8').splitlines()
        lines = cached[path]
        if not 1 <= line <= len(lines):
            raise ValueError('location out of range: %s:%d' % (path, line))
        # Contract shape is exactly {path,line,text}; the human note lives in
        # REVIEW.md's source-location table, not in machine evidence.
        out.append({'path': path, 'line': line, 'text': lines[line - 1]})
    return out


def write_evidence():
    from lab import run_case

    cases = load_cases()
    records = []
    for case in cases:
        records.append({
            'name': case['name'],
            'expected': expected(case),
            'observed': run_case(case),
        })
    evidence = {'cases': records, 'locations': locations()}
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')
    print('wrote %s: %d cases, %d locations' %
          (EVIDENCE.name, len(records), len(evidence['locations'])))


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _show_layout(side, table):
    manifest = table['manifest']
    layout = manifest['layout']
    return '%s schema=%s encoding=%s buckets=%d family=%s placements=%s' % (
        side, _compact(manifest['schema']), layout['encoding'],
        layout['buckets'], layout['family'], _compact(table['placements']))


def demo():
    from lab import run_case

    cases = load_cases()
    print('== independent oracle vs. actual execution ==')
    for case in cases:
        result = run_case(case)
        want = expected(case)
        got = result['pairs']
        plan = result['trace']['plan']
        print('\n-- %s (how=%s)' % (case['name'], case['how']))
        print('   ' + _show_layout('left', result['tables']['left']))
        print('   ' + _show_layout('right', result['tables']['right']))
        print('   plan=%s' % _compact(plan))
        for group in result['trace']['groups']:
            print('   group %d left_buckets=%s right_buckets=%s left_ids=%s right_ids=%s' % (
                group['group'], group['left_buckets'], group['right_buckets'],
                group['left_ids'], group['right_ids']))
        print('   expected=%s' % _compact(want))
        print('   actual  =%s' % _compact(got))
        print('   verdict =%s' % ('MATCH' if _multiset(want) == _multiset(got)
                                  else 'MISMATCH'))

    # One case is also written through the real directory object backend so the
    # demo shows persisted part files, not just in-memory placements.
    incident = next(c for c in cases if c['name'] == 'incident_original_layout_split')
    from join import execute
    from lab import build_case
    from storage import DirectoryObjects
    with tempfile.TemporaryDirectory(prefix='bucket-review-') as tmp:
        left, right = build_case(incident, DirectoryObjects(tmp))
        print('\n== on-disk demonstration: %s ==' % incident['name'])
        for side, table, prefix in (('left', left, 'left'), ('right', right, 'right')):
            print('   %s manifest: %s' % (side, _compact(table.manifest['layout'])))
            for index in range(len(table.manifest['files'])):
                rows = table.read_bucket(index)
                print('   %s/part-%d.json rows=%s' % (prefix, index,
                                                      [r['id'] for r in rows]))
        result = execute(left, right, incident['how'])
        print('   plan=%s' % _compact(result['trace']['plan']))
        print('   actual  =%s' % _compact(result['pairs']))
        print('   expected=%s' % _compact(expected(incident)))


def _multiset(pairs):
    return sorted(_compact(pair) for pair in pairs)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    if args.write:
        write_evidence()
    if args.demo:
        demo()
    if not args.write and not args.demo:
        parser.error('choose --write or --demo')
