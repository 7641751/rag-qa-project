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

## Task 6（可选）：域名 + HTTPS

有域名时最省事的是用 Caddy（主机安装或加一个容器），`Caddyfile` 两行自动签证书：

```
your-domain.com {
    reverse_proxy 127.0.0.1:8080
}
```

再放行安全组 80 / 443。不做也不影响简历效果——「IP:8080 可访问」已经成立。

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
| 提问报 LLM / embedding 错误 | 服务器访问 `api.deepseek.com` / DashScope 超时：检查服务器出网，或改对应 Base URL |

## 验收标准（全部满足才算完成）

1. `http://<公网IP>:8080` 公网可访问，注册 / 登录可用；
2. `/api/health` 返回 `kb_count=3755`（知识库复用成功，未重复入库）；
3. 问答为逐 token 流式（nginx 关缓冲生效）；
4. `docker compose restart` 后用户 / 会话 / 缓存数据不丢；
5. 改名 / 删除会话立即生效（缓存失效在真实环境成立）。
