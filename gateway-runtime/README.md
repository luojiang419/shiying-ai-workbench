# 网关依赖

应用系统镜像已安装 `http-proxy-middleware@3.0.5`。源码方式部署到已准备好的基础环境时执行：

```bash
npm ci --prefix /root/minimax-node/gateway-runtime --omit=dev --ignore-scripts
```

依赖版本由 package-lock.json 固定。node_modules 留在系统镜像中，不进入源码仓库。
