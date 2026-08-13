import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArchiveRestore, BookOpen, Box, Brain, Check, ChevronDown,
  CircleAlert, ExternalLink, FileText, FolderOpen, HardDriveDownload, Import,
  Library, ListChecks, MapPin, Plus, Search, Settings, ShieldCheck, Tags,
  Trash2, Upload, Video, X,
} from 'lucide-react'

type SourceSummary = {
  id: string
  title: string
  source_type: string
  author?: string | null
  language: string
  notes?: string | null
  source_date?: string | null
  rights?: string | null
  categories: string[]
  tags: string[]
  processing_state: string
  imported_at: string
  updated_at: string
  deleted_at?: string | null
}
type Version = {
  id: string
  artifact_sha256: string
  original_name: string
  media_type?: string | null
  completeness: string
  created_at: string
}
type Relation = {
  id: string
  source_id: string
  related_source_id: string
  relation_type: string
  created_at: string
}
type SourceDetail = SourceSummary & { versions: Version[]; relations: Relation[] }
type Job = {
  id: string
  kind: string
  state: string
  progress: number
  message?: string | null
  source_id?: string | null
  created_at: string
  updated_at: string
  attempt_count: number
}
type Card = {
  id: string
  card_type: string
  url: string
  title: string
  author?: string | null
  notes?: string | null
  tags: string[]
  created_at: string
}
type SearchItem = {
  kind: 'source' | 'knowledge' | 'external_card'
  id: string
  title: string
  relevance: number
  processing_state?: string
  source_type?: string
  updated_at: string
}
type Backup = { id: string; archive_name: string; created_at: string; state: string }
type Representation = {
  id: string
  kind: string
  parser_name: string
  config_hash: string
  parent_representation_id?: string | null
  text_content: string
  created_at: string
}
type Locator = Record<string, unknown>
type Evidence = {
  id: string
  excerpt: string
  locator: Locator
  is_validated: boolean
  created_at: string
  representation_id: string
}
type Citation = { id: string; evidence_id: string; created_at: string }
type CitationDetail = Citation & {
  source_id: string
  title: string
  processing_state: string
  locator: Locator
  context: string
  human_revised: boolean
}
type Knowledge = {
  id: string
  kind: string
  statement: string
  status: string
  evidence_ids: string[]
  created_at: string
  published_at?: string | null
}
type Topic = { id: string; name: string; source_ids: string[]; created_at: string }
type MetadataRevision = { id: string; ordinal: number; created_at: string; snapshot: Record<string, unknown> }
type Health = { database: string }
type VideoFrame = {
  id: string
  artifact_sha256: string
  ordinal: number
  time_ms: number
  width?: number | null
  height?: number | null
}
type VideoMetadata = {
  container_name: string
  duration_ms: number
  width?: number | null
  height?: number | null
  video_codec?: string | null
  audio_codec?: string | null
}
type VideoDetail = {
  source_id: string
  version: Version
  analysis: { id: string; metadata: VideoMetadata; frames: VideoFrame[] } | null
  media_capability: { enabled: boolean }
  ai_capability: { enabled: boolean; provider?: string | null; reason?: string }
}

type Page = 'library' | 'import' | 'video' | 'search' | 'knowledge' | 'jobs' | 'external' | 'transfers' | 'settings'
type SourceScope = 'active' | 'deleted'
const API = '/api/v1'
const categories = [
  ['technical', '技术'], ['business', '商业'], ['education', '教育'], ['news', '新闻'],
  ['interview', '访谈'], ['podcast', '播客'], ['document', '文档'],
] as const
const rights = [
  ['owned', '本人拥有'], ['authorized', '已获授权'], ['permitted', '已获许可'],
  ['open_license', '开放许可'], ['other', '其他'],
] as const
const relationTypes = [
  ['new_version_of', '新版本'], ['revision_of', '修订版本'], ['related_to', '相关'],
  ['user_declared_same_work', '用户声明同一作品'],
] as const
const knowledgeTypes = [
  ['fact', '事实'], ['opinion', '观点'], ['instruction', '指令'], ['case', '案例'],
  ['citation', '引文'], ['unverified', '未核验'],
] as const

class ApiError extends Error {
  status: number
  code: string
  conflicts: string[]
  reason?: string

  constructor(status: number, code: string, message: string, conflicts: string[] = [], reason?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.conflicts = conflicts
    this.reason = reason
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => ({}))
    const detail = isObject(payload) && isObject(payload.detail) ? payload.detail : {}
    const message = typeof detail.message === 'string' ? detail.message : '本地请求失败'
    const code = typeof detail.code === 'string' ? detail.code : `http_${response.status}`
    const conflicts = Array.isArray(detail.conflicts)
      ? detail.conflicts.filter((item): item is string => typeof item === 'string')
      : []
    const reason = typeof detail.reason === 'string' ? detail.reason : undefined
    throw new ApiError(response.status, code, message, conflicts, reason)
  }
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

async function uploadFile(path: string, body: FormData, onProgress: (value: number) => void): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API}${path}`)
    xhr.responseType = 'json'
    xhr.upload.addEventListener('progress', event => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    })
    xhr.addEventListener('load', () => {
      const payload: unknown = xhr.response || (() => {
        try { return JSON.parse(xhr.responseText) } catch { return {} }
      })()
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload)
        return
      }
      const detail = isObject(payload) && isObject(payload.detail) ? payload.detail : {}
      reject(new ApiError(
        xhr.status,
        typeof detail.code === 'string' ? detail.code : `http_${xhr.status}`,
        typeof detail.message === 'string' ? detail.message : '文件导入失败',
      ))
    })
    xhr.addEventListener('error', () => reject(new ApiError(0, 'network_error', '本地请求失败')))
    xhr.send(body)
  })
}

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}
function formatDateOnly(value?: string | null) { return value || '-' }
function stateLabel(value: string) {
  const map: Record<string, string> = {
    queued: '排队', running: '处理中', retry_wait: '等待重试', succeeded: '已完成',
    failed: '失败', blocked: '已阻止', cancelled: '已取消', awaiting_ocr: '等待 OCR',
    pending: '待处理', complete: '完整', incomplete: '不完整', draft: '草稿', published: '已发布',
  }
  return map[value] || value
}
function sourceType(value: string) {
  return value === 'paste' ? '粘贴文本' : value === 'file' ? '本地文件' : value === 'douyin' ? '抖音参考' : value === 'video_link' ? '链接视频' : '外部卡'
}
function labelFor(items: readonly (readonly [string, string])[], value?: string | null) {
  return items.find(item => item[0] === value)?.[1] || value || '-'
}
function parseTags(value: string) { return value.split(/[,，]/).map(item => item.trim()).filter(Boolean) }
function asText(value: unknown) { return typeof value === 'string' || typeof value === 'number' ? String(value) : '' }
function locatorLabel(locator: Locator) {
  const type = asText(locator.type)
  const chars = Array.isArray(locator.char_range) ? locator.char_range.map(asText).join('–') : ''
  if (type === 'video_metadata') {
    const duration = asText(locator.duration_ms)
    return `视频元数据${duration ? `，时长 ${formatDuration(Number(duration))}` : ''}`
  }
  if (type === 'video_time_range') {
    return `视频 ${formatDuration(Number(locator.start_ms))} 至 ${formatDuration(Number(locator.end_ms))}`
  }
  if (type === 'pdf_page_char_range' || type === 'pdf_char_range') {
    return `PDF 第 ${asText(locator.page) || '未知'} 页${chars ? `，字符 ${chars}` : ''}`
  }
  if (type === 'docx_structure_char_range') {
    return `DOCX ${asText(locator.structure) || '正文'}，段落 ${asText(locator.paragraph_ordinal) || '未知'}${chars ? `，字符 ${chars}` : ''}`
  }
  if (type === 'text_range') {
    const lines = Array.isArray(locator.line_range) ? locator.line_range.map(asText).join('–') : ''
    const heading = asText(locator.heading)
    return `${heading ? `“${heading}”` : '文本'}${asText(locator.paragraph_ordinal) ? `，段落 ${asText(locator.paragraph_ordinal)}` : ''}${lines ? `，行 ${lines}` : ''}${chars ? `，字符 ${chars}` : ''}`
  }
  return type || '已记录位置'
}
function formatDuration(milliseconds?: number | null) {
  if (!Number.isFinite(milliseconds) || !milliseconds || milliseconds < 0) return '-'
  const totalSeconds = Math.floor(milliseconds / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}` : `${minutes}:${String(seconds).padStart(2, '0')}`
}

function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 4500)
    return () => window.clearTimeout(timer)
  }, [onClose])
  return <div className="toast" role="status"><CircleAlert size={18} /><span>{message}</span><button onClick={onClose} aria-label="关闭"><X size={16} /></button></div>
}

function PageHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return <header className="page-header"><h1>{title}</h1>{children && <div className="page-actions">{children}</div>}</header>
}

function Status({ value }: { value: string }) { return <span className={`status status-${value}`}>{stateLabel(value)}</span> }

export function App() {
  const [page, setPage] = useState<Page>('library')
  const [sources, setSources] = useState<SourceSummary[]>([])
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null)
  const [selectedSource, setSelectedSource] = useState<SourceDetail | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [cards, setCards] = useState<Card[]>([])
  const [topics, setTopics] = useState<Topic[]>([])
  const [knowledge, setKnowledge] = useState<Knowledge[]>([])
  const [focusedCardId, setFocusedCardId] = useState<string | null>(null)
  const [focusedKnowledgeId, setFocusedKnowledgeId] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)
  const selectedRequest = useRef(0)

  const invalidateSelectedSource = useCallback(() => {
    selectedRequest.current += 1
    setSelectedSource(null)
  }, [])
  const loadSources = useCallback(async () => {
    setSources(await request<SourceSummary[]>('/sources?include_deleted=true'))
  }, [])
  const loadSelectedSource = useCallback(async (sourceId: string) => {
    const requestId = ++selectedRequest.current
    try {
      const source = await request<SourceDetail>(`/sources/${sourceId}`)
      if (requestId !== selectedRequest.current) return false
      setSelectedSource(source)
      return true
    } catch (error) {
      if (requestId !== selectedRequest.current) return false
      throw error
    }
  }, [])
  const loadJobs = useCallback(async () => setJobs(await request<Job[]>('/jobs')), [])
  const loadCards = useCallback(async () => setCards(await request<Card[]>('/external/cards')), [])
  const loadTopics = useCallback(async () => setTopics(await request<Topic[]>('/topics')), [])
  const loadKnowledge = useCallback(async () => setKnowledge(await request<Knowledge[]>('/knowledge')), [])
  const refreshSource = useCallback(async () => {
    await loadSources()
    if (selectedSourceId) await loadSelectedSource(selectedSourceId)
  }, [loadSelectedSource, loadSources, selectedSourceId])
  const refresh = useCallback(async () => {
    try {
      await Promise.all([loadSources(), loadJobs(), loadCards(), loadTopics(), loadKnowledge()])
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '读取本地数据失败')
    } finally {
      setLoading(false)
    }
  }, [loadCards, loadJobs, loadKnowledge, loadSources, loadTopics])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadJobs().catch(() => undefined)
      void loadSources().catch(() => undefined)
    }, 2500)
    return () => window.clearInterval(timer)
  }, [loadJobs, loadSources])
  useEffect(() => {
    if (!selectedSourceId) {
      selectedRequest.current += 1
      setSelectedSource(null)
      return
    }
    const load = async () => {
      try {
        const loaded = await loadSelectedSource(selectedSourceId)
        if (!loaded) return
      } catch (error) {
        setSelectedSource(null)
        setSelectedSourceId(null)
        setMessage(error instanceof Error ? error.message : '无法读取来源')
      }
    }
    void load()
    const timer = window.setInterval(() => { void load().catch(() => undefined) }, 3500)
    return () => window.clearInterval(timer)
  }, [loadSelectedSource, selectedSourceId])

  const openSource = useCallback((id: string) => {
    setFocusedCardId(null)
    setFocusedKnowledgeId(null)
    invalidateSelectedSource()
    setSelectedSourceId(id || null)
    setPage('library')
  }, [invalidateSelectedSource])
  const openCard = useCallback((id: string) => {
    invalidateSelectedSource()
    setSelectedSourceId(null)
    setFocusedKnowledgeId(null)
    setFocusedCardId(id)
    setPage('external')
  }, [invalidateSelectedSource])
  const openKnowledge = useCallback((id: string) => {
    invalidateSelectedSource()
    setSelectedSourceId(null)
    setFocusedCardId(null)
    setFocusedKnowledgeId(id)
    setPage('knowledge')
  }, [invalidateSelectedSource])
  const navigate = (next: Page) => {
    invalidateSelectedSource()
    setSelectedSourceId(null)
    setFocusedCardId(null)
    setFocusedKnowledgeId(null)
    setPage(next)
  }

  const nav: { id: Page; label: string; icon: React.ReactNode }[] = [
    { id: 'library', label: '资料库', icon: <Library size={18} /> },
    { id: 'import', label: '导入', icon: <Import size={18} /> },
    { id: 'video', label: '视频', icon: <Video size={18} /> },
    { id: 'search', label: '检索', icon: <Search size={18} /> },
    { id: 'knowledge', label: '知识', icon: <Brain size={18} /> },
    { id: 'jobs', label: '作业', icon: <ListChecks size={18} /> },
    { id: 'external', label: '外部卡', icon: <Video size={18} /> },
    { id: 'transfers', label: '备份与导出', icon: <HardDriveDownload size={18} /> },
    { id: 'settings', label: '设置', icon: <Settings size={18} /> },
  ]

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><BookOpen size={22} /><span>源知库</span></div>
      <nav aria-label="主导航">
        {nav.map(item => <button type="button" className={page === item.id ? 'nav-item active' : 'nav-item'} onClick={() => navigate(item.id)} key={item.id}>{item.icon}<span>{item.label}</span></button>)}
      </nav>
      <div className="sidebar-foot"><ShieldCheck size={16} /><span>仅本机 · 无遥测</span></div>
    </aside>
    <main className="main-content">
      {loading ? <div className="loading">正在读取本地资料库</div> : <>
        {page === 'library' && <LibraryPage
          sources={sources}
          selected={selectedSource}
          onSelect={openSource}
          onClose={() => { invalidateSelectedSource(); setSelectedSourceId(null) }}
          onRefresh={refreshSource}
          onTopicsRefresh={loadTopics}
          topics={topics}
          onKnowledgeRefresh={loadKnowledge}
          onMessage={setMessage}
        />}
        {page === 'import' && <ImportPage onDone={() => { void refresh(); navigate('library') }} onMessage={setMessage} />}
        {page === 'video' && <VideoWorkspace onDone={() => { void refresh(); navigate('library') }} onDoneLink={() => { void refresh(); navigate('jobs') }} onMessage={setMessage} />}
        {page === 'search' && <SearchPage onSelectSource={openSource} onSelectKnowledge={openKnowledge} onSelectCard={openCard} onMessage={setMessage} />}
        {page === 'knowledge' && <KnowledgePage knowledge={knowledge} focusedId={focusedKnowledgeId} onRefresh={loadKnowledge} onMessage={setMessage} />}
        {page === 'jobs' && <JobsPage jobs={jobs} onRefresh={refresh} onMessage={setMessage} />}
        {page === 'external' && <ExternalCardsPage cards={cards} focusedId={focusedCardId} onDone={loadCards} onMessage={setMessage} />}
        {page === 'transfers' && <TransfersPage onMessage={setMessage} />}
        {page === 'settings' && <SettingsPage onMessage={setMessage} />}
      </>}
    </main>
    {message && <Toast message={message} onClose={() => setMessage('')} />}
  </div>
}

function LibraryPage({
  sources, selected, onSelect, onClose, onRefresh, onTopicsRefresh, topics, onKnowledgeRefresh, onMessage,
}: {
  sources: SourceSummary[]
  selected: SourceDetail | null
  onSelect: (id: string) => void
  onClose: () => void
  onRefresh: () => Promise<void>
  onTopicsRefresh: () => Promise<void>
  topics: Topic[]
  onKnowledgeRefresh: () => Promise<void>
  onMessage: (message: string) => void
}) {
  const [filter, setFilter] = useState('')
  const [scope, setScope] = useState<SourceScope>('active')
  const visible = useMemo(() => sources.filter(source => {
    const inScope = scope === 'deleted' ? Boolean(source.deleted_at) : !source.deleted_at
    const needle = filter.toLowerCase()
    return inScope && (source.title.toLowerCase().includes(needle) || source.tags.some(tag => tag.toLowerCase().includes(needle)))
  }), [filter, scope, sources])
  return <div className="page library-page">
    <PageHeader title={selected ? selected.title : '资料库'}>
      {selected
        ? <button type="button" className="button secondary" onClick={onClose}>返回列表</button>
        : <span className="count">{visible.length} 项来源</span>}
    </PageHeader>
    {selected ? <SourceDetail
      source={selected}
      sources={sources}
      topics={topics}
      onRefresh={onRefresh}
      onTopicsRefresh={onTopicsRefresh}
      onKnowledgeRefresh={onKnowledgeRefresh}
      onPurged={onClose}
      onMessage={onMessage}
    /> : <>
      <div className="toolbar library-toolbar">
        <label className="search-field"><Search size={17}/><input value={filter} onChange={event => setFilter(event.target.value)} placeholder="按标题或标签筛选" /></label>
        <div className="segmented" aria-label="来源范围">
          <button type="button" className={scope === 'active' ? 'selected' : ''} onClick={() => setScope('active')}>当前</button>
          <button type="button" className={scope === 'deleted' ? 'selected' : ''} onClick={() => setScope('deleted')}>已删除</button>
        </div>
        <button type="button" className="icon-button" onClick={() => void onRefresh()} title="刷新资料库"><ArchiveRestore size={18}/></button>
      </div>
      {visible.length ? <div className="source-table" role="table">
        <div className="table-head" role="row"><span>标题</span><span>类型</span><span>状态</span><span>更新时间</span></div>
        {visible.map(source => <button type="button" className="table-row" role="row" onClick={() => onSelect(source.id)} key={source.id}>
          <span className="source-title"><FileText size={18}/><b>{source.title}</b><small>{source.author || '未署名'}{source.tags.length ? ` · ${source.tags.join('、')}` : ''}</small></span>
          <span>{sourceType(source.source_type)}</span><span>{source.deleted_at ? <Status value="cancelled"/> : <Status value={source.processing_state}/>}</span><time>{formatDate(source.updated_at)}</time>
        </button>)}
      </div> : <Empty icon={<FolderOpen size={36}/>} text={scope === 'deleted' ? '没有已删除来源' : '资料库尚无来源'} />}
    </>}
  </div>
}

function SourceDetail({
  source, sources, topics, onRefresh, onTopicsRefresh, onKnowledgeRefresh, onPurged, onMessage,
}: {
  source: SourceDetail
  sources: SourceSummary[]
  topics: Topic[]
  onRefresh: () => Promise<void>
  onTopicsRefresh: () => Promise<void>
  onKnowledgeRefresh: () => Promise<void>
  onPurged: () => void
  onMessage: (message: string) => void
}) {
  const [showPurge, setShowPurge] = useState(false)
  const [versionId, setVersionId] = useState('')
  const [representations, setRepresentations] = useState<Representation[]>([])
  const [representationId, setRepresentationId] = useState('')
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [citations, setCitations] = useState<Record<string, Citation[]>>({})
  const [citation, setCitation] = useState<CitationDetail | null>(null)
  const [editingRevision, setEditingRevision] = useState(false)
  const [revisedText, setRevisedText] = useState('')
  const [highlightedExcerpt, setHighlightedExcerpt] = useState('')
  const [metadataOpen, setMetadataOpen] = useState(false)
  const [metadata, setMetadata] = useState({ title: '', author: '', language: 'zh', notes: '', sourceDate: '', tags: '', categories: [] as string[] })
  const [right, setRight] = useState('')
  const [revisions, setRevisions] = useState<MetadataRevision[]>([])
  const [revisionsOpen, setRevisionsOpen] = useState(false)
  const [relatedSourceId, setRelatedSourceId] = useState('')
  const [relationType, setRelationType] = useState('related_to')
  const [topicId, setTopicId] = useState('')
  const [newTopic, setNewTopic] = useState('')
  const [knowledgeKind, setKnowledgeKind] = useState('fact')
  const [knowledgeStatement, setKnowledgeStatement] = useState('')
  const [knowledgeEvidenceIds, setKnowledgeEvidenceIds] = useState<string[]>([])
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const busyActionRef = useRef<string | null>(null)
  const textRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    setVersionId(source.versions[0]?.id || '')
    setMetadata({
      title: source.title,
      author: source.author || '',
      language: source.language,
      notes: source.notes || '',
      sourceDate: source.source_date || '',
      tags: source.tags.join(', '),
      categories: source.categories,
    })
    setRight(source.rights || '')
    setCitation(null)
    setShowPurge(false)
    setKnowledgeEvidenceIds([])
  }, [source.id])

  const version = source.versions.find(item => item.id === versionId) || source.versions[0]
  const representation = representations.find(item => item.id === representationId) || representations.at(-1)
  const sourceDeleted = Boolean(source.deleted_at)
  const runBusyAction = async (action: string, task: () => Promise<void>) => {
    const isDeletedSourceRecovery = action === 'lifecycle-restore' || action === 'lifecycle-purge'
    if (busyActionRef.current || (sourceDeleted && !isDeletedSourceRecovery)) return
    busyActionRef.current = action
    setBusyAction(action)
    try {
      await task()
    } finally {
      busyActionRef.current = null
      setBusyAction(null)
    }
  }
  const loadRepresentations = useCallback(async () => {
    if (!version) {
      setRepresentations([])
      return
    }
    try {
      const items = await request<Representation[]>(`/documents/${version.id}/representations`)
      setRepresentations(items)
      setRepresentationId(current => items.some(item => item.id === current) ? current : (items.at(-1)?.id || ''))
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '读取表示失败')
    }
  }, [onMessage, version])
  useEffect(() => { void loadRepresentations() }, [loadRepresentations])
  useEffect(() => {
    if (!representationId) {
      setEvidence([])
      setCitations({})
      return
    }
    const loadEvidence = async () => {
      try {
        const items = await request<Evidence[]>(`/representations/${representationId}/evidence`)
        setEvidence(items)
        const groups = await Promise.all(items.map(async item => [item.id, await request<Citation[]>(`/citations?evidence_id=${encodeURIComponent(item.id)}`)] as const))
        setCitations(Object.fromEntries(groups))
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '读取证据失败')
      }
    }
    void loadEvidence()
  }, [onMessage, representationId])

  const saveMetadata = async (event: React.FormEvent) => {
    event.preventDefault()
    await runBusyAction('metadata', async () => {
      try {
        await request(`/sources/${source.id}/metadata`, {
          method: 'PUT',
          body: JSON.stringify({
            title: metadata.title,
            author: metadata.author || null,
            language: metadata.language,
            notes: metadata.notes || null,
            source_date: metadata.sourceDate || null,
            categories: metadata.categories,
            tags: parseTags(metadata.tags),
          }),
        })
        setMetadataOpen(false)
        await onRefresh()
        onMessage('来源元数据已修订')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '元数据保存失败')
      }
    })
  }
  const updateRights = async () => {
    if (!right) return
    await runBusyAction('rights', async () => {
      try {
        await request(`/sources/${source.id}/rights?rights=${encodeURIComponent(right)}`, { method: 'PUT' })
        await onRefresh()
        onMessage('权利确认已更新')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '权利确认更新失败')
      }
    })
  }
  const loadRevisions = async () => {
    try {
      setRevisions(await request<MetadataRevision[]>(`/sources/${source.id}/metadata-revisions`))
      setRevisionsOpen(true)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '读取修订记录失败')
    }
  }
  const saveManualRevision = async () => {
    if (!version || !revisedText.trim()) {
      onMessage('请输入人工修订文本')
      return
    }
    await runBusyAction('manual-revision', async () => {
      try {
        await request(`/documents/${version.id}/representations/manual`, { method: 'POST', body: JSON.stringify({ text: revisedText }) })
        setEditingRevision(false)
        setRevisedText('')
        await loadRepresentations()
        await onRefresh()
        onMessage('已创建人工修订表示')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '人工修订保存失败')
      }
    })
  }
  const lifecycle = async (action: 'delete' | 'restore' | 'purge') => {
    await runBusyAction(`lifecycle-${action}`, async () => {
      try {
        await request(`/sources/${source.id}/${action}`, { method: 'POST' })
        if (action === 'purge') {
          onPurged()
          await onRefresh()
          onMessage('已永久删除来源与无引用 artifact')
          return
        }
        await onRefresh()
        onMessage(action === 'restore' ? '已恢复来源' : '已移至软删除')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '操作失败')
      }
    })
  }
  const addRelation = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!relatedSourceId) return
    await runBusyAction('relation', async () => {
      try {
        await request(`/sources/${source.id}/relations`, { method: 'POST', body: JSON.stringify({ related_source_id: relatedSourceId, relation_type: relationType }) })
        setRelatedSourceId('')
        await onRefresh()
        onMessage('来源关系已创建')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '来源关系创建失败')
      }
    })
  }
  const addTopic = async () => {
    if (!topicId) return
    await runBusyAction('topic', async () => {
      try {
        await request(`/topics/${topicId}/sources/${source.id}`, { method: 'POST' })
        setTopicId('')
        await onTopicsRefresh()
        onMessage('来源已关联主题')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '主题关联失败')
      }
    })
  }
  const createTopic = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!newTopic.trim()) return
    await runBusyAction('topic-create', async () => {
      try {
        await request('/topics', { method: 'POST', body: JSON.stringify({ name: newTopic.trim(), source_ids: [source.id] }) })
        setNewTopic('')
        await onTopicsRefresh()
        onMessage('主题已创建并关联来源')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '主题创建失败')
      }
    })
  }
  const createCitation = async (evidenceId: string) => {
    await runBusyAction(`citation-${evidenceId}`, async () => {
      try {
        const created = await request<Citation>(`/citations?evidence_id=${encodeURIComponent(evidenceId)}`, { method: 'POST' })
        setCitations(current => ({ ...current, [evidenceId]: [...(current[evidenceId] || []), created] }))
        onMessage('引用已创建')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '引用创建失败')
      }
    })
  }
  const loadCitation = async (citationId: string) => {
    try {
      setCitation(await request<CitationDetail>(`/citations/${citationId}`))
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '读取引用失败')
    }
  }
  const locateEvidence = (item: Evidence) => {
    const page = asText(item.locator.page)
    if (version?.media_type === 'application/pdf' && page && page !== 'unknown') {
      onMessage(`证据位于 PDF 第 ${page} 页`)
      return
    }
    setHighlightedExcerpt(item.excerpt)
    window.requestAnimationFrame(() => textRef.current?.querySelector('mark')?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
  }
  const createKnowledge = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!knowledgeStatement.trim()) return
    await runBusyAction('knowledge', async () => {
      try {
        await request('/knowledge', { method: 'POST', body: JSON.stringify({ kind: knowledgeKind, statement: knowledgeStatement.trim(), evidence_ids: knowledgeEvidenceIds }) })
        setKnowledgeStatement('')
        setKnowledgeEvidenceIds([])
        await onKnowledgeRefresh()
        onMessage('知识草稿已创建')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '知识草稿创建失败')
      }
    })
  }
  const relatedSources = source.relations.map(item => {
    const otherId = item.source_id === source.id ? item.related_source_id : item.source_id
    return { ...item, other: sources.find(candidate => candidate.id === otherId) }
  })
  const linkedTopics = topics.filter(item => item.source_ids.includes(source.id))
  const availableTopics = topics.filter(item => !item.source_ids.includes(source.id))
  const availableRelations = sources.filter(item => item.id !== source.id && !item.deleted_at)

  return <section className="detail-layout">
    <div className="detail-main">
      <div className="metadata-grid">
        <div><label>来源类型</label><span>{sourceType(source.source_type)}</span></div>
        <div><label>处理状态</label><Status value={source.processing_state}/></div>
        <div><label>权利确认</label><span>{labelFor(rights, source.rights)}</span></div>
        <div><label>语言</label><span>{source.language}</span></div>
        <div><label>来源日期</label><span>{formatDateOnly(source.source_date)}</span></div>
        <div><label>导入时间</label><span>{formatDate(source.imported_at)}</span></div>
        <div className="wide"><label>固定分类</label><span>{source.categories.map(value => labelFor(categories, value)).join('、') || '-'}</span></div>
        <div className="wide"><label>标签</label><span>{source.tags.join('、') || '-'}</span></div>
        <div className="wide"><label>备注</label><span>{source.notes || '-'}</span></div>
      </div>

      {sourceDeleted && <p className="deleted-notice">此来源已软删除。可查看既有元数据和证据；恢复后才能编辑、创建派生内容或预览原件。</p>}
      {version && (version.media_type === 'video/mp4' || version.media_type === 'video/webm') && <VideoDetailPanel sourceId={source.id} version={version} disabled={sourceDeleted} onMessage={onMessage} />}
      <section className="management-panel">
        <header><h2>来源管理</h2><div>{!sourceDeleted && <button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => setMetadataOpen(value => !value)}>编辑元数据</button>}<button type="button" className="button text" onClick={() => void loadRevisions()}>修订记录</button></div></header>
        {!sourceDeleted && metadataOpen && <form className="form-stack compact-form" onSubmit={saveMetadata}>
          <label>标题<input required value={metadata.title} onChange={event => setMetadata(current => ({ ...current, title: event.target.value }))}/></label>
          <div className="form-row"><label>作者<input value={metadata.author} onChange={event => setMetadata(current => ({ ...current, author: event.target.value }))}/></label><label>语言<input required value={metadata.language} onChange={event => setMetadata(current => ({ ...current, language: event.target.value }))}/></label></div>
          <div className="form-row"><label>来源日期<input type="date" value={metadata.sourceDate} onChange={event => setMetadata(current => ({ ...current, sourceDate: event.target.value }))}/></label><label>标签<input value={metadata.tags} onChange={event => setMetadata(current => ({ ...current, tags: event.target.value }))} placeholder="用逗号分隔"/></label></div>
          <label>备注<textarea value={metadata.notes} onChange={event => setMetadata(current => ({ ...current, notes: event.target.value }))}/></label>
          <fieldset><legend>固定分类</legend><div className="check-grid">{categories.map(item => <label key={item[0]}><input type="checkbox" checked={metadata.categories.includes(item[0])} onChange={() => setMetadata(current => ({ ...current, categories: current.categories.includes(item[0]) ? current.categories.filter(value => value !== item[0]) : [...current.categories, item[0]] }))}/>{item[1]}</label>)}</div></fieldset>
          <div className="form-actions"><button className="button primary" disabled={Boolean(busyAction)}>{busyAction === 'metadata' ? '正在保存' : '保存修订'}</button><button type="button" className="button text" disabled={Boolean(busyAction)} onClick={() => setMetadataOpen(false)}>取消</button></div>
        </form>}
        {revisionsOpen && <div className="revision-list">{revisions.map(item => <article key={item.id}><b>修订 {item.ordinal}</b><time>{formatDate(item.created_at)}</time><span>{asText(item.snapshot.title)} · {asText(item.snapshot.author) || '未署名'} · {asText(item.snapshot.source_date) || '无来源日期'}</span></article>)}</div>}
      </section>

      <section className="document-panel">
        <header><h2>文本表示</h2><span>{representation?.kind === 'manual' ? '人工修订表示' : representation ? `${representation.parser_name} · ${representation.kind}` : '暂无表示'}</span></header>
        {source.versions.length > 1 && <label className="panel-control">内容版本<select value={version?.id || ''} onChange={event => setVersionId(event.target.value)}>{source.versions.map(item => <option key={item.id} value={item.id}>{item.original_name} · {formatDate(item.created_at)}</option>)}</select></label>}
        {representations.length > 0 && <label className="panel-control">表示<select value={representation?.id || ''} onChange={event => setRepresentationId(event.target.value)}>{representations.map(item => <option key={item.id} value={item.id}>{item.kind === 'manual' ? '人工修订' : item.kind} · {item.parser_name}</option>)}</select></label>}
        {representation ? <pre ref={textRef}><TextWithHighlight text={representation.text_content} highlight={highlightedExcerpt}/></pre> : <div className="loading">当前版本尚无可显示的本地文本。</div>}
      </section>

      {!sourceDeleted && version && <section className="manual-revision"><header><h2>人工修订</h2>{!editingRevision && <button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => { setRevisedText(representation?.text_content || ''); setEditingRevision(true) }}>新建修订表示</button>}</header>{editingRevision && <><textarea value={revisedText} disabled={Boolean(busyAction)} onChange={event => setRevisedText(event.target.value)} aria-label="人工修订文本"/><div><button type="button" className="button primary" disabled={Boolean(busyAction)} onClick={() => void saveManualRevision()}>{busyAction === 'manual-revision' ? '正在保存' : '保存新表示'}</button><button type="button" className="button text" disabled={Boolean(busyAction)} onClick={() => setEditingRevision(false)}>取消</button></div></>}</section>}

      <section className="evidence-panel">
        <header><h2>证据与引用</h2><span>{representation?.kind === 'manual' ? '人工修订来源' : '不可变 evidence'}</span></header>
        {evidence.length ? evidence.map(item => <article key={item.id}>
          <p>{item.excerpt}</p><small>{locatorLabel(item.locator)}</small>
          <div className="inline-actions"><button type="button" className="text-button" onClick={() => locateEvidence(item)}><MapPin size={15}/>定位</button>{!sourceDeleted && <button type="button" className="text-button" disabled={Boolean(busyAction)} onClick={() => void createCitation(item.id)}><Plus size={15}/>{busyAction === `citation-${item.id}` ? '正在创建' : '创建引用'}</button>}</div>
          <div className="citation-row">{(citations[item.id] || []).map(entry => <button type="button" className="citation-chip" key={entry.id} onClick={() => void loadCitation(entry.id)}>引用 {entry.id.slice(0, 8)}</button>)}</div>
        </article>) : <p className="muted">当前表示尚无可引用 evidence。</p>}
        {citation && <article className="citation-detail"><header><b>{citation.title}</b><button type="button" className="icon-button" onClick={() => setCitation(null)} title="关闭引用"><X size={15}/></button></header><p>{citation.context}</p><small>{locatorLabel(citation.locator)} · {citation.human_revised ? '人工修订表示' : '原始表示'}</small></article>}
      </section>

      {!sourceDeleted && <section className="knowledge-draft-panel">
        <header><h2>从证据创建知识</h2></header>
        <form className="form-stack compact-form" onSubmit={createKnowledge}>
          <label>知识类型<select disabled={Boolean(busyAction)} value={knowledgeKind} onChange={event => setKnowledgeKind(event.target.value)}>{knowledgeTypes.map(item => <option value={item[0]} key={item[0]}>{item[1]}</option>)}</select></label>
          <label>陈述<textarea required disabled={Boolean(busyAction)} value={knowledgeStatement} onChange={event => setKnowledgeStatement(event.target.value)}/></label>
          {evidence.length > 0 && <fieldset><legend>关联 evidence</legend><div className="evidence-checks">{evidence.map(item => <label key={item.id}><input type="checkbox" disabled={Boolean(busyAction)} checked={knowledgeEvidenceIds.includes(item.id)} onChange={() => setKnowledgeEvidenceIds(current => current.includes(item.id) ? current.filter(value => value !== item.id) : [...current, item.id])}/><span>{item.excerpt.slice(0, 100)}</span></label>)}</div></fieldset>}
          <div className="form-actions"><button className="button primary" disabled={Boolean(busyAction)}>{busyAction === 'knowledge' ? '正在创建' : '创建知识草稿'}</button></div>
        </form>
      </section>}

      {!sourceDeleted && version?.media_type === 'application/pdf' && <section className="document-panel"><header><h2>PDF 只读预览</h2><span>隔离预览</span></header><iframe title="PDF 只读预览" sandbox="allow-same-origin" src={`${API}/sources/${source.id}/original#toolbar=0&navpanes=0`} /></section>}
    </div>
    <aside className="detail-side">
      <h2>版本</h2>{source.versions.map(item => <div className="version" key={item.id}><Box size={17}/><div><b>{item.original_name}</b><small>{item.artifact_sha256.slice(0, 16)}...</small><Status value={item.completeness}/></div></div>)}
      <section className="side-section"><h2>权利确认</h2>{sourceDeleted ? <p className="muted">{labelFor(rights, source.rights)}</p> : <><select disabled={Boolean(busyAction)} value={right} onChange={event => setRight(event.target.value)}>{rights.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select><button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => void updateRights()}>{busyAction === 'rights' ? '正在更新' : '更新'}</button></>}</section>
      <section className="side-section"><h2>关系</h2>{relatedSources.length ? relatedSources.map(item => <div className="relation" key={item.id}>{labelFor(relationTypes, item.relation_type)} · {item.other?.title || item.other?.id || '已移除来源'}</div>) : <p className="muted">尚无手工关系</p>}{!sourceDeleted && <form className="side-form" onSubmit={addRelation}><select required disabled={Boolean(busyAction)} value={relatedSourceId} onChange={event => setRelatedSourceId(event.target.value)}><option value="">选择来源</option>{availableRelations.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}</select><select disabled={Boolean(busyAction)} value={relationType} onChange={event => setRelationType(event.target.value)}>{relationTypes.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select><button className="button secondary" disabled={Boolean(busyAction)}>{busyAction === 'relation' ? '正在添加' : '添加关系'}</button></form>}</section>
      <section className="side-section"><h2>主题</h2>{linkedTopics.length ? linkedTopics.map(item => <div className="relation" key={item.id}>{item.name}</div>) : <p className="muted">尚未关联主题</p>}{!sourceDeleted && <><select disabled={Boolean(busyAction)} value={topicId} onChange={event => setTopicId(event.target.value)}><option value="">选择现有主题</option>{availableTopics.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => void addTopic()}>{busyAction === 'topic' ? '正在关联' : '关联主题'}</button><form className="side-form" onSubmit={createTopic}><input disabled={Boolean(busyAction)} value={newTopic} onChange={event => setNewTopic(event.target.value)} placeholder="新主题名称"/><button className="button secondary" disabled={Boolean(busyAction)}>{busyAction === 'topic-create' ? '正在创建' : '新建主题'}</button></form></>}</section>
      <div className="danger-zone">{source.deleted_at ? <button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => void lifecycle('restore')}>{busyAction === 'lifecycle-restore' ? '正在恢复' : '恢复来源'}</button> : <button type="button" className="button danger" disabled={Boolean(busyAction)} onClick={() => void lifecycle('delete')}><Trash2 size={16}/>{busyAction === 'lifecycle-delete' ? '正在删除' : '软删除'}</button>}{source.deleted_at && <button type="button" className="button danger" disabled={Boolean(busyAction)} onClick={() => setShowPurge(true)}><Trash2 size={16}/>永久删除</button>}{showPurge && <div className="confirm"><p>将移除来源、派生数据和无引用 artifact。此操作不可撤销。</p><button type="button" className="button danger" disabled={Boolean(busyAction)} onClick={() => void lifecycle('purge')}>{busyAction === 'lifecycle-purge' ? '正在永久删除' : '确认永久删除'}</button><button type="button" className="button text" disabled={Boolean(busyAction)} onClick={() => setShowPurge(false)}>取消</button></div>}</div>
    </aside>
  </section>
}

function TextWithHighlight({ text, highlight }: { text: string; highlight: string }) {
  if (!highlight) return <>{text}</>
  const index = text.indexOf(highlight)
  if (index < 0) return <>{text}</>
  return <>{text.slice(0, index)}<mark data-evidence-highlight>{highlight}</mark>{text.slice(index + highlight.length)}</>
}

function VideoDetailPanel({
  sourceId, version, disabled, onMessage,
}: {
  sourceId: string
  version: Version
  disabled: boolean
  onMessage: (message: string) => void
}) {
  const [detail, setDetail] = useState<VideoDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedFrameId, setSelectedFrameId] = useState('')
  const playerRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const value = await request<VideoDetail>(`/videos/${sourceId}?version_id=${encodeURIComponent(version.id)}`)
        if (!active) return
        setDetail(value)
        setSelectedFrameId(current => value.analysis?.frames.some(frame => frame.id === current) ? current : '')
      } catch (error) {
        if (!active) return
        setDetail(null)
        onMessage(error instanceof Error ? error.message : '读取本地视频失败')
      } finally {
        if (active) setLoading(false)
      }
    }
    setLoading(true)
    void load()
    const timer = window.setInterval(() => { void load() }, 3500)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [onMessage, sourceId, version.id])

  const seek = (frame: VideoFrame) => {
    const player = playerRef.current
    if (!player) return
    player.currentTime = frame.time_ms / 1000
    setSelectedFrameId(frame.id)
    void player.play().catch(() => undefined)
  }
  const metadata = detail?.analysis?.metadata
  const frames = detail?.analysis?.frames || []
  const versionQuery = `?version_id=${encodeURIComponent(version.id)}`
  const streamPath = `${API}/videos/${encodeURIComponent(sourceId)}/stream${versionQuery}`

  return <section className="video-panel">
    <header><div><h2>本地视频</h2><span>{detail?.analysis ? '已完成本地分析' : loading ? '正在读取视频状态' : '等待本地分析结果'}</span></div><Status value={version.completeness}/></header>
    {!disabled && <div className="video-player-wrap"><video ref={playerRef} controls preload="metadata" src={streamPath}>当前浏览器无法播放此本地视频。</video></div>}
    {metadata ? <div className="video-metadata-grid">
      <div><label>时长</label><span>{formatDuration(metadata.duration_ms)}</span></div>
      <div><label>尺寸</label><span>{metadata.width && metadata.height ? `${metadata.width} x ${metadata.height}` : '-'}</span></div>
      <div><label>容器</label><span>{metadata.container_name || '-'}</span></div>
      <div><label>视频编码</label><span>{metadata.video_codec || '-'}</span></div>
      <div><label>音频编码</label><span>{metadata.audio_codec || '-'}</span></div>
    </div> : <p className="muted video-waiting">{detail?.media_capability.enabled === false ? '本机未检测到可用的 FFmpeg/ffprobe，视频分析作业会被阻止。' : '视频原件已保存，分析作业完成后会在这里显示媒体参数和时间采样帧。'}</p>}
    {frames.length > 0 && <section className="video-frames"><header><h3>时间采样关键帧</h3><span>{frames.length} 帧</span></header><div className="frame-strip">{frames.map(frame => <button type="button" className={selectedFrameId === frame.id ? 'video-frame selected' : 'video-frame'} key={frame.id} onClick={() => seek(frame)} title={`定位到 ${formatDuration(frame.time_ms)}`}><img src={`${API}/videos/${encodeURIComponent(sourceId)}/frames/${encodeURIComponent(frame.id)}${versionQuery}`} alt={`关键帧 ${frame.ordinal + 1}`} /><span>{formatDuration(frame.time_ms)}</span></button>)}</div></section>}
    <section className="video-ai-status"><header><h3>转写与摘要</h3><Status value={detail?.ai_capability.enabled ? 'succeeded' : 'blocked'}/></header><div><p>{detail?.ai_capability.enabled ? '媒体 AI 服务可用。' : '媒体 AI 服务尚未配置；不会上传视频或发起外部请求。'}</p><div className="video-ai-actions"><button type="button" className="button secondary" disabled>语音转写</button><button type="button" className="button secondary" disabled>内容摘要</button></div></div></section>
  </section>
}

type DownloaderCapability = { enabled: boolean; version?: string; cookie_file_available: boolean }

function VideoWorkspace({ onDone, onDoneLink, onMessage }: { onDone: () => void; onDoneLink: () => void; onMessage: (message: string) => void }) {
  const [mode, setMode] = useState<'local' | 'link'>('local')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [right, setRight] = useState('')
  const [author, setAuthor] = useState('')
  const [language, setLanguage] = useState('zh')
  const [notes, setNotes] = useState('')
  const [sourceDate, setSourceDate] = useState('')
  const [tags, setTags] = useState('')
  const [selectedCategories, setSelectedCategories] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [linkPlatform, setLinkPlatform] = useState('bilibili')
  const [linkUrl, setLinkUrl] = useState('')
  const [useCookie, setUseCookie] = useState(false)
  const [downloader, setDownloader] = useState<DownloaderCapability | null>(null)
  useEffect(() => {
    let active = true
    void request<{ downloader: DownloaderCapability }>('/capabilities')
      .then(output => { if (active) setDownloader(output.downloader) })
      .catch(() => undefined)
    return () => { active = false }
  }, [])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!right) {
      onMessage('导入前必须确认视频的权利来源')
      return
    }
    if (!file) {
      onMessage('请选择 MP4 或 WebM 视频文件')
      return
    }
    setBusy(true)
    setUploadProgress(0)
    try {
      const body = new FormData()
      body.set('file', file)
      body.set('title', title)
      body.set('rights', right)
      body.set('author', author)
      body.set('language', language)
      body.set('notes', notes)
      if (sourceDate) body.set('source_date', sourceDate)
      body.set('tags', JSON.stringify(parseTags(tags)))
      body.set('categories', JSON.stringify(selectedCategories))
      await uploadFile('/videos/local', body, setUploadProgress)
      onMessage('已写入不可变视频 artifact，并已排入本地分析作业')
      onDone()
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '视频导入失败')
    } finally {
      setBusy(false)
      setUploadProgress(null)
    }
  }
  const submitLink = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!right) {
      onMessage('下载前必须确认视频的权利来源')
      return
    }
    if (!linkUrl.trim()) {
      onMessage('请输入哔哩哔哩或抖音的 HTTPS 视频链接')
      return
    }
    setBusy(true)
    try {
      await request('/videos/link', { method: 'POST', body: JSON.stringify({
        url: linkUrl.trim(), platform: linkPlatform, rights: right, use_cookie: useCookie,
        title, author: author || null, language, notes: notes || null,
        source_date: sourceDate || null, categories: selectedCategories, tags: parseTags(tags),
      }) })
      onMessage('已提交链接下载作业，请到作业页查看进度')
      onDoneLink()
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '链接下载提交失败')
    } finally {
      setBusy(false)
    }
  }
  const downloadEnabled = downloader?.enabled === true
  const cookieAvailable = downloader?.cookie_file_available === true
  return <div className="page narrow video-workspace"><PageHeader title="视频"/>
    <div className="segmented" aria-label="视频来源"><button type="button" className={mode === 'local' ? 'selected' : ''} onClick={() => setMode('local')}>本地视频</button><button type="button" className={mode === 'link' ? 'selected' : ''} onClick={() => setMode('link')}>链接获取</button></div>
    {mode === 'local' ? <form className="form-stack" onSubmit={submit}>
      <label className="file-pick">视频文件<input className="visually-hidden" type="file" accept=".mp4,.webm,video/mp4,video/webm" onChange={event => setFile(event.target.files?.[0] || null)} /><span><Upload size={20}/>{file?.name || '选择 MP4 或 WebM'}</span></label>
      <label>标题<input value={title} onChange={event => setTitle(event.target.value)} placeholder="可留空，默认使用文件名" /></label>
      <div className="form-row"><label>权利确认<select required value={right} onChange={event => setRight(event.target.value)}><option value="" disabled>请选择</option>{rights.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label><label>语言<input required value={language} onChange={event => setLanguage(event.target.value)}/></label></div>
      <div className="form-row"><label>作者<input value={author} onChange={event => setAuthor(event.target.value)}/></label><label>来源日期<input type="date" value={sourceDate} onChange={event => setSourceDate(event.target.value)}/></label></div>
      <label>备注<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label>
      <fieldset><legend>固定分类（可多选）</legend><div className="check-grid">{categories.map(item => <label key={item[0]}><input type="checkbox" checked={selectedCategories.includes(item[0])} onChange={() => setSelectedCategories(current => current.includes(item[0]) ? current.filter(value => value !== item[0]) : [...current, item[0]])}/>{item[1]}</label>)}</div></fieldset>
      <label>自由标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label>
      {uploadProgress !== null && <div className="upload-progress" aria-live="polite"><span style={{ width: `${uploadProgress}%` }}/><small>{uploadProgress}%</small></div>}
      <button className="button primary" disabled={busy}>{busy ? '正在写入本地存储' : '确认权利并导入'}</button>
    </form> : <form className="form-stack" onSubmit={submitLink}>
      <div className="notice">联网告知：提交即向所选平台服务器发起下载请求；仅单视频、≤1080p，绝不绕过会员、付费墙或 DRM。不会预览、嗅探或解析展示。</div>
      <div className="form-row"><label>平台<select value={linkPlatform} onChange={event => setLinkPlatform(event.target.value)}><option value="bilibili">哔哩哔哩</option><option value="douyin">抖音</option></select></label><label>语言<input required value={language} onChange={event => setLanguage(event.target.value)}/></label></div>
      <label>视频链接<input required type="url" value={linkUrl} onChange={event => setLinkUrl(event.target.value)} placeholder="https://www.bilibili.com/video/... 或 https://v.douyin.com/..." /></label>
      <div className="form-row"><label>权利确认<select required value={right} onChange={event => setRight(event.target.value)}><option value="" disabled>请选择</option>{rights.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label><label>标题<input value={title} onChange={event => setTitle(event.target.value)} placeholder="可留空，默认未命名视频" /></label></div>
      <label className="check-row"><input type="checkbox" checked={useCookie} disabled={!cookieAvailable} onChange={event => setUseCookie(event.target.checked)}/>使用已导入 cookies.txt{cookieAvailable ? '' : '（尚未导入，请在设置页导入后使用）'}</label>
      <div className="form-row"><label>作者<input value={author} onChange={event => setAuthor(event.target.value)}/></label><label>来源日期<input type="date" value={sourceDate} onChange={event => setSourceDate(event.target.value)}/></label></div>
      <label>备注<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label>
      <fieldset><legend>固定分类（可多选）</legend><div className="check-grid">{categories.map(item => <label key={item[0]}><input type="checkbox" checked={selectedCategories.includes(item[0])} onChange={() => setSelectedCategories(current => current.includes(item[0]) ? current.filter(value => value !== item[0]) : [...current, item[0]])}/>{item[1]}</label>)}</div></fieldset>
      <label>自由标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label>
      <button className="button primary" disabled={busy || !downloadEnabled}>{busy ? '正在提交下载作业' : downloadEnabled ? '确认权利并提交下载' : '链接下载工具不可用（需 yt-dlp 与 FFmpeg/ffprobe）'}</button>
    </form>}
  </div>
}

function ImportPage({ onDone, onMessage }: { onDone: () => void; onMessage: (message: string) => void }) {
  const [mode, setMode] = useState<'paste' | 'file'>('paste')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [right, setRight] = useState('')
  const [author, setAuthor] = useState('')
  const [language, setLanguage] = useState('zh')
  const [notes, setNotes] = useState('')
  const [sourceDate, setSourceDate] = useState('')
  const [tags, setTags] = useState('')
  const [selectedCategories, setSelectedCategories] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!right) {
      onMessage('导入前必须确认文档或文本的权利来源')
      return
    }
    setBusy(true)
    try {
      if (mode === 'paste') {
        await request('/imports/paste', { method: 'POST', body: JSON.stringify({ title, text: content, rights: right, author: author || null, language, notes: notes || null, source_date: sourceDate || null, tags: parseTags(tags), categories: selectedCategories }) })
      } else {
        if (!file) {
          onMessage('请选择 PDF、DOCX、Markdown 或 TXT 文件')
          return
        }
        const body = new FormData()
        body.set('file', file)
        body.set('title', title)
        body.set('rights', right)
        body.set('author', author)
        body.set('language', language)
        body.set('notes', notes)
        if (sourceDate) body.set('source_date', sourceDate)
        body.set('tags', JSON.stringify(parseTags(tags)))
        body.set('categories', JSON.stringify(selectedCategories))
        setUploadProgress(0)
        await uploadFile('/imports/file', body, setUploadProgress)
      }
      onMessage('已写入不可变 artifact，并已排入本地解析作业')
      onDone()
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '导入失败')
    } finally {
      setBusy(false)
      setUploadProgress(null)
    }
  }
  return <div className="page narrow"><PageHeader title="导入"/><form className="form-stack" onSubmit={submit}>
    <div className="segmented"><button type="button" className={mode === 'paste' ? 'selected' : ''} onClick={() => setMode('paste')}>粘贴文本</button><button type="button" className={mode === 'file' ? 'selected' : ''} onClick={() => setMode('file')}>本地文件</button></div>
    <label>标题<input required={mode === 'paste'} value={title} onChange={event => setTitle(event.target.value)} placeholder={mode === 'file' ? '可留空，默认使用文件名' : '来源标题'} /></label>
    {mode === 'paste' ? <label>UTF-8 文本或 Markdown<textarea required maxLength={10 * 1024 * 1024} value={content} onChange={event => setContent(event.target.value)} placeholder="粘贴不超过 10MB 的文本" /></label> : <label className="file-pick"><Upload size={20}/><span>{file?.name || '选择 PDF、DOCX、Markdown 或 TXT'}</span><input className="visually-hidden" type="file" accept=".pdf,.docx,.md,.markdown,.txt,text/plain,application/pdf" onChange={event => setFile(event.target.files?.[0] || null)} /></label>}
    <div className="form-row"><label>权利确认<select required value={right} onChange={event => setRight(event.target.value)}><option value="" disabled>请选择</option>{rights.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label><label>语言<input required value={language} onChange={event => setLanguage(event.target.value)}/></label></div>
    <div className="form-row"><label>作者<input value={author} onChange={event => setAuthor(event.target.value)}/></label><label>来源日期<input type="date" value={sourceDate} onChange={event => setSourceDate(event.target.value)}/></label></div>
    <label>备注<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label>
    <fieldset><legend>固定分类（可多选）</legend><div className="check-grid">{categories.map(item => <label key={item[0]}><input type="checkbox" checked={selectedCategories.includes(item[0])} onChange={() => setSelectedCategories(current => current.includes(item[0]) ? current.filter(value => value !== item[0]) : [...current, item[0]])}/>{item[1]}</label>)}</div></fieldset>
    <label>自由标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label>
    {uploadProgress !== null && <div className="upload-progress" aria-live="polite"><span style={{ width: `${uploadProgress}%` }}/><small>{uploadProgress}%</small></div>}
    <button className="button primary" disabled={busy}>{busy ? '正在写入本地存储' : '确认权利并导入'}</button>
  </form></div>
}

function SearchPage({
  onSelectSource, onSelectKnowledge, onSelectCard, onMessage,
}: {
  onSelectSource: (id: string) => void
  onSelectKnowledge: (id: string) => void
  onSelectCard: (id: string) => void
  onMessage: (message: string) => void
}) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchItem[]>([])
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [includeHistorical, setIncludeHistorical] = useState(false)
  const [includeIncomplete, setIncludeIncomplete] = useState(false)
  const [sort, setSort] = useState('relevance')
  const [filters, setFilters] = useState({ source_type: '', category: '', tag: '', author: '', language: '', processing_state: '', source_date_from: '', source_date_to: '', imported_at_from: '', imported_at_to: '' })
  const search = async (event?: React.FormEvent) => {
    event?.preventDefault()
    const params = new URLSearchParams({ q: query, include_historical: String(includeHistorical), include_incomplete: String(includeIncomplete), sort })
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
    try {
      const response = await request<{ items: SearchItem[] }>(`/search?${params.toString()}`)
      setResults(response.items)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '检索失败')
    }
  }
  const open = (item: SearchItem) => {
    if (item.kind === 'source') onSelectSource(item.id)
    else if (item.kind === 'knowledge') onSelectKnowledge(item.id)
    else onSelectCard(item.id)
  }
  return <div className="page"><PageHeader title="检索"/><form className="search-command" onSubmit={search}><Search size={20}/><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="输入中文短语、关键词或任意子串"/><button className="button primary">检索</button></form>
    <div className="search-options"><label>排序<select value={sort} onChange={event => setSort(event.target.value)}><option value="relevance">相关度</option><option value="updated">导入/更新时间</option><option value="title">标题</option></select></label><div className="advanced"><button type="button" className="text-button" onClick={() => setAdvancedOpen(value => !value)}><ChevronDown size={16} className={advancedOpen ? 'turned' : ''}/>高级范围</button></div></div>
    {advancedOpen && <div className="advanced-filters">
      <div className="filter-checks"><label><input type="checkbox" checked={includeHistorical} onChange={event => setIncludeHistorical(event.target.checked)}/>包含历史版本</label><label><input type="checkbox" checked={includeIncomplete} onChange={event => setIncludeIncomplete(event.target.checked)}/>包含不完整版本</label></div>
      <div className="filter-grid"><label>来源类型<select value={filters.source_type} onChange={event => setFilters(current => ({ ...current, source_type: event.target.value }))}><option value="">全部</option><option value="file">本地文件</option><option value="paste">粘贴文本</option><option value="external">外部卡</option><option value="douyin">抖音参考</option><option value="video_link">链接视频</option></select></label><label>固定分类<select value={filters.category} onChange={event => setFilters(current => ({ ...current, category: event.target.value }))}><option value="">全部</option>{categories.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label><label>标签<input value={filters.tag} onChange={event => setFilters(current => ({ ...current, tag: event.target.value }))}/></label><label>作者<input value={filters.author} onChange={event => setFilters(current => ({ ...current, author: event.target.value }))}/></label><label>语言<input value={filters.language} onChange={event => setFilters(current => ({ ...current, language: event.target.value }))}/></label><label>处理状态<select value={filters.processing_state} onChange={event => setFilters(current => ({ ...current, processing_state: event.target.value }))}><option value="">全部</option><option value="queued">排队</option><option value="running">处理中</option><option value="succeeded">已完成</option><option value="failed">失败</option><option value="blocked">已阻止</option></select></label><label>来源日期起<input type="date" value={filters.source_date_from} onChange={event => setFilters(current => ({ ...current, source_date_from: event.target.value }))}/></label><label>来源日期止<input type="date" value={filters.source_date_to} onChange={event => setFilters(current => ({ ...current, source_date_to: event.target.value }))}/></label><label>导入日期起<input type="date" value={filters.imported_at_from} onChange={event => setFilters(current => ({ ...current, imported_at_from: event.target.value }))}/></label><label>导入日期止<input type="date" value={filters.imported_at_to} onChange={event => setFilters(current => ({ ...current, imported_at_to: event.target.value }))}/></label></div>
    </div>}
    <p className="hint">仅进行中文短语、关键词和子串匹配，不提供语义检索。</p>
    {results.length ? <div className="result-list">{results.map(item => <button type="button" key={`${item.kind}-${item.id}`} className="result" onClick={() => open(item)}><span className="result-kind">{item.kind === 'source' ? '来源' : item.kind === 'knowledge' ? '知识' : '外部卡'}</span><b>{item.title}</b><small>匹配 {item.relevance} 次 · {formatDate(item.updated_at)}</small></button>)}</div> : <Empty icon={<Search size={36}/>} text="输入条件后开始本地检索" />}
  </div>
}

function KnowledgePage({ knowledge, focusedId, onRefresh, onMessage }: { knowledge: Knowledge[]; focusedId: string | null; onRefresh: () => Promise<void>; onMessage: (message: string) => void }) {
  const [kind, setKind] = useState('unverified')
  const [statement, setStatement] = useState('')
  const [evidenceIds, setEvidenceIds] = useState('')
  const selected = knowledge.find(item => item.id === focusedId) || null
  const create = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await request('/knowledge', { method: 'POST', body: JSON.stringify({ kind, statement, evidence_ids: parseTags(evidenceIds) }) })
      setStatement('')
      setEvidenceIds('')
      await onRefresh()
      onMessage('知识草稿已创建')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '知识草稿创建失败')
    }
  }
  const publish = async (id: string) => {
    try {
      await request(`/knowledge/${id}/publish`, { method: 'POST' })
      await onRefresh()
      onMessage('知识已发布')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '知识发布失败')
    }
  }
  return <div className="page split-page"><section><PageHeader title="知识"/><form className="form-stack compact-form" onSubmit={create}><label>知识类型<select value={kind} onChange={event => setKind(event.target.value)}>{knowledgeTypes.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label><label>陈述<textarea required value={statement} onChange={event => setStatement(event.target.value)}/></label><label>evidence ID（可选，逗号分隔）<input value={evidenceIds} onChange={event => setEvidenceIds(event.target.value)}/></label><button className="button primary">创建草稿</button></form></section><section className="card-column"><h2>{selected ? '选中知识' : '知识列表'}</h2>{knowledge.length ? knowledge.map(item => <article className={item.id === selected?.id ? 'knowledge-item selected' : 'knowledge-item'} key={item.id}><div><span className="result-kind">{labelFor(knowledgeTypes, item.kind)}</span><Status value={item.status}/></div><p>{item.statement}</p><small>{item.evidence_ids.length} 条 evidence · {formatDate(item.created_at)}</small>{item.status !== 'published' && <button type="button" className="button secondary" onClick={() => void publish(item.id)}>发布</button>}</article>) : <Empty icon={<Brain size={36}/>} text="尚无知识项" />}</section></div>
}

function jobLabel(kind: string) {
  const labels: Record<string, string> = {
    parse: '本地解析',
    backup: '日常备份',
    integrity_sample: '完整性抽样校验',
    video_analyze: '本地视频分析',
    video_transcribe: '视频语音转写',
    video_summarize: '视频内容摘要',
    video_download: '链接下载',
  }
  return labels[kind] || kind
}

function JobsPage({ jobs, onRefresh, onMessage }: { jobs: Job[]; onRefresh: () => Promise<void>; onMessage: (message: string) => void }) {
  const act = async (id: string, action: 'cancel' | 'retry') => {
    try { await request(`/jobs/${id}/${action}`, { method: 'POST' }); await onRefresh() } catch (error) { onMessage(error instanceof Error ? error.message : '作业操作失败') }
  }
  return <div className="page"><PageHeader title="作业"><button type="button" className="icon-button" onClick={() => void onRefresh()} title="刷新作业"><ArchiveRestore size={18}/></button></PageHeader>{jobs.length ? <div className="job-list">{jobs.map(job => <article className="job" key={job.id}><div className="job-top"><div><b>{jobLabel(job.kind)}</b><small>{formatDate(job.created_at)} · 已尝试 {job.attempt_count} 次</small></div><Status value={job.state}/></div><div className="progress"><span style={{ width: `${job.progress}%` }} /></div><div className="job-foot"><span>{job.message || '等待本地 worker'}</span>{['queued', 'running', 'retry_wait'].includes(job.state) && <button type="button" className="icon-button" title="取消作业" onClick={() => void act(job.id, 'cancel')}><X size={17}/></button>}{['failed', 'blocked', 'cancelled'].includes(job.state) && <button type="button" className="icon-button" title="重试作业" onClick={() => void act(job.id, 'retry')}><ArchiveRestore size={17}/></button>}</div></article>)}</div> : <Empty icon={<ListChecks size={36}/>} text="没有作业记录" />}</div>
}

function ExternalCardsPage({ cards, focusedId, onDone, onMessage }: { cards: Card[]; focusedId: string | null; onDone: () => Promise<void>; onMessage: (message: string) => void }) {
  const [mode, setMode] = useState<'general' | 'douyin'>('general')
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [author, setAuthor] = useState('')
  const [notes, setNotes] = useState('')
  const [tags, setTags] = useState('')
  useEffect(() => {
    if (focusedId) document.getElementById(`external-card-${focusedId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [focusedId])
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      await request(mode === 'douyin' ? '/external/douyin' : '/external/cards', { method: 'POST', body: JSON.stringify({ title, url, author: author || null, notes: notes || null, tags: parseTags(tags) }) })
      setTitle(''); setUrl(''); setAuthor(''); setNotes(''); setTags('')
      await onDone()
      onMessage('已保存用户输入的元数据')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '保存外部卡失败')
    }
  }
  return <div className="page split-page"><section><PageHeader title="外部卡"/><form className="form-stack" onSubmit={submit}><div className="segmented"><button type="button" className={mode === 'general' ? 'selected' : ''} onClick={() => setMode('general')}>一般 URL</button><button type="button" className={mode === 'douyin' ? 'selected' : ''} onClick={() => setMode('douyin')}>抖音参考</button></div>{mode === 'douyin' && <div className="notice">抖音链接只作为外部来源卡片处理。不会下载、登录、抓取、解析、预览或伪造时间码。</div>}<label>标题<input required value={title} onChange={event => setTitle(event.target.value)} /></label><label>URL<input required type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder={mode === 'douyin' ? 'https://www.douyin.com/...' : 'https://...'} /></label><label>作者或账号<input value={author} onChange={event => setAuthor(event.target.value)} /></label><label>备注<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label><label>标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label><button className="button primary">保存元数据</button></form></section><section className="card-column"><h2>已保存参考</h2>{cards.length ? cards.map(card => <article className={card.id === focusedId ? 'external-card selected' : 'external-card'} id={`external-card-${card.id}`} key={card.id}><span className="result-kind">{card.card_type === 'douyin' ? '抖音参考' : '一般 URL'}</span><b>{card.title}</b><small>{card.author || '未署名'}{card.tags.length ? ` · ${card.tags.join('、')}` : ''}</small><p>{card.notes}</p><a href={card.url} target="_blank" rel="noreferrer" onClick={() => card.card_type === 'douyin' && onMessage('将在外部浏览器打开原始页面') }><ExternalLink size={16}/>在浏览器打开原 URL</a></article>) : <Empty icon={<Video size={36}/>} text="尚无外部卡" />}</section></div>
}

function TransfersPage({ onMessage }: { onMessage: (message: string) => void }) {
  const [backups, setBackups] = useState<Backup[]>([])
  const [targetRoot, setTargetRoot] = useState('')
  const [targetDatabaseUrl, setTargetDatabaseUrl] = useState('')
  const [archive, setArchive] = useState('')
  const [sampleSize, setSampleSize] = useState(10)
  const [backend, setBackend] = useState('')
  const [conflicts, setConflicts] = useState<string[]>([])
  const [conflictReason, setConflictReason] = useState('')
  const load = useCallback(async () => {
    try {
      const [savedBackups, health] = await Promise.all([request<Backup[]>('/backups'), request<Health>('/health')])
      setBackups(savedBackups)
      setBackend(health.database)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '读取备份失败')
    }
  }, [onMessage])
  useEffect(() => { void load() }, [load])
  const backup = async () => {
    try { await request('/backups', { method: 'POST' }); await load(); onMessage('备份已创建并完成 SHA-256 校验') } catch (error) { onMessage(error instanceof Error ? error.message : '备份失败') }
  }
  const verify = async (full: boolean) => {
    try {
      const result = await request<{ checked: number; valid: boolean; failures: string[] }>('/verify', { method: 'POST', body: JSON.stringify({ full, sample_size: sampleSize }) })
      onMessage(result.valid ? `校验通过，共检查 ${result.checked} 个 artifact` : `校验失败，共 ${result.failures.length} 个 artifact 无效`)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '完整性校验失败')
    }
  }
  const exportData = async () => {
    if (!window.confirm('导出将包含原始 artifact、派生数据和逻辑记录。确认继续？')) return
    try { await request('/exports', { method: 'POST', body: JSON.stringify({ confirmed: true }) }); onMessage('导出归档已创建并完成校验') } catch (error) { onMessage(error instanceof Error ? error.message : '导出失败') }
  }
  const restore = async (id: string) => {
    if (!targetRoot) {
      onMessage('填写一个不存在或为空的新数据根')
      return
    }
    if (backend === 'postgresql' && !targetDatabaseUrl) {
      onMessage('PostgreSQL 还原需要新的空目标数据库')
      return
    }
    try {
      const body: { target_data_root: string; target_database_url?: string } = { target_data_root: targetRoot }
      if (backend === 'postgresql') body.target_database_url = targetDatabaseUrl
      await request(`/backups/${id}/restore`, { method: 'POST', body: JSON.stringify(body) })
      setTargetDatabaseUrl('')
      onMessage('已还原到新的数据根并校验 artifact')
    } catch {
      setTargetDatabaseUrl('')
      onMessage('还原失败；请检查目标数据根和目标数据库为空且可用')
    }
  }
  const reimport = async () => {
    if (!archive) return
    setConflicts([])
    setConflictReason('')
    try {
      const output = await request<{ report: { inserted_records: number; imported_artifacts: number } }>('/reimports', { method: 'POST', body: JSON.stringify({ archive_path: archive }) })
      onMessage(`再导入完成：${output.report.inserted_records} 条记录，${output.report.imported_artifacts} 个 artifact`)
    } catch (error) {
      if (error instanceof ApiError && error.code === 'reimport_conflict') {
        setConflicts(error.conflicts)
        setConflictReason(error.reason || error.message)
        return
      }
      onMessage(error instanceof Error ? error.message : '再导入失败')
    }
  }
  return <div className="page split-page"><section><PageHeader title="备份、还原与导出"/><div className="transfer-actions"><button type="button" className="button secondary" onClick={() => void backup()}><ArchiveRestore size={17}/>立即备份</button><button type="button" className="button secondary" onClick={() => void verify(false)}><Check size={17}/>抽样校验</button><button type="button" className="button secondary" onClick={() => void verify(true)}><Check size={17}/>全量校验</button><button type="button" className="button primary" onClick={() => void exportData()}><HardDriveDownload size={17}/>确认并导出</button></div><label className="sample-field">抽样数量<input type="number" min="1" max="10000" value={sampleSize} onChange={event => setSampleSize(Math.max(1, Number(event.target.value) || 1))}/></label><h2>可还原备份</h2>{backups.length ? <div className="backup-list">{backups.map(item => <article className="backup" key={item.id}><div><b>{item.archive_name}</b><small>{formatDate(item.created_at)} · {stateLabel(item.state)}</small></div><button type="button" className="icon-button" title="还原到新数据根" onClick={() => void restore(item.id)}><ArchiveRestore size={18}/></button></article>)}</div> : <p className="muted">尚无完成的备份。</p>}</section><aside className="transfer-form"><label>新数据根（仅还原）<input value={targetRoot} onChange={event => setTargetRoot(event.target.value)} placeholder="E:\\新位置\\data" /></label>{backend === 'postgresql' && <label>新的空目标数据库 URL<input type="password" autoComplete="off" value={targetDatabaseUrl} onChange={event => setTargetDatabaseUrl(event.target.value)} placeholder="仅本次还原使用" /></label>}<p className="hint">还原不覆盖当前数据根，目标必须不存在或为空。</p><label>导出归档路径（再导入）<input value={archive} onChange={event => setArchive(event.target.value)} placeholder="E:\\...\\export-*.zip" /></label><button type="button" className="button secondary" onClick={() => void reimport()}><Import size={17}/>校验并再导入</button>{conflicts.length > 0 && <section className="conflict-list"><h2>再导入冲突</h2><p>{conflictReason}</p>{conflicts.map(item => <code key={item}>{item}</code>)}</section>}</aside></div>
}

function SettingsPage({ onMessage }: { onMessage: (message: string) => void }) {
  const [settings, setSettings] = useState<Record<string, string>>({})
  useEffect(() => { void request<Record<string, string>>('/settings').then(setSettings).catch(error => onMessage(error instanceof Error ? error.message : '读取设置失败')) }, [onMessage])
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      const result = await request<Record<string, string>>('/settings', { method: 'PUT', body: JSON.stringify({ parser_timeout_seconds: Number(settings.parser_timeout_seconds), parser_no_progress_seconds: Number(settings.parser_no_progress_seconds), parser_memory_limit_mb: Number(settings.parser_memory_limit_mb), parser_disk_limit_mb: Number(settings.parser_disk_limit_mb), video_timeout_seconds: Number(settings.video_timeout_seconds), video_memory_limit_mb: Number(settings.video_memory_limit_mb), video_disk_limit_mb: Number(settings.video_disk_limit_mb), video_max_frames: Number(settings.video_max_frames), job_lease_seconds: Number(settings.job_lease_seconds), max_retry_attempts: Number(settings.max_retry_attempts), download_timeout_seconds: Number(settings.download_timeout_seconds), download_no_progress_seconds: Number(settings.download_no_progress_seconds), download_disk_limit_mb: Number(settings.download_disk_limit_mb) }) })
      setSettings(result)
      onMessage('设置已保存到本地 state')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '保存设置失败')
    }
  }
  const importCookie = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const picked = event.target.files?.[0]
    if (!picked) return
    try {
      const body = new FormData()
      body.set('file', picked)
      await uploadFile('/settings/download-cookie', body, () => undefined)
      onMessage('已导入 cookies.txt，可在视频页链接下载时选择使用')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : 'cookies.txt 导入失败')
    } finally {
      event.target.value = ''
    }
  }
  const removeCookie = async () => {
    try {
      await request('/settings/download-cookie', { method: 'DELETE' })
      onMessage('已删除导入的 cookies.txt')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '删除失败')
    }
  }
  return <div className="page narrow"><PageHeader title="设置"/><form className="form-stack" onSubmit={save}><label>解析总超时（秒）<input type="number" min="60" max="86400" value={settings.parser_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, parser_timeout_seconds: event.target.value }))}/></label><label>无进度断路器（秒）<input type="number" min="60" max="86400" value={settings.parser_no_progress_seconds || ''} onChange={event => setSettings(current => ({ ...current, parser_no_progress_seconds: event.target.value }))}/></label><label>解析内存上限（MB）<input type="number" min="64" max="32768" value={settings.parser_memory_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, parser_memory_limit_mb: event.target.value }))}/></label><label>解析磁盘上限（MB）<input type="number" min="64" max="32768" value={settings.parser_disk_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, parser_disk_limit_mb: event.target.value }))}/></label><fieldset><legend>本地视频分析</legend><div className="settings-grid"><label>视频总超时（秒）<input type="number" min="60" max="86400" value={settings.video_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, video_timeout_seconds: event.target.value }))}/></label><label>视频内存上限（MB）<input type="number" min="64" max="32768" value={settings.video_memory_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, video_memory_limit_mb: event.target.value }))}/></label><label>视频磁盘上限（MB）<input type="number" min="64" max="32768" value={settings.video_disk_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, video_disk_limit_mb: event.target.value }))}/></label><label>最大关键帧数<input type="number" min="1" max="32" value={settings.video_max_frames || ''} onChange={event => setSettings(current => ({ ...current, video_max_frames: event.target.value }))}/></label></div></fieldset><fieldset><legend>链接下载</legend><div className="settings-grid"><label>下载总超时（秒）<input type="number" min="60" max="86400" value={settings.download_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, download_timeout_seconds: event.target.value }))}/></label><label>下载无进展观察窗口（秒）<input type="number" min="10" max="86400" value={settings.download_no_progress_seconds || ''} onChange={event => setSettings(current => ({ ...current, download_no_progress_seconds: event.target.value }))}/></label><label>下载 staging 磁盘上限（MB）<input type="number" min="64" max="32768" value={settings.download_disk_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, download_disk_limit_mb: event.target.value }))}/></label></div></fieldset><label>作业租约（秒）<input type="number" min="60" max="86400" value={settings.job_lease_seconds || ''} onChange={event => setSettings(current => ({ ...current, job_lease_seconds: event.target.value }))}/></label><label>最大重试次数<input type="number" min="0" max="10" value={settings.max_retry_attempts || ''} onChange={event => setSettings(current => ({ ...current, max_retry_attempts: event.target.value }))}/></label><button className="button primary">保存设置</button></form><section className="form-stack download-cookie"><h2>下载 Cookie（cookies.txt 单通道）</h2><div className="form-row"><label className="file-pick">导入 cookies.txt（Netscape 格式，≤1MB）<input className="visually-hidden" type="file" accept=".txt" onChange={event => void importCookie(event)} /><span><Upload size={20}/>选择 cookies.txt</span></label><button type="button" className="button secondary" onClick={() => void removeCookie()}><Trash2 size={16}/>删除已导入 Cookie</button></div><p className="hint">重复导入覆盖旧文件；Cookie 内容绝不进入数据库、日志、备份或导出；下载结束后作业内拷贝即删除。</p></section><section className="policy-list"><h2>本地运行策略</h2><div><Check size={16}/>仅绑定 127.0.0.1</div><div><Check size={16}/>无遥测、无本地 HTTPS、无加密层</div><div><Check size={16}/>解析仅本地回退，禁止静默云服务</div><div><Check size={16}/>视频分析仅限本地 MP4/WebM</div><div><Check size={16}/>链接下载仅白名单平台、单视频、≤1080p，出站经回环过滤代理</div><div><Check size={16}/>下载 Cookie 仅 cookies.txt 单通道，绝不进入备份、导出或日志</div><div><Check size={16}/>操作日志不记录正文、路径或令牌</div></section></div>
}

function Empty({ icon, text }: { icon: React.ReactNode; text: string }) { return <div className="empty">{icon}<span>{text}</span></div> }
