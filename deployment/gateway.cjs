const http = require('node:http');
const fs = require('node:fs');
const {createRequire} = require('node:module');
const requirePanel = createRequire('/root/zealman-app/package.json');
const {createProxyMiddleware} = requirePanel('http-proxy-middleware');
const {readPlatformUrls, publicHost} = require('./platform-origin.cjs');
const configPath = '/root/minimax-node/config/gateway.json';
let platformUrls = readPlatformUrls();
setInterval(() => { platformUrls = readPlatformUrls(); }, 60000).unref();
// 保留本次请求的公网 Host，让后端可将其与浏览器 Origin 正确核对。
// 改写为内部 127.0.0.1:7890 会使镜像部署到任意域名后都误报来源不匹配。
const proxy = createProxyMiddleware({target:'http://127.0.0.1:7890',changeOrigin:false,ws:true,
  proxyTimeout:3600000,timeout:3600000,xfwd:false,
  on:{proxyReq(p,req){p.removeHeader('x-forwarded-for');p.removeHeader('forwarded');
        const host=publicHost(req.headers.host,platformUrls);if(host)p.setHeader('host',host);},
      error(e,req,res){if(res.writeHead){res.writeHead(502,{'Content-Type':'application/json'});res.end('{"error":"node_unavailable"}');}else res.destroy();}}});
const normalize = s => (s || '').replace(/^::ffff:/,'');
function allowed(req){
  const config=JSON.parse(fs.readFileSync(configPath,'utf8'));
  if(config.restrictNetwork === false) return true;
  const peer=normalize(req.socket.remoteAddress);
  const forwarded=String(req.headers['x-forwarded-for']||'').split(',').map(x=>normalize(x.trim())).filter(Boolean);
  const address=forwarded.at(-2);
  if(config.diagnostic) console.log(JSON.stringify({peer,forwarded,real:req.headers['x-real-ip'],path:req.url}));
  return config.allowed.includes(address) && config.trustedPeers.includes(peer) && config.trustedProxies.includes(forwarded.at(-1));
}
const server=http.createServer((req,res)=>{
  if(!allowed(req)){res.writeHead(403,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end('{"error":"network_not_allowed"}');return;}
  proxy(req,res);
});
server.on('upgrade',(req,socket,head)=>{if(!allowed(req)){socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');return;}proxy.upgrade(req,socket,head);});
server.requestTimeout=3600000;server.headersTimeout=60000;
const port=Number(process.env.MINIMAX_GATEWAY_PORT||6008);
server.listen(port,'0.0.0.0',()=>console.log('MiniMax HTTPS gateway upstream ready on :'+port));
