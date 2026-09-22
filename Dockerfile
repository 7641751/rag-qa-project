# rag_qa_project/Dockerfile —— 后端运行镜像
# 基础镜像与开发 venv 同为 Python 3.14（requirements 都是 >= 约束，如需回退还 python:3.12-slim 亦可）
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 1) 先装依赖（独立层：改业务代码不必重装依赖）
COPY requirements.txt .
# ⚠ 构建时剔除两行「本地 HF 嵌入」依赖（sentence-transformers / langchain-huggingface）：
#   运行时代码从不 import 它们（嵌入走 DashScope 云端，源码里只剩历史注释），
#   但它们会拖入 torch/transformers（≈ +2GB 镜像、拉取与构建显著变慢），对运行零价值。
RUN grep -vE '^(sentence-transformers|langchain-huggingface)' requirements.txt > /tmp/reqs.txt \
 && pip install -r /tmp/reqs.txt

# 2) 再拷代码（.dockerignore 已排除 data/ frontend/ tests/ docs/ .env 等非运行必需物）
COPY . .

EXPOSE 8000
# 单 worker：LangGraph checkpointer / chroma / lru_cache 都假定单进程内存态（见 README 开发约定）
CMD ["python", "-m", "uvicorn", "backend.app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
