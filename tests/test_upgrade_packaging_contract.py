"""Code-only candidate ZIP is reproducible and does not carry runtime secrets."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT=Path(__file__).resolve().parents[1]


def test_packer_uses_explicit_main_and_safe_filters():
    source=(ROOT/'_pack_code.py').read_text('utf-8')
    for required in ("if __name__ == '__main__':",'--output',
                     "'.superpowers'", "'models'", "'.key'", 'UPGRADE_SHA256_MANIFEST.json'):
        assert required in source


def test_pack_source_has_verified_manifest_and_no_live_state(tmp_path):
    destination=tmp_path/'candidate.zip'
    subprocess.run([sys.executable,str(ROOT/'_pack_code.py'),'--output',str(destination)],
                   cwd=ROOT,check=True,timeout=30,capture_output=True,text=True)
    assert destination.is_file() and destination.stat().st_size>0
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        files=set(archive.namelist())
        assert 'AAS/docs/UPGRADE_STATUS_AND_NEXT_STEPS_20261105.md' in files
        assert 'AAS/LOCAL_TEST/Run-UpgradeTests.ps1' in files
        assert 'AAS/scripts/server/v2_csv_workspace.py' in files
        assert 'AAS/scripts/server/v2_model_bundle.py' in files
        # Fresh-install wizard and CSV column-mapping flows depend on these.
        assert 'AAS/datasets/templates/AAS_column_mapping_template.csv' in files
        assert 'AAS/datasets/templates/telemetry_template.csv' in files
        assert 'AAS/datasets/README.md' in files
        assert 'AAS/LOCAL_TEST/Verify-UpgradeSourceZip.py' in files
        assert 'AAS/UPGRADE_SHA256_MANIFEST.json' in files
        assert not any('/.git/' in f or '/.venv/' in f or '/.superpowers/' in f for f in files)
        assert not any(f.startswith(('AAS/models/','AAS/outputs/')) for f in files)
        assert not any(f.startswith('AAS/datasets/') and not f.startswith('AAS/datasets/templates/')
                       and f!='AAS/datasets/README.md' for f in files)
        assert not any(f.endswith(('.key','.pem','.sqlite','.db','.so','.pyc')) for f in files)
        manifest=json.loads(archive.read('AAS/UPGRADE_SHA256_MANIFEST.json'))
        assert manifest['format']=='aas-v2-source-manifest-sha256-v1'
        entries=manifest['files']
        assert len(entries)>100 and set(entries)==files-{'AAS/UPGRADE_SHA256_MANIFEST.json'}
        for name,expected in entries.items():
            assert hashlib.sha256(archive.read(name)).hexdigest()==expected


def test_source_package_verifier_rejects_tampered_archive(tmp_path):
    destination=tmp_path/'candidate.zip'
    subprocess.run([sys.executable,str(ROOT/'_pack_code.py'),'--output',str(destination)],
                   cwd=ROOT,check=True,timeout=30,capture_output=True,text=True)
    verifier=ROOT/'LOCAL_TEST'/'Verify-UpgradeSourceZip.py'
    initial=subprocess.run([sys.executable,str(verifier),str(destination)],
                           capture_output=True,text=True)
    assert initial.returncode==0,initial.stderr
    with zipfile.ZipFile(destination,'a') as archive:
        archive.writestr('AAS/evil.txt', 'corrupted-or-added')
    tampered=subprocess.run([sys.executable,str(verifier),str(destination)],
                            capture_output=True,text=True)
    assert tampered.returncode!=0
    assert 'manifest' in tampered.stderr.lower()
