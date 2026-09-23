# 云服务器部署实施计划（Docker Compose 全栈上线）

> **执行方式：** 两条路——① AI 远程执行（提供 ssh 信息后逐任务推进）；② 你按本计划自跑，每步都有「命令 + 预期输出」，卡住把输出贴回来即可。
> 出错先查文末「常见故障」再继续。

**Goal:** 把 rag_qa_project 全栈（MySQL + Redis + 后端 + 前端 nginx）部署到一台公网 Linux 服务器，得到一个能写进简历的「线上地址」，并跑通注册 / 提问 / 流式回答 / 会话管理。

**Architecture:** 服务器上 `docker compose` 起四个服务（镜像与编排已就绪：`docker-compose.yml` / `Dockerfile` / `frontend/Dockerfile`）。代码与 `.env` 由本机 scp 上传（**不走服务器 git clone**——规避国内服务器到 GitHub 的网络问题）；知识库直接上传本机 `data/chroma_db`（**免二次 ingest**，省 API 额度）；对外只暴露 8080，前端 nginx 反代 `/api`（SSE 端点已关缓冲）。

**Tech Stack:** Ubuntu 22.04/24.04 + Docker Engine + compose v2（≥2.20）；本机 Windows PowerShell（git archive / scp / ssh）。

---

## 前置（需要提供 / 确认）

| 项 | 要求 |
|---|---|
| 服务器 | 阿里云轻量 / ECS 等，**≥2C4G**（2C2G 必须先加 swap，见 Task 1b） |
| 系统 | Ubuntu 22.04 / 24.04（其他发行版把 apt 换成对应命令） |
| 磁盘 | ≥10G 可用（镜像约 2.5G + 数据） |
| 网络 | 安全组 / 防火墙放行 **22** 与 **8080** |
| 登录 | 公网 IP + 用户名 + 密码或密钥 |

---

## Task 1：连通与资源检查（服务器）

**Step 1：登录**

```bash
ssh <user>@<公网IP>
```

**Step 2：资源检查**

```bash
free -h     # Mem 行：总内存 ≥ 3.5Gi 直接继续；约 2.0Gi 先做 Task 1b
df -h /     # 可用 ≥ 10G
cat /etc/os-release | head -2
```

预期：`Ubuntu 22.04` 或 `24.04`；内存/磁盘不紧张。

**Task 1b（仅 2G 内存机器）：加 2G swap** —— 否则构建可能被 OOM kill

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h    # 预期：Swap 行出现 2.0Gi
```

## Task 2：安装 Docker（阿里云镜像源，Ubuntu）

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
docker compose version      # 预期：Docker Compose version v2.2x
```

（海外服务器可改用官方脚本：`curl -fsSL https://get.docker.com | sudo sh`）

## Task 3：上传代码与数据（本机 Windows 执行）

**Step 1：打包代码**（git archive 只含跟踪文件，天然排除 `.env` 与向量库等未跟踪大件）

```powershell
cd d:\PycharmProjects\my_langchain_demo\advanced_tutorial
git archive --format=zip -o ragqa_src.zip HEAD
```

**Step 2：上传代码包 + .env + 知识库**

```powershell
scp ragqa_src.zip <user>@<IP>:/opt/
scp rag_qa_project\.env <user>@<IP>:/opt/
scp -r rag_qa_project\data\chroma_db <user>@<IP>:/opt/data_tmp/
# 可选：要保留历史会话与上传件，追加这两条
# scp rag_qa_project\data\checkpoints.db* <user>@<IP>:/opt/data_tmp/
# scp -r rag_qa_project\data\uploads <user>@<IP>:/opt/data_tmp/
```

**Step 3：服务器端解包落位**

```bash
sudo mkdir -p /opt/ragqa && cd /opt/ragqa
sudo apt-get install -y unzip
unzip -q /opt/ragqa_src.zip
mkdir -p rag_qa_project/data
cp -r /opt/data_tmp/chroma_db rag_qa_project/data/
mv /opt/.env rag_qa_project/.env
cd rag_qa_project
ls docker-compose.yml .env data/chroma_db      # 三样都在 → 就绪
```

**Step 4（可选，国内服务器建议）：打开构建源加速** —— 编辑 `rag_qa_project/.env`，去掉注释：

```
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
NPM_REGISTRY=https://registry.npmmirror.com
```

## Task 4：构建并启动（服务器）

```bash
cd /opt/ragqa/rag_qa_project
docker compose up -d --build     # 首次约 10~25 分钟（主要耗在 pip / npm 下载）
docker compose ps                # 预期：mysql/redis 为 healthy；backend/frontend 为 Up
```

## Task 5：验收（逐条对照）

**Step 1：服务端自检**

```bash
curl -s http://127.0.0.1:8080/api/health
# 预期：{"status":"ok","kb_count":3755,"model":"qwen3.7-text-embedding"}
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
# 预期：200
```

**Step 2：公网访问（浏览器 `http://<公网IP>:8080`）**

- [ ] 页面打开、注册 / 登录成功；
- [ ] 提问有**逐字流式**回答（SSE 未被 nginx 缓冲）；
- [ ] 会话侧栏：改名 / 删除后列表**立即**生效（P4 缓存失效链路）；
- [ ] 上传一个小 .md 进知识库，随后提问能检索到（单集合 + metadata 过滤链路）。

**Step 3：持久化**

```bash
docker compose restart && docker compose ps
```

- [ ] 重启后用户与会话仍在（MySQL 卷）、缓存不报错（Redis 卷）。

## Task 6：域名 + HTTPS（DuckDNS + Caddy + DNS-01）

### 先看约束：这台机器在阿里云**境内**节点（cn-hangzhou）

| 约束 | 后果 |
|---|---|
| 域名在 80/443 上对外提供服务需 **ICP 备案** | 未备案域名走 80/443 会被拦截；备案要时间与主体资质 |
| ACME 的 HTTP-01 / TLS-ALPN-01 **都要求在 80/443 上应答** | 「未备案」与「端口验证」互斥 —— 常规的「Caddy 两行自动 HTTPS」在这台机器上必然失败 |

**结论：DNS-01 验证 + 非 80/443 端口（8443）。** DNS-01 只在 DNS 里写一条
`_acme-challenge` TXT 记录，与对外端口无关；8443 不属于备案拦截的端口范围。
这也是「境内未备案机器上拿到**绿锁**证书」的通用解法。

### 为什么用 DuckDNS 而不是 sslip.io / nip.io

`nip.io` 这类通配 DNS 虽然零配置，但**无法写入 TXT 记录** —— DNS-01 走不通，
而 HTTP-01 又受备案限制。DuckDNS 满足三件事：免费、可用 API 写 TXT、**域名进了
Public Suffix List**（因此 Let's Encrypt 愿意为 `xxx.duckdns.org` 单独签发证书 ——
这条是硬门槛，不少免费子域服务在这一点上不合格）。

### 落地步骤

```bash
# 1) 注册 duckdns.org（GitHub/Google 登录）→ 建一个子域 → 复制页面上的 token
# 2) 把子域解析到本机（API 一条命令，不用进控制台）
curl "https://www.duckdns.org/update?domains=<子域>&token=<token>&ip=47.114.103.158"

# 3) 在 rag_qa_project/.env 追加两行
#    RAGQA_DOMAIN=<子域>.duckdns.org
#    DUCKDNS_TOKEN=<token>

# 4) 云服务器安全组放行 8443/TCP（**不需要** 80/443）
# 5) 启动 Caddy（叠加层，只新增一个服务，8080 不受影响）
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d caddy
docker compose -f docker-compose.yml -f docker-compose.https.yml logs caddy | tail -30

# 6) 验收
curl -sI https://<子域>.duckdns.org:8443/api/health        # 200 + 有效证书
curl -s https://<子域>.duckdns.org:8443/api/health         # {"status":"ok",...}
echo | openssl s_client -connect <子域>.duckdns.org:8443 -servername <子域>.duckdns.org 2>/dev/null | openssl x509 -noout -issuer -dates
```

### 实现要点

- **Caddy 必须自定义镜像**：官方 `caddy:2` 不含任何第三方 DNS 模块，
  `caddy-dns/duckdns` 要用 `xcaddy` 在构建期编入（见 `caddy/Dockerfile`，
  构建期设 `GOPROXY=https://goproxy.cn`，否则国内拉不到 Go 模块）。
- **证书必须持久化**：`caddy_data` 卷存证书与 ACME 账户。删了它就会重新签发，
  反复几次会撞上 Let's Encrypt 的速率限制（每周同名证书 5 张）。
- **与 8080 并存**：8080 继续用 IP 直连（健康检查、调试方便），8443 是给人看的正式地址。
  两者反代到同一个 `frontend:80`，SSE 关缓冲在 nginx 那层，不受影响。
- **换自有域名时**：只需改 `.env` 的 `RAGQA_DOMAIN`、`Caddyfile` 里 `dns duckdns`
  换成对应 DNS 提供商模块、端口改 443 并放行安全组 —— 证书与反代逻辑不用动。

### 实施记录（2026-09-23，含四个坑）

**结果**：`https://ragqa753.duckdns.org:8443` 上线 —— 证书 issuer `Let's Encrypt`（90 天，自动续期）、
`/api/health` 返回 200、浏览器 `isSecureContext=true`、HTTPS 下 TTFT p50 **1.48s**
（首事件 0.34s，与 HTTP 的 0.33s 一致 → 反向代理层未引入任何缓冲）。

踩到的坑按「下次最先撞上」排序：

1. **Caddyfile 必须挂进容器**（`docker-compose.https.yml` 的 `volumes`）。
   第一次忘了挂，Caddy 静默退回默认配置：只在 `:80` 上返回 hello，
   日志里是 `server is listening only on the HTTP port, so no automatic HTTPS will be applied`，
   **连 ACME 都不会尝试**。这与「DNS-01 配错」的症状完全不同 —— 后者会明确报 challenge 失败。
2. **构建前先腾内存**。这台是 `ecs.e-c1m1.large`（经济型，1.7 GiB），业务容器占着 ~1.2 GiB，
   Go 链接器因缺内存反复重读输入：实测 **15 秒读 1.1 GB 磁盘、而 CPU 只累计了 1 分 42 秒**，
   单是 link 就卡了 18 分钟。`docker compose stop` 腾出内存后，编译+链接几分钟内跑完。
   > 排查手法记录：`ps -o etime,time` 对比进出时间，再看 `/proc/<pid>/io` 两次采样的
   > `read_bytes` 增量 —— 低 CPU + 高磁盘读 = 内存抖动的典型特征。
3. **Docker Hub 拉取要走国内镜像**：`caddy:2-builder` 从官方源 12 分钟只下了 117/184 MB；
   换成 `docker.m.daocloud.io/library/...` 后 1 分钟拉完，再 `docker tag` 回官方名 ——
   这样 `Dockerfile` 里的 `FROM caddy:2-builder` 不必改，构建层缓存也照样能命中。
4. **别被 curl 的报错误导**：从 Windows 客户端（Schannel）访问 8443，握手会报
   `Recv failure: Connection was reset` / `SEC_E_INTERNAL_ERROR`，看着像被墙或被拦截；
   而**同一网络下 Python(OpenSSL) 与 Chromium(BoringSSL) 握手完全正常**。
   判断「服务到底通不通」要用浏览器或 OpenSSL 客户端，别拿 Schannel 的 curl 下结论 ——
   我为此白排查了一轮「是不是未备案域名被 SNI 拦截」。

---

## 常见故障

| 症状 | 处理 |
|---|---|
| 构建里 npm / pip 下载极慢或超时 | Task 3 Step 4 的换源未打开；镜像拉取慢可再配 Docker daemon 加速 |
| 构建中途进程被杀（Killed） | 内存不足：补 Task 1b 的 swap 后重试 |
| 浏览器 502 Bad Gateway | backend 未就绪：`docker compose logs backend --tail 50`，等健康检查通过 |
| health 里 kb_count=0 | chroma 没落位：确认 `data/chroma_db` 在项目目录下；或重建：`docker compose run --rm backend python ingest.py` |
| SSE 不逐字、一把全出 | 确认访问的是 8080（nginx），且路径命中 `/api/chat/stream`（nginx 已对该路径关缓冲） |
| `docker compose` 命令不存在 | Task 2 漏装 `docker-compose-plugin` |
| `caddy logs` 报 `DNS problem: NXDOMAIN` / `timeout` | `_acme-challenge` TXT 未生效：核对 `DUCKDNS_TOKEN` 与子域是否已解析到本机；DuckDNS 传播慢时 Caddy 会自动重试，一般 1–2 分钟自愈 |
| 服务器内 `curl -k https://127.0.0.1:8443` 正常、外部连不上 | 安全组没放行 **8443**（Task 6 最容易漏的一条） |
| 证书签不出且日志提到 `rate limit` | 反复删除 `caddy_data` 卷导致重复签发。停手等一周，或换一个子域 |
| 想换成自有域名 | 改 `.env` 的 `RAGQA_DOMAIN`；`caddy-dns/duckdns` 换成对应提供商的模块；端口改 443 并放行安全组 |
| `caddy logs` 只说 `no automatic HTTPS`，且**完全没有任何 ACME 记录** | Caddyfile 没挂进容器：检查 `docker-compose.https.yml` 里的 `./caddy/Caddyfile:/etc/caddy/Caddyfile:ro` |
| 构建镜像时 `go build` 卡在 link 十几分钟、磁盘读放大 | 小规格实例内存不足：先 `docker compose stop` 停业务容器再构建（见 Task 6 实施记录第 2 条） |
| Windows 上 curl 访问 8443 报 `Connection was reset`，但浏览器能打开 | Schannel 客户端问题，不是服务故障；用浏览器或 OpenSSL 客户端复测 |
| 提问报 LLM / embedding 错误 | 服务器访问 `api.deepseek.com` / DashScope 超时：检查服务器出网，或改对应 Base URL |

## 验收标准（全部满足才算完成）

1. `http://<公网IP>:8080` 公网可访问，注册 / 登录可用；
2. `/api/health` 返回 `kb_count=3755`（知识库复用成功，未重复入库）；
3. 问答为逐 token 流式（nginx 关缓冲生效）；
4. `docker compose restart` 后用户 / 会话 / 缓存数据不丢；
5. 改名 / 删除会话立即生效（缓存失效在真实环境成立）。
