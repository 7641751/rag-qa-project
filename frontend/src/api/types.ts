export type StepNode = 'retrieve' | 'grade_documents' | 'rewrite_query' | 'generate';
export interface Step { node: StepNode; label: string; detail?: string; }
export interface Source { title: string; snippet?: string; score?: number | null; }
export type StepEvent = Step;
export interface TokenEvent { text: string; }
export interface SourcesEvent { sources: Source[]; }
/** grounded=false 表示本轮未命中知识库、答案完全来自模型通用知识 → 必须渲染警示标识 */
export interface DoneEvent { thread_id: string; rewrites: number; grounded: boolean; }
export type ErrorCode =
  | 'VALIDATION_ERROR' | 'RETRIEVAL_ERROR' | 'LLM_ERROR' | 'INTERNAL_ERROR'
  | 'UNSUPPORTED_FILE_TYPE' | 'FILE_TOO_LARGE' | 'PARSE_ERROR'
  | 'EMPTY_DOCUMENT' | 'EMBEDDING_ERROR' | 'NOT_FOUND'
  // P2 鉴权新增 4 个：401 两种（UNAUTHORIZED 凭证缺失/失效、INVALID_CREDENTIALS 用户名或
  // 密码错）+ 403 越权 + 409 注册重名。后端对登录失败的两种原因**故意不区分**，前端也一律
  // 只显示 message、不据此分支。
  | 'UNAUTHORIZED' | 'INVALID_CREDENTIALS' | 'FORBIDDEN' | 'USERNAME_TAKEN';
export interface ErrorEvent { code: ErrorCode; message: string; }
export interface ChatRequest { question: string; thread_id: string; }
export type HistoryRole = 'user' | 'assistant';
/** grounded 可选：后端未把它持久化进 AIMessage.additional_kwargs 时为 null/缺失 */
export interface HistoryMessage { role: HistoryRole; content: string; sources?: Source[]; grounded?: boolean | null; }
export interface HistoryResponse { thread_id: string; messages: HistoryMessage[]; }
/** DELETE /api/chat/threads/{id} 的响应。deleted=false 表示本来就不存在（幂等，非错误）。 */
export interface ChatDeleteResponse { thread_id: string; deleted: boolean; }
export interface Health { status: string; kb_count?: number; model?: string; }
export interface StreamHandlers {
  onStep?: (e: StepEvent) => void;
  onToken?: (e: TokenEvent) => void;
  onSources?: (e: SourcesEvent) => void;
  onDone?: (e: DoneEvent) => void;
  onError?: (e: ErrorEvent) => void;
}

// ---------- 知识库（对齐 docs/api/openapi.yaml 的 Kb* schema） ----------
export type KbStage = 'saved' | 'parsed' | 'split' | 'embedding';

export interface KbProgressEvent {
  stage: KbStage;
  current?: number | null;   // embedding 阶段必有（已嵌入段数）
  total?: number | null;     // embedding 阶段必有（总段数）
  message?: string;          // 可读中文短句，直接显示
}

export interface KbDoneEvent {
  doc_id: string;
  filename: string;
  chunks: number;
  replaced: boolean;         // true = 同名旧文档已被替换
  uploaded_at: string;       // ISO 8601 UTC
}

export interface KbStreamHandlers {
  onProgress?: (e: KbProgressEvent) => void;
  onDone?: (e: KbDoneEvent) => void;
  onError?: (e: ErrorEvent) => void;
}

export interface KbDocument {
  doc_id: string;
  filename: string;
  title?: string;
  chunks: number;
  size_bytes?: number;
  uploaded_at: string;
}

export interface KbBuiltinSummary { docs: number; chunks: number; }

export interface KbListResponse {
  documents: KbDocument[];
  builtin?: KbBuiltinSummary;   // 可选：后端统计不到会整体省略
}

export interface KbDeleteResponse {
  doc_id: string;
  filename: string;
  deleted_chunks: number;
}

// ---------- 鉴权（对齐 docs/api/openapi.yaml 的 Auth* schema） ----------
export interface AuthUser { id: number; username: string; }

export interface RegisterRequest { username: string; password: string; }

/** 契约里 LoginRequest 与 RegisterRequest **同形**（后端复用同一个 pydantic 模型 Auth），
 *  所以这里复用类型而不是重复定义一份，避免将来两边约束漂移。 */
export type LoginRequest = RegisterRequest;

export interface LoginResponse {
  access_token: string;
  token_type: string;      // 固定 'bearer'
  user: AuthUser;
}

// ---------- 会话列表（对齐 docs/api/openapi.yaml 的 Conversation* schema） ----------
export interface ConversationSummary {
  thread_id: string;
  title: string;
  created_at: string;      // ISO 8601 UTC
  updated_at: string;      // 列表按它倒序
}

export interface ThreadListResponse {
  threads: ConversationSummary[];
  /** 后端**真实总数**。而 threads 最多 50 条，所以 total > threads.length 时前端要提示
   *  「仅显示最近 50 条」—— 只看数组长度是看不出被截断的。 */
  total: number;
}

export interface RenameRequest { title: string; }

export interface RenameResponse { thread_id: string; title: string; }
