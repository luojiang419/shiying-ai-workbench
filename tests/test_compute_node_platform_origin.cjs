const test = require('node:test');
const assert = require('node:assert/strict');
const {publicHost} = require('../deployment/platform-origin.cjs');

for (const domain of ['first-instance.example','next-image-instance.example']) {
  test(`恢复当前平台入口端口 ${domain}`, () => {
    assert.equal(publicHost(domain,[`https://${domain}:8443`]),`${domain}:8443`);
    assert.equal(publicHost(`${domain}:8443`,[`https://${domain}:8443`]),`${domain}:8443`);
  });
}
test('不替换其他域名、不信任旧域名或错误端口', () => {
  assert.equal(publicHost('new.example',['https://old.example:8443']),'new.example');
  assert.equal(publicHost('new.example:9000',['https://new.example:8443']),'new.example:9000');
  assert.equal(publicHost('evil.example',['https://new.example:8443']),'evil.example');
});
test('IPv6 公网入口正确添加方括号端口', () => {
  assert.equal(publicHost('[2001:db8::1]',['https://[2001:db8::1]:8443']),'[2001:db8::1]:8443');
});
test('缺失和损坏平台地址保留原 Host，拒绝 URL 凭据混入', () => {
  assert.equal(publicHost('new.example',[]),'new.example');
  assert.equal(publicHost('new.example',['bad','ftp://new.example:8443','https://user:pass@new.example:8443']),'new.example');
  assert.equal(publicHost('user@new.example',['https://new.example:8443']),'user@new.example');
});
