# caddy/Dockerfile —— 自定义 Caddy 镜像（仅多编一个 DNS 插件）
#
# 为什么需要自定义：官方 `caddy:2` 镜像不含任何第三方 DNS 提供商模块，而
# DuckDNS 的 DNS-01 验证必须靠 `caddy-dns/duckdns`。用官方 builder 镜像 + xcaddy
# 在**构建期**把模块编进 caddy 二进制，运行期就不需要任何额外体积或工具链。
#
# 为什么是 DNS-01，而不是 HTTP-01 / TLS-ALPN-01：
#   服务器在阿里云境内节点（cn-hangzhou）。域名未做 ICP 备案时，80/443 上的
#   域名访问会被拦截，而 ACME 的两种端口验证恰恰都要求在 80/443 上应答 ——
#   也就是说「未备案」与「端口验证」互斥。DNS-01 只在 DNS 里写一条 TXT 记录，
#   与对外端口完全无关，是这种场景下唯一可靠的路子；证书签下来后 HTTPS 走
#   8443（非 80/443 端口不受备案拦截影响）。
FROM caddy:2-builder AS builder

# 国内构建走 goproxy.cn：proxy.golang.org 在国内基本不可用，会卡在下载依赖
ENV GOPROXY=https://goproxy.cn,direct

RUN xcaddy build --with github.com/caddy-dns/duckdns

FROM caddy:2
# 只替换二进制：基础镜像自带的入口脚本、非 root 用户与 /data 目录结构全部保留
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
