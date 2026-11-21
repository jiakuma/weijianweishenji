export type UserRole = 'admin' | 'user'
export type UserId = string | number

export interface AuthUser {
  id: UserId
  username: string
  displayName: string
  role: UserRole
  mustChangePassword?: boolean
}

export interface LoginResult {
  token: string
  user: AuthUser
}

export interface UserInfoRow {
  id: UserId
  username: string
  displayName: string
  role: UserRole
  enabled: boolean
  lastLoginAt: string | null
}

export type FileFormat = 'PDF' | 'DOCX' | 'PPTX' | 'MD' | 'TXT' | '网页' | '图片'
export type SourceType = '本地文件' | '微信文件' | '分享链接' | '网页链接'
export type TaskStatus = '等待中' | '解析中' | '已完成' | '失败' | '重复跳过'

export interface DocChunk {
  id: string
  page: number
  section: string
  text: string
}

export interface KnowledgeDoc {
  id: string
  title: string
  format: FileFormat
  source: SourceType
  sourceUrl?: string
  category: string
  tags: string[]
  status: string
  pages: number
  sizeKb: number
  importedAt: string
  summary: string
  keywords: string[]
  starred: boolean
  chunks: DocChunk[]
  hasFulltext?: boolean
  hasOriginal?: boolean
  officePreview?: boolean
}

export interface CategoryItem {
  id: string
  name: string
  parentId: string
  orderIndex: number
}

export interface CategoryNode extends CategoryItem {
  count: number
  children: CategoryNode[]
  directCount?: number
  directOf?: string
}

export interface ImportTask {
  id: string
  name: string
  format: FileFormat
  source: SourceType
  sizeKb: number
  progress: number
  status: TaskStatus
  stage: string
  relativePath: string
  batchId: string
  attempt: number
  createdAt: string
  docId: string
  message: string
}

export interface DocQuery {
  keyword?: string
  formats?: FileFormat[]
  sources?: SourceType[]
  categories?: string[]
  tags?: string[]
  status?: string
  category?: string
  starredOnly?: boolean
  sort?: 'time' | 'name'
}

export interface Settings {
  displayName: string
  pageSize: number
  viewMode: 'list' | 'card'
  autoSummary: boolean
  autoClassify: boolean
  autoChunk: boolean
  retryCount: number
  supportedFormats: string[]
  llmBaseUrl: string
  llmApiKey: string
  llmApiKeySet?: boolean
  accessToken: string
  accessTokenSet?: boolean
  zhihuCookie: string
  zhihuCookieSet?: boolean
  llmModel: string
  llmThinking: boolean
  temperature: number
  maxTokens: number
  embeddingBaseUrl: string
  embeddingModel: string
  originalsDir: string
  parsedDir: string
  databasePath: string
  [key: string]: unknown
}

export interface StorageInfo {
  originalsDir: string
  parsedDir: string
  databasePath: string
  originalFiles: number
  originalMb: number
  parsedMb: number
  databaseMb: number
  totalMb: number
}

export interface BackupItem {
  name: string
  sizeMb: number
  createdAt: string
}

export interface AssetItem {
  id: string
  name: string
  reference: string
  url: string
  size: number
}

export interface UploadItem {
  file: File
  relativePath: string
  category?: string
}

export interface QACitation {
  docId: string
  docTitle: string
  chunkId: string
  page: number
  section: string
  quote: string
  /** doc = 制度原文切片；issue = 历史审计问题。缺省按 doc 处理。 */
  kind?: 'doc' | 'issue'
  /** 正文 [n] 角标对应的 1-based 编号。 */
  refNo?: number
  issueId?: string
  sourceYear?: number | string
  sourceFile?: string
  auditSubject?: string
  auditProject?: string
  category?: string
  catL1?: string
  catL2?: string
  catL3?: string
  catL4?: string
  manifestation?: string
  problemDesc?: string
  legalBasis?: string
  remediation?: string
  snippet?: string
}

/** 历史审计问题定性库条目（对应后端 audit_issues._row_to_dict）。 */
export interface AuditIssue {
  id: string
  issueId: string
  sourceYear: number
  sourceFile: string
  auditSubject: string
  auditProject: string
  catL1: string
  catL2: string
  catL3: string
  catL4: string
  category: string
  manifestation: string
  problemDesc: string
  legalBasis: string
  remediation: string
  remediationStatus: string
  remediationType?: string
  remediationDeadline?: string
  disclosure?: string
  auditType?: string
  snippet?: string
  score?: number
  notice?: string
}

export interface AuditIssueStats {
  dbTotal: number
  vectorTotal: number
  byYear: Record<string, number>
  bySubject: Record<string, number>
  notice?: string
}

export interface QAAnswer {
  conclusion: string
  basis: string[]
  notice?: string
  markdown?: string
}

export interface AgentAction {
  tool: string
  label: string
  ok: boolean
  costMs?: number
  writable?: boolean
  confirmed?: string
  detail?: unknown
}

export interface ConflictPair { text: string; locate: string }
export interface ConflictRow {
  index: number; topic: string; type: string; severity: string
  explanation: string; a: ConflictPair; b: ConflictPair
}
export interface ConflictDetail {
  taskId: string; conflictCount: number; files?: number; units?: number
  clauses?: number; judgedPairs?: number; topics?: string[]; conflicts?: ConflictRow[]
}

export interface ContractIssue {
  index: number; dimension: string; level: string; title: string
  fact: string; quote: string; locate: string; analysis: string
  impact: string; suggestion: string
}
export interface ContractRecon {
  field: string; files: string; value_a: string; value_b: string; note: string
}
export interface ContractDetail {
  taskId: string; contractType?: string; mainContract?: string
  totalIssues: number; issueCount?: number; reconCount?: number
  levelCount?: Record<string, number>
  issues?: ContractIssue[]; reconciliation?: ContractRecon[]; disclaimer?: string
}

export interface WorkpaperIssue {
  index: number; dimension: string; level: string; title: string
  fact: string; quote: string; locate: string; basis: string
  analysis: string; impact: string; suggestion: string; source?: string
}
export interface WorkpaperRecon {
  field: string; files: string; value_a: string; value_b: string; note: string
}
export interface WorkpaperRequirement {
  id: string; requirement: string; scope?: string; source?: string
}
export interface WorkpaperAssessment {
  id: string; requirement: string; scope?: string; source?: string
  result: '符合' | '不符合' | '无法判断'
  reason: string; evidence?: string[]
}
export interface WorkpaperSummary {
  total: number; pass: number; fail: number; unknown: number; conclusion?: string; opinion?: string
}
export interface WorkpaperDetail {
  taskId: string; unitCount?: number; fileCount?: number; workpaperCount?: number
  totalIssues: number; issueCount: number; reconCount: number
  levelCount?: Record<string, number>
  requirements?: WorkpaperRequirement[]
  assessments?: WorkpaperAssessment[]
  items?: Array<{ index?: number; topic: string; file: string; result: string; reason: string; source?: string }>
  summary?: WorkpaperSummary
  issues?: WorkpaperIssue[]; reconciliation?: WorkpaperRecon[]
  fileRoles?: Array<{ name: string; role: string; unit?: string; topic?: string; chars?: number }>
  disclaimer?: string
}

export interface QAResult {
  id: string
  question: string
  answer: QAAnswer
  citations: QACitation[]
  createdAt: string
  recallMode: string
  feedback: string | null
  route?: 'knowledge' | 'chitchat' | 'clarify' | 'stats' | 'agent'
  actions?: AgentAction[]
  rewrittenQuery?: string
  subQueries?: string[]
  usedLLM?: boolean
  retrievalCount?: number
  retried?: boolean
  agentRunId?: string
  agentSteps?: number
  nativeTools?: boolean
  conflict?: ConflictDetail
  contract?: ContractDetail
  workpaper?: WorkpaperDetail
  status?: 'running' | 'done' | 'stopped' | 'error'
  files?: string[]
}

export interface AgentConfirm {
  confirmId: string
  tool: string
  label: string
  args: Record<string, unknown>
  risk: 'low' | 'high'
}

export interface AgentProgress {
  taskId: string
  taskName?: string
  status: string
  progress: number
  message: string
}

export interface AgentRunItem {
  id: string
  sessionId: string
  question: string
  status: string
  stepsCount: number
  costMs: number
  createdAt: string
  finishedAt: string
}

export interface AgentStepItem {
  seq: number
  kind: string
  tool: string
  args: Record<string, unknown>
  result: Record<string, unknown>
  costMs: number
}

export interface EvalQueryItem {
  id: string
  question: string
  chunkId: string
  docId: string
  docTitle: string
  category: string
  difficulty: string
  createdAt: string
}

export interface EvalMiss {
  question: string
  expectedDoc: string
  topChunk: string
}

export interface EvalRunItem {
  id: string
  createdAt: string
  total: number
  k: number
  mode: string
  hitRate: number
  mrr: number
  costMs: number
  misses: EvalMiss[]
}

export interface EvalRunResult extends EvalRunItem {
  ok: boolean
  message?: string
  runId: string
}

export interface ConflictProgress {
  kind: 'conflict' | 'conflict_done'
  taskId: string
  stage: string
  done: number
  total: number
  compareDone: number
  compareTotal: number
  conflictCount?: number
}

export interface ContractProgress {
  kind: 'contract' | 'contract_done'
  taskId: string
  stage: string
  done: number
  total: number
  totalIssues?: number
}

export interface WorkpaperProgress {
  kind: 'workpaper' | 'workpaper_done'
  taskId: string
  stage: string
  done: number
  total: number
  totalIssues?: number
}

export interface AgentStreamHandlers {
  onStart?: (runId: string, qaId?: string) => void
  onStep?: (action: AgentAction) => void
  onConfirm?: (payload: AgentConfirm) => void
  onProgress?: (payload: AgentProgress) => void
  onConflictProgress?: (payload: ConflictProgress) => void
  onContractProgress?: (payload: ContractProgress) => void
  onWorkpaperProgress?: (payload: WorkpaperProgress) => void
  onStatus?: (steps: number) => void
  onAnswerDelta?: (text: string) => void
  onDone?: (result: QAResult) => void
  onStopped?: () => void
  onError?: (message: string) => void
}

export interface QASession {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  count: number
  lastQuestion: string
}

export interface AgentTool {
  name: string
  label: string
  description: string
  parameters: Record<string, unknown>
  readonly: boolean
}

export const FORMAT_TONE: Record<string, string> = {
  PDF: 'pdf', DOCX: 'docx', PPTX: 'pptx', MD: 'md', TXT: 'txt', 网页: 'web', 图片: 'image',
}

export interface AuditModule {
  id: string
  name: string
  enabled: boolean
  route: string
  group: string
  order: number
}

export interface ConflictConfig {
  mock: boolean
  llmReady: boolean
  mode: string
  budgetDefault?: number
  budgetChoices?: Record<string, number>
  acceptManifest?: boolean
}

export interface ConflictFileStatus {
  filename: string
  status: string
  chars: number | null
  note: string | null
  error: string | null
  units?: number
  clauses?: number
  is_manifest?: boolean
  manifest_count?: number
}

export interface ConflictItem {
  topic: string
  conflict_type: string
  severity: string
  explanation: string
  file_a: string
  subject_a: string
  quote_a: string
  file_b: string
  subject_b: string
  quote_b: string
  chunk_a?: number | null
  chunk_b?: number | null
  // 新版原文定位（可选，兼容历史任务）
  unit_a?: string
  unit_b?: string
  page_a?: number | null
  page_b?: number | null
  article_a?: string
  article_b?: string
  chapter_a?: string
  chapter_b?: string
  locate_a?: string
  locate_b?: string
  clause_id_a?: string
  clause_id_b?: string
  score?: number
  shared_keywords?: string[]
}

export interface ConflictUnit {
  unit_id: string
  name: string
  source_file: string
  start_page: number | null
  end_page: number | null
  group?: string
  origin: string
  chars: number
  clause_count: number
  note?: string
}

export interface ConflictCoverage {
  documents?: number
  documents_parsed?: number
  documents_skipped?: Array<{ filename: string; reason: string }>
  manifest_items?: number
  toc_entries?: number
  toc_pages?: number
  units?: number
  units_by_origin?: Record<string, number>
  clauses?: number
  units_without_clause?: string[]
  pages_total?: number
  pages_covered?: number
  pages_unassigned?: number
  page_coverage_rate?: number
  theoretical_pairs?: number
  candidate_pairs?: number
  compared_pairs?: number
  unit_pairs_compared?: number
  judged_pairs?: number
  failed_pairs?: number
  first_error?: string
  budget?: number
  skipped_pairs?: number
  skip_reason?: string
  artifact_error?: string
}

export interface ConflictSession {
  id: string
  title: string
  created_at: string
  updated_at: string
  task_count: number
  task_ids: string[]
}

export interface ConflictTaskItem {
  task_id: string
  name: string
  created_at: string
  status: string
  stage: string
  mode: string
  total: number
  done: number
  compare_total: number
  compare_done: number
  conflict_count: number
  error: string
}

export interface ConflictTaskDetail extends ConflictTaskItem {
  files: ConflictFileStatus[]
  conflicts: ConflictItem[]
  units?: ConflictUnit[]
  coverage?: ConflictCoverage
  clause_count?: number
  report: {
    mode?: string
    topics?: string[]
    docs?: Array<{
      filename: string
      chars: number | null
      clause_count: number
      unit_count?: number
      is_manifest?: boolean
      error: string | null
      note: string | null
    }>
    conflicts?: ConflictItem[]
    units?: ConflictUnit[]
    coverage?: ConflictCoverage
    clause_count?: number
  }
}
