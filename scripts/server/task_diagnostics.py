"""Stable, user-facing failure explanations for V2 task operations.

Only curated messages and recovery actions leave the server. Original exception
strings and stack traces stay in server logs and are never copied into this
projection or the durable task journal.
"""
from __future__ import annotations

import errno
import sqlite3
from typing import Any


STAGE_LABELS = {
    'queued': '任务准备',
    'worker_start': '任务启动',
    'completed': '任务完成',
    'preflight': '运行前检查',
    'data_loading': '数据读取',
    'algorithm_execution': '算法执行',
    'model_validation': '模型保存与校验',
    'provenance_capture': '实验输入与来源记录',
    'report_archive': '实验报告归档',
    'evaluation_validation': '评估条件检查',
    'evaluation_execution': '模型评估',
    'score_only_validation': '仅推理条件检查',
    'score_only_execution': '模型仅推理',
    'batch_import': '批量数据导入',
    'csv_import': 'CSV 数据导入',
    'preprocessing': '数据预处理',
    'unknown': '任务执行',
}



class TaskOperationError(RuntimeError):
    """Carry the failing stage across a synchronous V2 HTTP boundary."""

    def __init__(self, stage: str, cause: BaseException, task_type: str = 'evaluation'):
        super().__init__(str(cause))
        self.stage = stage if stage in STAGE_LABELS else 'unknown'
        self.cause = cause
        self.task_type = task_type

def _diagnostic(code: str, stage: str, title: str, message: str,
                recovery_action: str, recovery_label: str,
                retryable: bool) -> dict[str, Any]:
    stage = stage if stage in STAGE_LABELS else 'unknown'
    return {
        'code': code,
        'stage': stage,
        'stage_label': STAGE_LABELS[stage],
        'title': title,
        'message': message,
        'recovery_action': recovery_action,
        'recovery_label': recovery_label,
        'retryable': bool(retryable),
    }


def diagnose_failure(error: BaseException | str | None, stage: str,
                     task_type: str = 'training') -> dict[str, Any]:
    """Map an internal exception to a stable, path-free recovery message."""
    stage = stage if stage in STAGE_LABELS else 'unknown'
    text = str(error or '').lower()
    name = type(error).__name__ if error is not None and not isinstance(error, str) else ''

    if isinstance(error, FileNotFoundError):
        return _diagnostic('input_missing', stage, '找不到输入文件或模型',
            '任务引用的数据文件或模型文件当前不可用。',
            '检查历史数据库和模型是否仍在原位置；本地 CSV 请重新导入，再重新预检。',
            '返回数据选择', False)
    if isinstance(error, PermissionError) or (isinstance(error, OSError) and
            getattr(error, 'errno', None) in (errno.EACCES, errno.EPERM)):
        return _diagnostic('permission_denied', stage, '没有目录写入权限',
            'AAS 无法读取所需文件或写入模型、实验报告。',
            '检查 AAS 安装目录、datasets、models 和 outputs 的当前用户读写权限。',
            '检查目录权限', False)
    if isinstance(error, OSError) and (getattr(error, 'errno', None) == errno.ENOSPC or
            getattr(error, 'winerror', None) == 112):
        return _diagnostic('disk_full', stage, '磁盘空间不足',
            '任务所需文件无法完整写入；已有原始数据不会被自动覆盖。',
            '释放 AAS 所在磁盘空间，确认 outputs/models 可用空间后，再重新提交任务。',
            '检查磁盘空间', True)
    if isinstance(error, sqlite3.OperationalError):
        if 'locked' in text or 'busy' in text:
            return _diagnostic('database_busy', stage, '数据库正在被占用',
                '数据库当前无法取得写入或读取锁。',
                '等待其他导入、预处理或任务结束后，再重新预检并运行。',
                '稍后重新运行', True)
        if 'no such table' in text or 'no such column' in text:
            return _diagnostic('database_schema_mismatch', stage, '数据库结构与当前操作不匹配',
                '当前数据库缺少操作所需的数据结构。',
                '在系统管理中确认当前活动数据库及版本；不要手工修改原始数据库表。',
                '检查数据库配置', False)
    if isinstance(error, sqlite3.DatabaseError):
        return _diagnostic('database_corrupt', stage, '数据库无法正常读取',
            '数据库文件可能损坏或不是受支持的数据库。',
            '停止对该库写入，先保留副本并执行数据库完整性检查；确认备份后再恢复。',
            '检查数据库完整性', False)
    if task_type == 'csv_import' or stage == 'csv_import':
        if isinstance(error, UnicodeError) or '编码' in text:
            return _diagnostic('csv_encoding_invalid', stage, 'CSV 文件编码无法读取',
                '当前文件编码与选择的格式不一致，数据尚未写入数据库。',
                '返回文件检查，选择 UTF-8 或 GB18030 并重新预检。',
                '检查 CSV 编码', True)
        if 'gzip' in text or '压缩流' in text:
            return _diagnostic('csv_compression_invalid', stage, 'CSV.GZ 压缩文件无效',
                '压缩文件损坏、包含额外尾部数据，或不是单个完整 GZIP 数据流。',
                '重新生成 CSV.GZ 文件，确认压缩包完整后再上传。',
                '重新选择文件', False)
        if '12mb' in text or '64mb' in text or '超过' in text and 'MB' in text:
            return _diagnostic('csv_size_limit', stage, 'CSV 文件超过导入上限',
                '当前文件或解压后的 CSV 超过单次导入容量限制。',
                '按时间范围拆分文件后分别导入；不要通过修改客户端限制绕过服务端上限。',
                '拆分文件', True)
        return _diagnostic('csv_content_invalid', stage, 'CSV 数据未通过导入检查',
            '文件表头、时间列、标签或参量数据未通过校验，数据库没有提交本次导入。',
            '返回文件检查，确认时间/标签列、参量映射、数值格式和用途后重新预检。',
            '返回文件检查', False)

    if name == 'AlgorithmTimeoutError' or '超过' in text and '秒' in text and '算法' in text:
        return _diagnostic('algorithm_timeout', stage, '算法执行超时',
            '算法运行超过当前配置的时间上限，工作进程已终止。',
            '缩小时间范围或参量数量，检查算法参数后重新提交；不要把未完成任务当作有效结果。',
            '调整数据范围', True)
    if name == 'MemoryError' or 'memory limit' in text or '内存上限' in text or '内存不足' in text:
        return _diagnostic('memory_limit', stage, '可用内存不足',
            '本次数据和算法参数超过当前可用内存预算。',
            '减少并行任务、参量数量或时间范围；保留原始数据后再重新运行。',
            '调整数据范围', True)
    if name == 'AlgorithmWorkerError' or '算法子进程' in text or 'worker' in text:
        return _diagnostic('algorithm_worker_failed', stage, '算法工作进程未能完成',
            '算法进程未返回完整、可验证的结果。',
            '先检查算法依赖和参数；如果重复失败，导出任务编号并检查服务端日志。',
            '检查算法配置', True)

    if stage == 'worker_start':
        return _diagnostic('worker_start_failed', stage, '任务未能启动',
            '系统未能创建训练工作线程，算法尚未开始执行。',
            '检查 AAS 服务进程和系统资源；确认服务正常后重新提交任务。',
            '返回训练设置', True)
    if stage == 'model_validation':
        return _diagnostic('model_artifact_invalid', stage, '模型文件未通过保存校验',
            '模型文件没有形成可重新加载的完整产物。',
            '检查模型目录写入权限和剩余空间；确认后重新训练，不要手工发布不完整模型。',
            '返回训练设置', True)
    if stage == 'provenance_capture':
        return _diagnostic('provenance_failed', stage, '实验来源证据未能核验',
            '系统无法确认本次模型输入和数据来源的一致性。',
            '重新预览数据，确认运行来源、时间范围和参量未发生变化，再重新提交。',
            '重新检查数据', False)
    if stage == 'report_archive':
        return _diagnostic('archive_failed', stage, '计算已完成，但实验报告未归档',
            '模型计算结果已产生，但报告文件未能完整保存；请勿将其视为已归档实验。',
            '检查 outputs 目录权限和磁盘空间；保留模型产物，修复环境后重新执行可审计的运行。',
            '检查报告存储', True)
    if stage in ('evaluation_validation', 'score_only_validation', 'preflight'):
        return _diagnostic('selection_invalid', stage, '数据或模型选择未通过检查',
            '当前数据选择与模型的参量、时间对齐或标签要求不匹配。',
            '返回数据选择，重新预览有效帧、参量和标签覆盖，再运行。',
            '返回数据选择', False)
    if stage in ('evaluation_execution', 'score_only_execution', 'algorithm_execution'):
        return _diagnostic('algorithm_failed', stage, '算法未能完成本次计算',
            '算法执行期间未产生可用结果。',
            '检查算法参数、模型文件和所选数据范围；修改后重新提交。',
            '返回运行设置', True)
    return _diagnostic('task_failed', stage, '任务未能完成',
        '系统未能完成当前任务。原始数据和已发布模型不会因本提示自动删除。',
        '检查当前数据选择和系统存储状态；重新预检后再提交。',
        '返回运行设置', False)


def archive_attention() -> dict[str, Any]:
    """Diagnostic for a computed result whose report publication did not finish."""
    return _diagnostic('archive_failed', 'report_archive', '计算已完成，但实验报告未归档',
        '模型计算结果已产生，但报告文件未能完整保存；请勿将其视为已归档实验。',
        '检查 outputs 目录权限和磁盘空间；保留模型产物，修复环境后重新执行可审计的运行。',
        '检查报告存储', True)


def server_restart_diagnostic(stage: str, task_type: str = 'training') -> dict[str, Any]:
    workflow = {
        'training': ('训练', '重新提交训练'),
        'evaluation': ('评估', '重新提交评估'),
        'score_only': ('仅推理', '重新提交仅推理'),
        'batch_import': ('批量导入', '重新预检后提交批量导入'),
        'csv_import': ('CSV 导入', '重新选择文件并提交导入'),
        'preprocess': ('数据预处理', '重新选择数据并提交预处理'),
    }
    label, recovery_label = workflow.get(task_type, workflow['training'])
    return _diagnostic('server_restart', stage, '服务重启导致任务中断',
        '服务重启后，原任务不会自动恢复；此前的中间计算不作为有效结果。',
        f'重新进入{label}页面，核对数据和参数后创建一次新任务；旧任务记录会保留。',
        recovery_label, True)


def sanitize_diagnostic(value: Any) -> dict[str, Any] | None:
    """Copy only the documented bounded diagnostic fields from persisted JSON."""
    if not isinstance(value, dict):
        return None
    fields = ('code', 'stage', 'stage_label', 'title', 'message',
              'recovery_action', 'recovery_label', 'retryable')
    result = {}
    for key in fields:
        item = value.get(key)
        if key == 'retryable':
            result[key] = item is True
        elif isinstance(item, str):
            result[key] = item[:300]
    if not isinstance(result.get('code'), str) or not isinstance(result.get('message'), str):
        return None
    if result.get('stage') not in STAGE_LABELS:
        result['stage'] = 'unknown'
        result['stage_label'] = STAGE_LABELS['unknown']
    return result
