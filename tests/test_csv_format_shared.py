"""Shared bounded CSV parsing behavior used by all V2 import surfaces."""
from __future__ import annotations

import csv
import gzip
import io

import pytest

from scripts.server.csv_format import (
    decompress_gzip,
    normalize_delimiter,
    resolve_csv_format,
)


def test_resolves_gb18030_semicolon_without_splitting_quoted_delimiters():
    raw = ('time_s;adcs.rw.speed;note\r\n'
           '0;1.5;"中文;说明"\r\n'
           '1;2.5;"第二行,含逗号"\r\n').encode('gb18030')

    text, encoding, delimiter = resolve_csv_format(raw, 'auto', 'auto')
    normalized = normalize_delimiter(text, delimiter, max_rows=50_000, max_bytes=12_000_000)
    rows = list(csv.reader(io.StringIO(normalized, newline='')))

    assert (encoding, delimiter) == ('gb18030', ';')
    assert rows == [
        ['time_s', 'adcs.rw.speed', 'note'],
        ['0', '1.5', '中文;说明'],
        ['1', '2.5', '第二行,含逗号'],
    ]


def test_resolves_utf8_bom_tab_and_keeps_original_header_values():
    raw = '\ufefftime_s\tvoltage\tlabel\r\n0\t28.1\t0\r\n1\t28.2\t1\r\n'.encode('utf-8')
    text, encoding, delimiter = resolve_csv_format(raw, 'auto', 'auto')
    normalized = normalize_delimiter(text, delimiter, max_rows=50_000, max_bytes=12_000_000)

    assert (encoding, delimiter) == ('utf-8', '\t')
    assert next(csv.reader(io.StringIO(normalized))) == ['time_s', 'voltage', 'label']


def test_bounded_gzip_rejects_trailing_data_and_expansion_over_limit():
    valid = gzip.compress(b'time_s,speed\n0,1\n1,2\n')
    assert decompress_gzip(valid, max_output_bytes=100) == b'time_s,speed\n0,1\n1,2\n'
    with pytest.raises(ValueError, match='尾部'):
        decompress_gzip(valid + b'extra', max_output_bytes=100)
    with pytest.raises(ValueError, match='超过'):
        decompress_gzip(gzip.compress(b'x' * 101), max_output_bytes=100)


def test_explicit_encoding_and_delimiter_are_not_silently_overridden():
    raw = 'time_s;speed;note\n0;1;中文\n1;2;说明\n'.encode('gb18030')
    text, encoding, delimiter = resolve_csv_format(raw, 'gb18030', ';')
    assert (encoding, delimiter) == ('gb18030', ';')
    assert text.splitlines()[1] == '0;1;中文'
    with pytest.raises(ValueError, match='编码'):
        resolve_csv_format(raw, 'utf-8', ';')
    with pytest.raises(ValueError, match='分隔符'):
        resolve_csv_format(raw, 'gb18030', '|')
