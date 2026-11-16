import hashlib
import re
from fractions import Fraction

MIN_I64 = -(1 << 63)
MAX_I64 = (1 << 63) - 1
ENCODINGS = ('le-v1', 'text-v1')


def validate_schema(schema):
    if set(schema) != {'kind', 'scale'}:
        raise ValueError('schema fields')
    kind, scale = schema['kind'], schema['scale']
    if type(scale) is not int or not 0 <= scale <= 6:
        raise ValueError('scale')
    if kind not in ('i64', 'decimal') or (kind == 'i64' and scale != 0):
        raise ValueError('kind/scale')


def coefficient(schema, key):
    validate_schema(schema)
    if key is None:
        return None
    if schema['kind'] == 'i64':
        if type(key) is not int:
            raise ValueError('i64 key')
        value = key
    else:
        scale = schema['scale']
        pattern = r'-?(0|[1-9][0-9]*)'
        if scale:
            pattern += r'\.[0-9]{' + str(scale) + '}'
        if type(key) is not str or re.fullmatch(pattern, key) is None:
            raise ValueError('fixed-scale decimal key')
        value = int(key.replace('.', ''))
    if not MIN_I64 <= value <= MAX_I64:
        raise ValueError('coefficient outside signed 64 bits')
    return value


def numeric_value(schema, key):
    value = coefficient(schema, key)
    return None if value is None else Fraction(value, 10 ** schema['scale'])


def key_bytes(schema, key, encoding):
    value = coefficient(schema, key)
    if encoding not in ENCODINGS:
        raise ValueError('encoding')
    if value is None:
        return b'NULL'
    prefix = (schema['kind'] + ':' + str(schema['scale']) + ':').encode('ascii')
    if encoding == 'le-v1':
        return prefix + value.to_bytes(8, 'little', signed=True)
    return prefix + str(value).encode('ascii')


def bucket(schema, key, encoding, count):
    if type(count) is not int or count not in (2, 4):
        raise ValueError('bucket count')
    digest = hashlib.sha256(key_bytes(schema, key, encoding)).digest()
    return int.from_bytes(digest, 'big') % count
