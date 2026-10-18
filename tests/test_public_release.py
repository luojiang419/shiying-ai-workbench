import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.assistant_secrets import open_node_key
from app.public_release import configure_flash, enabled, install_routes, owner_code


def release_root(tmp_path):
    (tmp_path/'config').mkdir()
    (tmp_path/'config/public-release.json').write_text(json.dumps({'edition':'video-clone'}))
    return tmp_path


def test_owner_code_survives_restart_but_rotates_for_new_instance(tmp_path):
    assert not enabled(tmp_path)
    root=release_root(tmp_path)
    first=owner_code(root,'instance-a')
    assert owner_code(root,'instance-a')==first
    assert owner_code(root,'instance-b')!=first
    assert enabled(root)


def test_public_depth_base_is_blocked_before_dependency_download(tmp_path):
    from app.clone_depth_runtime import prepare_depth
    root=release_root(tmp_path)
    with pytest.raises(ValueError,match='非商业授权'):
        prepare_depth(root,model='base')


def test_flash_verified_before_persisting_and_no_plaintext_key(tmp_path):
    root=release_root(tmp_path)
    key='sk-public-test-only-12345'
    calls=[]
    configure_flash(root,key,lambda provider,value:calls.append((provider['defaultModel'],value)))
    assert calls==[('deepseek-flash',key)]
    text=(root/'config/assistant-providers.json').read_text(encoding='utf-8')
    provider=json.loads(text)[0]
    assert key not in text and provider['defaultModel']=='deepseek-flash'
    assert open_node_key(provider['encryptedKey'],root)==key
    before=text
    with pytest.raises(ValueError,match='验证失败'):
        configure_flash(root,'sk-invalid-key-value',lambda *_:(_ for _ in ()).throw(RuntimeError('private detail')))
    assert (root/'config/assistant-providers.json').read_text(encoding='utf-8')==before


def test_quick_flash_setup_preserves_other_owner_providers(tmp_path):
    root=release_root(tmp_path)
    other={'id':'another-provider','encryptedKey':'existing-encrypted-value','enabled':False}
    (root/'config/assistant-providers.json').write_text(json.dumps([other]))
    configure_flash(root,'sk-public-test-only-12345',lambda *_:None)
    providers=json.loads((root/'config/assistant-providers.json').read_text(encoding='utf-8'))
    assert providers[0]['id']=='deepseek-flash'
    assert providers[1]==other


def test_setup_needs_owner_code_and_same_origin_without_disclosing_credentials(tmp_path,monkeypatch):
    root=release_root(tmp_path)
    monkeypatch.setattr('app.public_release.instance_id',lambda:'test-instance')
    app=FastAPI()
    install_routes(app,root=root,verify=lambda *_:None)
    key='sk-public-test-only-12345'
    with TestClient(app,base_url='https://instance.test:8443',client=('127.0.0.1',41000)) as client:
        assert client.post('/api/public-release/assistant',json={'apiKey':key}).status_code==401
        code=owner_code(root)
        assert code not in client.get('/api/public-release').text
        headers={'X-Shiying-Admin-Code':code,'Origin':'https://instance.test:8443'}
        result=client.post('/api/public-release/assistant',headers=headers,json={'apiKey':key})
        assert result.status_code==200 and result.json()['model']=='deepseek-flash'
        assert key not in result.text and code not in result.text
        assert client.get('/api/public-release').json()['assistantConfigured']
        assert client.post('/api/public-release/assistant',headers={**headers,'Origin':'https://evil.test'},json={'apiKey':key}).status_code==403
