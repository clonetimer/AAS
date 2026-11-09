"""Shared, bounded CSV byte decoding and delimiter handling for V2 imports.

The functions here operate on the original byte payload and never persist it.
Callers retain ownership of their business validation, row limits and SHA-256.
"""
from __future__ import annotations

import codecs
import csv
import io
from pathlib import Path
import zlib

CSV_ENCODINGS = {'utf-8': 'utf-8-sig', 'gb18030': 'gb18030'}
CSV_DELIMITERS = {',', ';', '\t'}


def decode_csv(raw: bytes, encoding: str) -> str:
    if encoding not in CSV_ENCODINGS:
        raise ValueError('CSV 编码只能选择 UTF-8 或 GB18030')
    try:
        result = raw.decode(CSV_ENCODINGS[encoding])
    except UnicodeError as exc:
        raise ValueError(f'CSV 文件无法按 {encoding} 编码读取；请返回检查列并调整编码') from exc
    if '\x00' in result:
        raise ValueError('CSV 包含二进制空字符')
    return result


def decompress_gzip(raw: bytes, max_output_bytes: int) -> bytes:
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ValueError('GZIP 解压上限必须为正整数')
    if not raw.startswith(b'\x1f\x8b'):
        raise ValueError('上传内容不是 GZIP 文件')
    limit_label = (f'{max_output_bytes // 1_000_000}MB'
                   if max_output_bytes % 1_000_000 == 0 else f'{max_output_bytes} 字节')
    try:
        decomp = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
        output = decomp.decompress(raw, max_output_bytes + 1)
        if len(output) > max_output_bytes or decomp.unconsumed_tail:
            raise ValueError(f'解压后超过 {limit_label}，请分文件或分时段导入')
        output += decomp.flush(max_output_bytes + 1 - len(output))
        if len(output) > max_output_bytes:
            raise ValueError(f'解压后超过 {limit_label}，请分文件或分时段导入')
        if not decomp.eof or decomp.unused_data:
            raise ValueError('GZIP 不是单个完整数据流，或包含额外尾部数据')
        return output
    except zlib.error as exc:
        raise ValueError('GZIP 损坏或压缩流无效') from exc


def detect_delimiter(text: str) -> str:
    """Prefer a telemetry time-column header, then use a bounded Sniffer sample."""
    first_line = text.partition('\n')[0].rstrip('\r')
    for delimiter in (',', ';', '\t'):
        try:
            columns = next(csv.reader([first_line], delimiter=delimiter, strict=True))
        except (csv.Error, StopIteration):
            continue
        if len(columns) >= 2 and ('time_s' in columns or 'timestamp' in columns):
            return delimiter
    try:
        guessed = csv.Sniffer().sniff(text[:8192], delimiters=',;\t').delimiter
        if guessed in CSV_DELIMITERS:
            return guessed
    except csv.Error:
        pass
    return ','


def normalize_delimiter(text: str, delimiter: str, *, max_rows: int = 50_000,
                        max_bytes: int = 12_000_000) -> str:
    if delimiter not in CSV_DELIMITERS:
        raise ValueError('CSV 分隔符只能选择逗号、分号或 TAB')
    if delimiter == ',':
        return text
    source = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
    converted = io.StringIO(newline='')
    writer = csv.writer(converted, lineterminator='\n')
    expected = None
    try:
        for index, fields in enumerate(source):
            if index >= max_rows + 1:
                raise ValueError(f'CSV 行数超过 {max_rows}，请分批导入')
            if expected is None:
                expected = len(fields)
            elif len(fields) != expected:
                raise ValueError(f'CSV 第 {source.line_num} 行列数不一致，请检查分隔符或源文件')
            writer.writerow(fields)
            if converted.tell() > max_bytes:
                raise ValueError(f'CSV 转换后超过 {max_bytes} 字节，请分批导入')
    except csv.Error as exc:
        raise ValueError(f'CSV 第 {source.line_num} 行引号/字段格式错误') from exc
    return converted.getvalue()


def resolve_csv_format(raw: bytes, encoding: str = 'auto', delimiter: str = 'auto', *,
                       max_output_bytes: int = 12_000_000) -> tuple[str, str, str]:
    """Decode a plain CSV payload and resolve explicit/automatic format choices."""
    if encoding in (None, 'auto'):
        try:
            text = decode_csv(raw, 'utf-8')
            resolved_encoding = 'utf-8'
        except ValueError as exc:
            if not isinstance(exc.__cause__, UnicodeError):
                raise
            text = decode_csv(raw, 'gb18030')
            resolved_encoding = 'gb18030'
    else:
        if encoding not in CSV_ENCODINGS:
            raise ValueError('CSV 编码只能选择自动识别、UTF-8 或 GB18030')
        text = decode_csv(raw, encoding)
        resolved_encoding = encoding
    if len(raw) > max_output_bytes:
        raise ValueError(f'普通 CSV 超过 {max_output_bytes} 字节，请分批导入')
    if delimiter in (None, 'auto'):
        resolved_delimiter = detect_delimiter(text)
    else:
        if delimiter not in CSV_DELIMITERS:
            raise ValueError('CSV 分隔符只能选择自动识别、逗号、分号或 TAB')
        resolved_delimiter = delimiter
    return text, resolved_encoding, resolved_delimiter


def decompress_gzip_file(source: Path, destination: Path, max_output_bytes: int, *,
                         cancel_requested=None, progress=None, chunk_size: int = 256 * 1024) -> int:
    """Decompress one GZIP stream incrementally into a bounded disk file."""
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ValueError('GZIP 解压上限必须为正整数')
    source = Path(source)
    destination = Path(destination)
    total_input = source.stat().st_size
    written = 0
    try:
        with source.open('rb') as input_file, destination.open('wb') as output_file:
            if input_file.read(2) != b'\x1f\x8b':
                raise ValueError('上传内容不是 GZIP 文件')
            input_file.seek(0)
            decomp = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
            while True:
                if cancel_requested is not None and cancel_requested():
                    raise InterruptedError('用户取消 GZIP 解压')
                chunk = input_file.read(chunk_size)
                if not chunk:
                    break
                pending = chunk
                while pending:
                    remaining = max_output_bytes - written
                    decoded = decomp.decompress(pending, remaining + 1)
                    if decoded:
                        written += len(decoded)
                        if written > max_output_bytes:
                            raise ValueError(f'解压后超过 {max_output_bytes // 1_000_000}MB，请分文件或分时段导入')
                        output_file.write(decoded)
                    if decomp.unused_data:
                        raise ValueError('GZIP 不是单个完整数据流，或包含额外尾部数据')
                    pending = decomp.unconsumed_tail
                    if pending and written >= max_output_bytes:
                        raise ValueError(f'解压后超过 {max_output_bytes // 1_000_000}MB，请分文件或分时段导入')
                if progress is not None:
                    progress(input_file.tell(), total_input)
            tail = decomp.flush()
            written += len(tail)
            if written > max_output_bytes:
                raise ValueError(f'解压后超过 {max_output_bytes // 1_000_000}MB，请分文件或分时段导入')
            if tail:
                output_file.write(tail)
            if not decomp.eof or decomp.unused_data or decomp.unconsumed_tail:
                raise ValueError('GZIP 不是单个完整数据流，或包含额外尾部数据')
            output_file.flush()
        return written
    except zlib.error as exc:
        raise ValueError('GZIP 损坏或压缩流无效') from exc


def resolve_csv_stream_format(path: Path, encoding: str = 'auto', delimiter: str = 'auto') -> tuple[str, str]:
    """Resolve encoding and delimiter from a bounded sample of a disk-backed CSV.

    Encoding detection validates the complete file incrementally, so a GB18030
    byte sequence beyond the first sample cannot be misclassified as UTF-8.
    """
    if encoding not in (None, 'auto', *CSV_ENCODINGS):
        raise ValueError('CSV 编码只能选择自动识别、UTF-8 或 GB18030')
    if delimiter not in (None, 'auto', *CSV_DELIMITERS):
        raise ValueError('CSV 分隔符只能选择自动识别、逗号、分号或 TAB')
    path = Path(path)

    def validate(candidate: str) -> bool:
        decoder = codecs.getincrementaldecoder(CSV_ENCODINGS[candidate])()
        try:
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(256 * 1024), b''):
                    if b'\x00' in chunk:
                        raise ValueError('CSV 包含二进制空字符')
                    decoder.decode(chunk, final=False)
                decoder.decode(b'', final=True)
            return True
        except UnicodeError:
            return False

    if encoding in (None, 'auto'):
        if validate('utf-8'):
            resolved_encoding = 'utf-8'
        elif validate('gb18030'):
            resolved_encoding = 'gb18030'
        else:
            raise ValueError('CSV 文件无法按 UTF-8 或 GB18030 编码读取')
    else:
        resolved_encoding = encoding
        if not validate(resolved_encoding):
            raise ValueError(f'CSV 文件无法按 {encoding} 编码读取；请返回检查列并调整编码')

    with path.open('rb') as handle:
        sample = handle.read(8196)
    if not sample:
        raise ValueError('CSV 文件为空')
    decoder = codecs.getincrementaldecoder(CSV_ENCODINGS[resolved_encoding])()
    text = decoder.decode(sample, final=False)
    if '\x00' in text:
        raise ValueError('CSV 包含二进制空字符')
    resolved_delimiter = detect_delimiter(text) if delimiter in (None, 'auto') else delimiter
    return resolved_encoding, resolved_delimiter
