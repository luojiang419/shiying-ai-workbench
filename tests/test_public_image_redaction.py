import importlib.util
import json
from pathlib import Path


def test_redaction_removes_private_backups_and_uploads_but_keeps_runtime_and_models(tmp_path):
    source=Path(__file__).resolve().parents[1]/'deployment/public_image.py'
    spec=importlib.util.spec_from_file_location('public_image_test',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    root=tmp_path/'node'
    for name in ['app','config','deployment','outputs/runtime','outputs/clone','logs','models','rollback']:
        (root/name).mkdir(parents=True,exist_ok=True)
    (root/'app/server.py').write_text('# application')
    (root/'deployment/models.json').write_text('{"assets":{}}')
    (root/'config/assistant-providers.json').write_text(json.dumps([{'encryptedKey':'private-ciphertext-value'}]))
    (root/'config/assistant-node.key').write_text('private-master-key-value')
    (root/'rollback/old.json').write_text('private-ciphertext-value')
    (root/'outputs/clone/source.mp4').write_bytes(b'private video')
    (root/'outputs/runtime/gpu-runtime.json').write_text('{"ready":true}')
    (root/'models/preserved-model.safetensors').write_bytes(b'model')
    result=module.convert(root)
    assert result['old_secret_matches']==0
    assert not (root/'rollback').exists()
    assert not (root/'outputs/clone').exists()
    assert json.loads((root/'config/assistant-providers.json').read_text())==[]
    assert (root/'outputs/runtime/gpu-runtime.json').is_file()
    assert (root/'models/preserved-model.safetensors').read_bytes()==b'model'
    assert not (root/'config/assistant-node.key').exists()
