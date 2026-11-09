"""Same-installation authenticated AAS V2 model transfer bundles.

Serialized detectors can execute code while loading. Never accept an arbitrary
pickle, joblib, py or unsigned ZIP: only this installation's HMAC-authenticated
exports may reach the existing saved-model loader.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any


MAX_BUNDLE_BYTES = 75_000_000
MAX_UNCOMPRESSED_BYTES = 64_000_000
_MODEL_NAME = re.compile(r'model_[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z')
_MODEL_FILES = {'config.json', 'metrics.json', 'model.pkl', 'model.pt'}
_STRUCTURAL = {'manifest.json', 'signature.txt'}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value,sort_keys=True,ensure_ascii=False,
                      separators=(',',':'),allow_nan=False).encode('utf-8')


def _key(root: Path, *, generate: bool) -> bytes:
    path = Path(root).resolve() / 'configs' / 'aas_model_bundle.key'
    if generate:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd,'wb') as handle:
                handle.write(os.urandom(32))
                handle.flush();os.fsync(handle.fileno())
    if not path.is_file() or path.is_symlink():
        raise ValueError('此安装实例尚无模型包认证密钥；仅接受本机导出的受信任模型包')
    secret=path.read_bytes()
    if len(secret)!=32:
        raise ValueError('模型包认证密钥异常')
    return secret


def _artifact_dir(root: Path, reference: str) -> Path:
    root=Path(root).resolve()
    model_root=(root/'models').resolve()
    if not isinstance(reference,str) or not reference:
        raise ValueError('模型目录未配置')
    candidate=Path(reference)
    raw_path=candidate if candidate.is_absolute() else root/candidate
    if raw_path.is_symlink():
        raise ValueError('拒绝链接式模型目录')
    path=raw_path.resolve()
    if path.parent!=model_root or not _MODEL_NAME.fullmatch(path.name) or not path.is_dir():
        raise ValueError('模型目录必须位于安装实例的 models/model_* 下')
    return path


def export_model_bundle(root: Path, model: dict) -> bytes:
    """Sign exactly the 3 local AAS files that already back a published model."""
    path=_artifact_dir(root,model.get('file_path'))
    names={x.name for x in path.iterdir()}
    if names not in ({'config.json','metrics.json','model.pkl'},
                     {'config.json','metrics.json','model.pt'}):
        raise ValueError('模型文件结构不符合受信任的 AAS 三件套')
    files={}
    for name in sorted(names):
        item=path/name
        if item.is_symlink() or not item.is_file() or item.stat().st_size>MAX_UNCOMPRESSED_BYTES:
            raise ValueError('模型包含链接、非文件或超大产物')
        files[name]=item.read_bytes()
    if sum(len(x) for x in files.values())>MAX_UNCOMPRESSED_BYTES:
        raise ValueError('模型包超过 64 MB 容量上限')
    try:
        cfg=json.loads(files['config.json'].decode('utf-8'))
        metrics=json.loads(files['metrics.json'].decode('utf-8'))
    except (ValueError,UnicodeError) as exc:
        raise ValueError('模型配置或指标不是 UTF-8 JSON') from exc
    if (not isinstance(cfg,dict) or not isinstance(metrics,dict)
            or cfg.get('algorithm')!=model.get('algorithm')
            or cfg.get('experiment_id')!=model.get('experiment_id')):
        raise ValueError('模型配置与注册表信息不一致')
    secret=_key(root,generate=True)
    manifest={'format':'aas-v2-same-install-model-bundle-v1',
              'original_experiment_id':cfg['experiment_id'],
              'algorithm':cfg['algorithm'],
              'model_name':model.get('model_name') or cfg['experiment_id'],
              'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}
    signed=_json_bytes(manifest)
    mac=hmac.new(secret,signed,hashlib.sha256).hexdigest().encode('ascii')
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for name,raw in files.items():bundle.writestr(name,raw)
        bundle.writestr('manifest.json',signed)
        bundle.writestr('signature.txt',mac)
    value=result.getvalue()
    if len(value)>MAX_BUNDLE_BYTES:
        raise ValueError('压缩后的模型包超过上传限制')
    return value


def verify_model_bundle(root: Path, raw: bytes) -> dict[str, Any]:
    """Strict integrity/authenticity check without touching any deserializer."""
    if not isinstance(raw,bytes) or not 0 < len(raw) <= MAX_BUNDLE_BYTES:
        raise ValueError('只能上传 75 MB 以内的 AAS 认证模型 ZIP 包')
    secret=_key(root,generate=False)
    try:
        with zipfile.ZipFile(io.BytesIO(raw),'r') as archive:
            infos=archive.infolist()
            names=[x.filename for x in infos]
            if (len(names)!=len(set(names)) or set(names) not in (
                 {'config.json','metrics.json','model.pkl',* _STRUCTURAL},
                 {'config.json','metrics.json','model.pt',* _STRUCTURAL})):
                raise ValueError('模型包存在多余、缺失、重复或路径穿越条目')
            if (any(info.is_dir() or stat.S_ISLNK(info.external_attr>>16) or
                    info.file_size<0 or info.file_size>MAX_UNCOMPRESSED_BYTES for info in infos)
                    or sum(info.file_size for info in infos)>MAX_UNCOMPRESSED_BYTES+100_000):
                raise ValueError('模型包包含链接、目录、压缩炸弹或过大文件')
            if (archive.getinfo('manifest.json').file_size>32_000
                    or archive.getinfo('signature.txt').file_size>128):
                raise ValueError('模型包认证清单超长')
            values={name:archive.read(name) for name in names}
    except (zipfile.BadZipFile,EOFError,OSError,RuntimeError) as exc:
        raise ValueError('模型包 ZIP 已损坏或并非受信任格式') from exc
    expected=hmac.new(secret,values['manifest.json'],hashlib.sha256).hexdigest().encode('ascii')
    if not hmac.compare_digest(values['signature.txt'],expected):
        raise ValueError('模型包签名校验失败；仅支持此安装实例签发的文件')
    try:
        manifest=json.loads(values['manifest.json'].decode('utf-8'))
        config=json.loads(values['config.json'].decode('utf-8'))
        metrics=json.loads(values['metrics.json'].decode('utf-8'))
    except (ValueError,UnicodeError) as exc:
        raise ValueError('签名模型包的 JSON 清单无效') from exc
    files={key:value for key,value in values.items() if key in _MODEL_FILES}
    digests={name:hashlib.sha256(value).hexdigest() for name,value in files.items()}
    if not isinstance(manifest,dict) or set(manifest)!={'format','original_experiment_id','algorithm','model_name','files'} or (
        manifest['format']!='aas-v2-same-install-model-bundle-v1' or digests!=manifest['files']):
        raise ValueError('模型包文件摘要或版本校验失败')
    parameters=config.get('config') if isinstance(config,dict) else None
    contract=parameters.get('data_contract') if isinstance(parameters,dict) else None
    if (not isinstance(config,dict) or not isinstance(metrics,dict) or
            config.get('algorithm')!=manifest['algorithm'] or
            config.get('experiment_id')!=manifest['original_experiment_id'] or
            not isinstance(contract,dict) or contract.get('version')!=7 or
            not contract.get('v2_run_selection') or not contract.get('v2_run_manifest')):
        raise ValueError('仅允许签发过的 V2 运行级模型及其完整训练契约')
    return {'files':files,'manifest':manifest,'config':config,'metrics':metrics}


def install_model_bundle(main, raw: bytes, name: str) -> dict:
    """Validate HMAC before saved-model restoration or registry writes."""
    if not isinstance(name,str) or not 1<=len(name.strip())<=100 or any(ord(x)<32 for x in name):
        raise ValueError('请输入合法的模型名称')
    verified=verify_model_bundle(main.ROOT,raw)
    root=Path(main.ROOT).resolve()
    models=root/'models';models.mkdir(parents=True,exist_ok=True)
    experiment_id='import_'+uuid.uuid4().hex
    directory=models/('model_'+experiment_id)
    staging=Path(tempfile.mkdtemp(prefix='.model-import-',dir=models))
    committed=False
    try:
        for filename,body in verified['files'].items():
            with (staging/filename).open('wb') as out:
                out.write(body);out.flush();os.fsync(out.fileno())
        config=dict(verified['config']);config['experiment_id']=experiment_id
        (staging/'config.json').write_bytes(_json_bytes(config))
        # Now and only now can the installed platform's native saved-model
        # loader run; the serialized artifact was authenticated beforehand.
        from scripts.core.model_manager import validate_saved_model
        validate_saved_model(staging,config['algorithm'],experiment_id=experiment_id)
        if directory.exists() or directory.is_symlink():
            raise FileExistsError('模型目录编号冲突，拒绝覆盖')
        staging.rename(directory)
        committed=True
        ref=directory.relative_to(root).as_posix()
        main.create_model(experiment_id,name.strip(),'本机已认证模型包导入',
            config['algorithm'],config.get('dataset') or 'aas_telemetry_v2.sqlite',
            'v2_channels',ref,verified['metrics'],config.get('config') or {})
        model=main.get_model_by_experiment_id(experiment_id)
        return {'status':'imported','experiment_id':experiment_id,'id':model.get('id') if model else None,
                'model_name':name.strip(),'algorithm':config['algorithm']}
    except Exception:
        if committed and directory.is_dir():
            shutil.rmtree(directory)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
