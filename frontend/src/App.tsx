import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, API, isAbort, request, uploadFile } from './api'
import { LatestRequestGate } from './asyncGate'
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
  domains: string[]
  genres: string[]
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
type SameWorkCandidate = { id: string; title: string; reason: 'same_artifact' | 'same_title' }
type SourceDetail = SourceSummary & { versions: Version[]; relations: Relation[]; same_work_candidates: SameWorkCandidate[] }
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
  location_action: { source_id: string; evidence_id: string }
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
  reason?: 'scene' | 'even' | 'transcript' | 'silence'
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
  analyses: { id: string; created_at: string; analyzer_name: string; config_hash: string; frame_count: number }[]
  current_analysis_id: string | null
  media_capability: { enabled: boolean }
  ai_capability: {
    enabled: boolean
    transcribe_enabled?: boolean
    understand_enabled?: boolean
    provider?: string | null
    reason?: string
    local_stt?: { enabled?: boolean; model?: string }
    video_input?: { video_input?: boolean; image_input?: boolean; provider?: string }
  }
}

type Page = 'library' | 'import' | 'search' | 'knowledge' | 'jobs' | 'external' | 'transfers' | 'settings'
type SourceScope = 'active' | 'deleted'
type TaxonomyOption = { value: string; label: string }
type Taxonomy = { domains: TaxonomyOption[]; genres: TaxonomyOption[] }
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

function formatDate(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-'
}
function formatDateOnly(value?: string | null) { return value || '-' }
function formatTimestamp(value?: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'
}
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
function representationKindLabel(kind: string) {
  const map: Record<string, string> = { extraction: '抽取', manual: '人工修订', transcription: '转写', summary: '摘要' }
  return map[kind] || kind
}
type SummarySuggestions = { domains: string[]; genres: string[]; tags: string[]; tier: number; visual_gap: boolean; video_direct: boolean; frame_fallback: boolean; enriched: boolean; applied: boolean }
function parseSummaryMarker(text: string): { body: string; suggestions: SummarySuggestions } | null {
  const match = text.match(/\s*<!--yuanzhiku:suggestions (\{[\s\S]*?\}) -->\s*$/)
  if (!match) return null
  try {
    const raw = JSON.parse(match[1]) as Partial<SummarySuggestions>
    const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
    return {
      body: text.slice(0, match.index).trimEnd(),
      suggestions: {
        domains: strings(raw.domains),
        genres: strings(raw.genres),
        tags: strings(raw.tags),
        tier: raw.tier === 2 ? 2 : raw.tier === 1.5 ? 1.5 : 1,
        visual_gap: raw.visual_gap === true,
        video_direct: raw.video_direct === true,
        frame_fallback: raw.frame_fallback === true,
        enriched: raw.enriched === true,
        applied: raw.applied === true,
      },
    }
  } catch {
    return null
  }
}
function labelFor(items: readonly (readonly [string, string])[], value?: string | null) {
  return items.find(item => item[0] === value)?.[1] || value || '-'
}
function taxonomyLabel(items: TaxonomyOption[], value: string) {
  return items.find(item => item.value === value)?.label || value
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
  const [taxonomy, setTaxonomy] = useState<Taxonomy>({ domains: [], genres: [] })
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
    // 分类体系由后端统一下发，启动时拉取一次
    request<Taxonomy>('/taxonomy').then(setTaxonomy).catch(() => undefined)
  }, [])
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
          taxonomy={taxonomy}
          onKnowledgeRefresh={loadKnowledge}
          onMessage={setMessage}
        />}
        {page === 'import' && <ImportPage taxonomy={taxonomy} onDone={() => { void refresh(); navigate('library') }} onDoneLink={() => { void refresh(); navigate('jobs') }} onDoneCard={() => { void refresh(); navigate('external') }} onMessage={setMessage} />}
        {page === 'search' && <SearchPage taxonomy={taxonomy} topics={topics} onSelectSource={openSource} onSelectKnowledge={openKnowledge} onSelectCard={openCard} onMessage={setMessage} />}
        {page === 'knowledge' && <KnowledgePage knowledge={knowledge} focusedId={focusedKnowledgeId} onRefresh={loadKnowledge} onMessage={setMessage} />}
        {page === 'jobs' && <JobsPage jobs={jobs} onRefresh={refresh} onMessage={setMessage} />}
        {page === 'external' && <ExternalCardsPage cards={cards} focusedId={focusedCardId} onMessage={setMessage} />}
        {page === 'transfers' && <TransfersPage onMessage={setMessage} />}
        {page === 'settings' && <SettingsPage onMessage={setMessage} />}
      </>}
    </main>
    {message && <Toast message={message} onClose={() => setMessage('')} />}
  </div>
}

function LibraryPage({
  sources, selected, onSelect, onClose, onRefresh, onTopicsRefresh, topics, taxonomy, onKnowledgeRefresh, onMessage,
}: {
  sources: SourceSummary[]
  selected: SourceDetail | null
  onSelect: (id: string) => void
  onClose: () => void
  onRefresh: () => Promise<void>
  onTopicsRefresh: () => Promise<void>
  topics: Topic[]
  taxonomy: Taxonomy
  onKnowledgeRefresh: () => Promise<void>
  onMessage: (message: string) => void
}) {
  const [filter, setFilter] = useState('')
  const [scope, setScope] = useState<SourceScope>('active')
  const [topicFilter, setTopicFilter] = useState('')
  const visible = useMemo(() => sources.filter(source => {
    const inScope = scope === 'deleted' ? Boolean(source.deleted_at) : !source.deleted_at
    const inTopic = !topicFilter || Boolean(topics.find(topic => topic.id === topicFilter)?.source_ids.includes(source.id))
    const needle = filter.toLowerCase()
    return inScope && inTopic && (source.title.toLowerCase().includes(needle) || source.tags.some(tag => tag.toLowerCase().includes(needle)))
  }), [filter, scope, sources, topicFilter, topics])
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
      taxonomy={taxonomy}
      onRefresh={onRefresh}
      onTopicsRefresh={onTopicsRefresh}
      onKnowledgeRefresh={onKnowledgeRefresh}
      onPurged={onClose}
      onSelect={onSelect}
      onMessage={onMessage}
    /> : <>
      <div className="toolbar library-toolbar">
        <label className="search-field"><Search size={17}/><input value={filter} onChange={event => setFilter(event.target.value)} placeholder="按标题或标签筛选" /></label>
        <select value={topicFilter} onChange={event => setTopicFilter(event.target.value)} aria-label="按主题筛选">
          <option value="">全部主题</option>
          {topics.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
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
  source, sources, topics, taxonomy, onRefresh, onTopicsRefresh, onKnowledgeRefresh, onPurged, onSelect, onMessage,
}: {
  source: SourceDetail
  sources: SourceSummary[]
  topics: Topic[]
  taxonomy: Taxonomy
  onRefresh: () => Promise<void>
  onTopicsRefresh: () => Promise<void>
  onKnowledgeRefresh: () => Promise<void>
  onPurged: () => void
  onSelect: (id: string) => void
  onMessage: (message: string) => void
}) {
  const [showPurge, setShowPurge] = useState(false)
  const [versionId, setVersionId] = useState('')
  const [representations, setRepresentations] = useState<Representation[]>([])
  const [representationId, setRepresentationId] = useState('')
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [citations, setCitations] = useState<Record<string, Citation[]>>({})
  const [citation, setCitation] = useState<CitationDetail | null>(null)
  const [citationContextOpen, setCitationContextOpen] = useState(false)
  const [editingRevision, setEditingRevision] = useState(false)
  const [revisedText, setRevisedText] = useState('')
  const [highlightedExcerpt, setHighlightedExcerpt] = useState('')
  const [metadataOpen, setMetadataOpen] = useState(false)
  const [metadata, setMetadata] = useState({ title: '', author: '', language: 'zh', notes: '', sourceDate: '', tags: '', domains: [] as string[], genres: [] as string[] })
  const [right, setRight] = useState('')
  const [revisions, setRevisions] = useState<MetadataRevision[]>([])
  const [revisionsOpen, setRevisionsOpen] = useState(false)
  const [relatedSourceId, setRelatedSourceId] = useState('')
  const [relationType, setRelationType] = useState('related_to')
  const [topicId, setTopicId] = useState('')
  const [newTopic, setNewTopic] = useState('')
  const [topicManageId, setTopicManageId] = useState('')
  const [topicRename, setTopicRename] = useState('')
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
      domains: source.domains,
      genres: source.genres,
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
  const detailGate = useRef(new LatestRequestGate())
  const loadRepresentations = useCallback(async () => {
    if (!version) {
      setRepresentations([])
      return
    }
    // 异步栅栏（加固计划 Task 10）：快速切换来源/版本时，陈旧响应不得覆盖当前选择。
    const { epoch, signal } = detailGate.current.begin(`representations:${version.id}`)
    try {
      const items = await request<Representation[]>(`/documents/${version.id}/representations`, { signal })
      if (!detailGate.current.isCurrent(`representations:${version.id}`, epoch)) return
      setRepresentations(items)
      setRepresentationId(current => items.some(item => item.id === current) ? current : (items.at(-1)?.id || ''))
    } catch (error) {
      if (isAbort(error)) return
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
    const key = `evidence:${representationId}`
    const { epoch, signal } = detailGate.current.begin(key)
    const loadEvidence = async () => {
      try {
        const items = await request<Evidence[]>(`/representations/${representationId}/evidence`, { signal })
        if (!detailGate.current.isCurrent(key, epoch)) return
        setEvidence(items)
        const groups = await Promise.all(items.map(async item => [item.id, await request<Citation[]>(`/citations?evidence_id=${encodeURIComponent(item.id)}`, { signal })] as const))
        if (!detailGate.current.isCurrent(key, epoch)) return
        setCitations(Object.fromEntries(groups))
      } catch (error) {
        if (isAbort(error)) return
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
            domains: metadata.domains,
            genres: metadata.genres,
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
  const removeRelation = async (relationId: string) => {
    await runBusyAction('relation', async () => {
      try {
        await request(`/sources/${source.id}/relations/${relationId}`, { method: 'DELETE' })
        await onRefresh()
        onMessage('来源关系已删除')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '来源关系删除失败')
      }
    })
  }
  const markSameWork = async (candidateId: string) => {
    await runBusyAction('relation', async () => {
      try {
        await request(`/sources/${source.id}/relations`, { method: 'POST', body: JSON.stringify({ related_source_id: candidateId, relation_type: 'user_declared_same_work' }) })
        await onRefresh()
        onMessage('已标记为同一作品')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '标记失败')
      }
    })
  }
  const removeTopicMembership = async (value: string) => {
    await runBusyAction('topic', async () => {
      try {
        await request(`/topics/${value}/sources/${source.id}`, { method: 'DELETE' })
        await onTopicsRefresh()
        onMessage('来源已移出主题')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '移出主题失败')
      }
    })
  }
  const renameTopic = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!topicManageId || !topicRename.trim()) return
    await runBusyAction('topic-rename', async () => {
      try {
        await request(`/topics/${topicManageId}`, { method: 'PUT', body: JSON.stringify({ name: topicRename.trim() }) })
        setTopicManageId('')
        await onTopicsRefresh()
        onMessage('主题已重命名')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '主题重命名失败')
      }
    })
  }
  const deleteTopic = async (item: Topic) => {
    if (!window.confirm(`确定删除主题“${item.name}”？其下所有来源关联将一并移除。`)) return
    await runBusyAction('topic-delete', async () => {
      try {
        await request(`/topics/${item.id}`, { method: 'DELETE' })
        setTopicManageId('')
        await onTopicsRefresh()
        onMessage('主题已删除')
      } catch (error) {
        onMessage(error instanceof Error ? error.message : '主题删除失败')
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
      setCitationContextOpen(false)
      setCitation(await request<CitationDetail>(`/citations/${citationId}`))
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '读取引用失败')
    }
  }
  const locateCitation = (detail: CitationDetail) => {
    const target = evidence.find(item => item.id === detail.location_action.evidence_id)
    if (!target) {
      onMessage('该引用的证据不在当前文本版本中，请切换到对应文本版本后定位')
      return
    }
    locateEvidence(target)
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
  const versionRelations = relatedSources.filter(item => item.relation_type === 'new_version_of' || item.relation_type === 'revision_of')
  const otherRelations = relatedSources.filter(item => !versionRelations.includes(item))
  const linkedTopics = topics.filter(item => item.source_ids.includes(source.id))
  const availableTopics = topics.filter(item => !item.source_ids.includes(source.id))
  const availableRelations = sources.filter(item => item.id !== source.id && !item.deleted_at)
  const relationRow = (item: (typeof relatedSources)[number]) => <div className="relation" key={item.id}>
    <button type="button" className="relation-link" disabled={!item.other} title={item.other ? '打开关联来源' : undefined} onClick={() => item.other && onSelect(item.other.id)}>{labelFor(relationTypes, item.relation_type)} · {item.other?.title || item.other?.id || '已移除来源'}</button>
    {!sourceDeleted && <button type="button" className="icon-button" title="删除关系" disabled={Boolean(busyAction)} onClick={() => void removeRelation(item.id)}><X size={14}/></button>}
  </div>

  return <section className="detail-layout">
    <div className="detail-main">
      <div className="metadata-grid">
        <div><label>来源类型</label><span>{sourceType(source.source_type)}</span></div>
        <div><label>处理状态</label><Status value={source.processing_state}/></div>
        <div><label>权利确认</label><span>{labelFor(rights, source.rights)}</span></div>
        <div><label>语言</label><span>{source.language}</span></div>
        <div><label>来源日期</label><span>{formatDateOnly(source.source_date)}</span></div>
        <div><label>导入时间</label><span>{formatDate(source.imported_at)}</span></div>
        <div className="wide"><label>领域</label><span>{source.domains.map(value => taxonomyLabel(taxonomy.domains, value)).join('、') || '-'}</span></div>
        <div className="wide"><label>体裁</label><span>{source.genres.map(value => taxonomyLabel(taxonomy.genres, value)).join('、') || '-'}</span></div>
        <div className="wide"><label>标签</label><span>{source.tags.join('、') || '-'}</span></div>
        <div className="wide"><label>备注</label><span>{source.notes || '-'}</span></div>
      </div>

      {sourceDeleted && <p className="deleted-notice">此来源已软删除。可查看既有元数据和证据；恢复后才能编辑、创建派生内容或预览原件。</p>}
      {!sourceDeleted && source.same_work_candidates.length > 0 && <div className="same-work-hint">
        <span>发现可能重复的来源：</span>
        {source.same_work_candidates.map(item => <span className="same-work-candidate" key={item.id}>
          {item.title}（{item.reason === 'same_artifact' ? '内容相同' : '标题相同'}）
          <button type="button" className="text-button" disabled={Boolean(busyAction)} onClick={() => void markSameWork(item.id)}>标记为同一作品</button>
        </span>)}
      </div>}
      {version && (version.media_type === 'video/mp4' || version.media_type === 'video/webm') && <VideoDetailPanel source={source} version={version} taxonomy={taxonomy} disabled={sourceDeleted} onRefresh={onRefresh} onMessage={onMessage} />}
      <section className="management-panel">
        <header><h2>来源管理</h2><div>{!sourceDeleted && <button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => setMetadataOpen(value => !value)}>编辑元数据</button>}<button type="button" className="button text" onClick={() => void loadRevisions()}>修订记录</button></div></header>
        {!sourceDeleted && metadataOpen && <form className="form-stack compact-form" onSubmit={saveMetadata}>
          <label>标题<input required value={metadata.title} onChange={event => setMetadata(current => ({ ...current, title: event.target.value }))}/></label>
          <div className="form-row"><label>作者<input value={metadata.author} onChange={event => setMetadata(current => ({ ...current, author: event.target.value }))}/></label><label>语言<input required value={metadata.language} onChange={event => setMetadata(current => ({ ...current, language: event.target.value }))}/></label></div>
          <div className="form-row"><label>来源日期<input type="date" value={metadata.sourceDate} onChange={event => setMetadata(current => ({ ...current, sourceDate: event.target.value }))}/></label><label>标签<input value={metadata.tags} onChange={event => setMetadata(current => ({ ...current, tags: event.target.value }))} placeholder="用逗号分隔"/></label></div>
          <label>备注<textarea value={metadata.notes} onChange={event => setMetadata(current => ({ ...current, notes: event.target.value }))}/></label>
          <fieldset><legend>领域（可多选）</legend><div className="check-grid">{taxonomy.domains.map(item => <label key={item.value}><input type="checkbox" checked={metadata.domains.includes(item.value)} onChange={() => setMetadata(current => ({ ...current, domains: current.domains.includes(item.value) ? current.domains.filter(value => value !== item.value) : [...current.domains, item.value] }))}/>{item.label}</label>)}</div></fieldset>
          <fieldset><legend>体裁（单选，可不选）</legend><div className="check-grid">{taxonomy.genres.map(item => <label key={item.value}><input type="radio" name="metadata-genre" checked={metadata.genres[0] === item.value} onChange={() => setMetadata(current => ({ ...current, genres: [item.value] }))}/>{item.label}</label>)}</div>{metadata.genres.length > 0 && <button type="button" className="button text" onClick={() => setMetadata(current => ({ ...current, genres: [] }))}>清除体裁</button>}</fieldset>
          <div className="form-actions"><button className="button primary" disabled={Boolean(busyAction)}>{busyAction === 'metadata' ? '正在保存' : '保存修订'}</button><button type="button" className="button text" disabled={Boolean(busyAction)} onClick={() => setMetadataOpen(false)}>取消</button></div>
        </form>}
        {revisionsOpen && <div className="revision-list">{revisions.map(item => <article key={item.id}><b>修订 {item.ordinal}</b><time>{formatDate(item.created_at)}</time><span>{asText(item.snapshot.title)} · {asText(item.snapshot.author) || '未署名'} · {asText(item.snapshot.source_date) || '无来源日期'}</span></article>)}</div>}
      </section>

      <section className="document-panel">
        <header><h2>文本版本</h2><span>{representation?.kind === 'manual' ? '人工修订稿' : representation ? `${representationKindLabel(representation.kind)} · ${representation.parser_name}` : '暂无文本'}</span></header>
        {source.versions.length > 1 && <label className="panel-control">内容版本<select value={version?.id || ''} onChange={event => setVersionId(event.target.value)}>{source.versions.map(item => <option key={item.id} value={item.id}>{item.original_name} · {formatDate(item.created_at)}</option>)}</select></label>}
        {representations.length > 0 && <label className="panel-control">文本版本（新稿在后）<select value={representation?.id || ''} onChange={event => setRepresentationId(event.target.value)}>{representations.map(item => <option key={item.id} value={item.id}>{formatTimestamp(item.created_at)} · {representationKindLabel(item.kind)}</option>)}</select></label>}
        {representation ? <pre ref={textRef}><TextWithHighlight text={representation.text_content} highlight={highlightedExcerpt}/></pre> : <div className="loading">当前版本尚无可显示的本地文本。</div>}
      </section>

      {!sourceDeleted && version && <section className="manual-revision"><header><h2>人工修订</h2>{!editingRevision && <button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => { setRevisedText(representation?.text_content || ''); setEditingRevision(true) }}>新建人工修订</button>}</header>{editingRevision && <><textarea value={revisedText} disabled={Boolean(busyAction)} onChange={event => setRevisedText(event.target.value)} aria-label="人工修订文本"/><div><button type="button" className="button primary" disabled={Boolean(busyAction)} onClick={() => void saveManualRevision()}>{busyAction === 'manual-revision' ? '正在保存' : '保存修订'}</button><button type="button" className="button text" disabled={Boolean(busyAction)} onClick={() => setEditingRevision(false)}>取消</button></div></>}</section>}

      <section className="evidence-panel">
        <header><h2>证据与引用</h2><span>{representation?.kind === 'manual' ? '来自人工修订稿' : '摘录 ≤300 字 · 完整内容见左侧文本版本'}</span></header>
        {evidence.length ? evidence.map(item => <article key={item.id}>
          <p>{item.excerpt}</p><small>{locatorLabel(item.locator)}</small>
          <div className="inline-actions"><button type="button" className="text-button" onClick={() => locateEvidence(item)}><MapPin size={15}/>定位</button>{!sourceDeleted && <button type="button" className="text-button" disabled={Boolean(busyAction)} onClick={() => void createCitation(item.id)}><Plus size={15}/>{busyAction === `citation-${item.id}` ? '正在创建' : '创建引用'}</button>}</div>
          <div className="citation-row">{(citations[item.id] || []).map(entry => <button type="button" className="citation-chip" key={entry.id} onClick={() => void loadCitation(entry.id)}>引用 {entry.id.slice(0, 8)}</button>)}</div>
        </article>) : <p className="muted">当前文本版本暂无可引用的证据摘录。</p>}
        {citation && <article className="citation-detail"><header><b>{citation.title}</b><Status value={citation.processing_state}/><button type="button" className="icon-button" onClick={() => setCitation(null)} title="关闭引用"><X size={15}/></button></header>{citationContextOpen ? <p>{citation.context}</p> : <p>{citation.context.slice(0, 80)}{citation.context.length > 80 ? '…' : ''}</p>}<div className="inline-actions">{citation.context.length > 80 && <button type="button" className="text-button" onClick={() => setCitationContextOpen(current => !current)}>{citationContextOpen ? '收起上下文' : '展开上下文'}</button>}<button type="button" className="text-button" onClick={() => locateCitation(citation)}><MapPin size={15}/>定位</button></div><small>{locatorLabel(citation.locator)} · {citation.human_revised ? '人工修订表示' : '原始表示'}</small></article>}
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

      {!sourceDeleted && version?.media_type === 'application/pdf' && <section className="document-panel"><header><h2>PDF 只读预览</h2><span>隔离预览</span><a className="text-button" href={`${API}/sources/${source.id}/original`} target="_blank" rel="noreferrer">外部打开</a></header><iframe title="PDF 只读预览" sandbox="allow-same-origin" src={`${API}/sources/${source.id}/original#toolbar=0&navpanes=0`} /></section>}
      {!sourceDeleted && version?.media_type?.startsWith('image/') && <section className="document-panel"><header><h2>图片只读预览</h2><span>本地原件</span><a className="text-button" href={`${API}/sources/${source.id}/original`} target="_blank" rel="noreferrer">外部打开</a></header><img className="image-original" src={`${API}/sources/${source.id}/original`} alt={source.title} /></section>}
    </div>
    <aside className="detail-side">
      <h2>版本</h2>{source.versions.map(item => <div className="version" key={item.id}><Box size={17}/><div><b>{item.original_name}</b><small>{item.artifact_sha256.slice(0, 16)}...</small><Status value={item.completeness}/></div></div>)}
      <section className="side-section"><h2>权利确认</h2>{sourceDeleted ? <p className="muted">{labelFor(rights, source.rights)}</p> : <><select disabled={Boolean(busyAction)} value={right} onChange={event => setRight(event.target.value)}>{rights.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select><button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => void updateRights()}>{busyAction === 'rights' ? '正在更新' : '更新'}</button></>}</section>
      <section className="side-section"><h2>关系</h2>{relatedSources.length ? <>{versionRelations.length > 0 && <h3>版本链</h3>}{versionRelations.map(relationRow)}{otherRelations.length > 0 && <h3>其他关系</h3>}{otherRelations.map(relationRow)}</> : <p className="muted">尚无手工关系</p>}{!sourceDeleted && <form className="side-form" onSubmit={addRelation}><select required disabled={Boolean(busyAction)} value={relatedSourceId} onChange={event => setRelatedSourceId(event.target.value)}><option value="">选择来源</option>{availableRelations.map(item => <option value={item.id} key={item.id}>{item.title}</option>)}</select><select disabled={Boolean(busyAction)} value={relationType} onChange={event => setRelationType(event.target.value)}>{relationTypes.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select><button className="button secondary" disabled={Boolean(busyAction)}>{busyAction === 'relation' ? '正在添加' : '添加关系'}</button></form>}</section>
      <section className="side-section"><h2>主题</h2>{linkedTopics.length ? linkedTopics.map(item => <div className="relation" key={item.id}>{topicManageId === item.id
        ? <form className="side-form" onSubmit={renameTopic}><input required disabled={Boolean(busyAction)} value={topicRename} onChange={event => setTopicRename(event.target.value)} aria-label="主题新名称"/><div className="inline-actions"><button className="button secondary" disabled={Boolean(busyAction)}>{busyAction === 'topic-rename' ? '正在保存' : '保存'}</button><button type="button" className="button text" disabled={Boolean(busyAction)} onClick={() => setTopicManageId('')}>取消</button></div></form>
        : <><span>{item.name}</span>{!sourceDeleted && <span className="inline-actions"><button type="button" className="text-button" disabled={Boolean(busyAction)} onClick={() => { setTopicManageId(item.id); setTopicRename(item.name) }}>重命名</button><button type="button" className="text-button" disabled={Boolean(busyAction)} onClick={() => void deleteTopic(item)}>删除主题</button><button type="button" className="icon-button" title="移出主题" disabled={Boolean(busyAction)} onClick={() => void removeTopicMembership(item.id)}><X size={14}/></button></span>}</>}</div>) : <p className="muted">尚未关联主题</p>}{!sourceDeleted && <><select disabled={Boolean(busyAction)} value={topicId} onChange={event => setTopicId(event.target.value)}><option value="">选择现有主题</option>{availableTopics.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button type="button" className="button secondary" disabled={Boolean(busyAction)} onClick={() => void addTopic()}>{busyAction === 'topic' ? '正在关联' : '关联主题'}</button><form className="side-form" onSubmit={createTopic}><input disabled={Boolean(busyAction)} value={newTopic} onChange={event => setNewTopic(event.target.value)} placeholder="新主题名称"/><button className="button secondary" disabled={Boolean(busyAction)}>{busyAction === 'topic-create' ? '正在创建' : '新建主题'}</button></form></>}</section>
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
  source, version, taxonomy, disabled, onRefresh, onMessage,
}: {
  source: SourceDetail
  version: Version
  taxonomy: Taxonomy
  disabled: boolean
  onRefresh: () => Promise<void>
  onMessage: (message: string) => void
}) {
  const [detail, setDetail] = useState<VideoDetail | null>(null)
  const [representations, setRepresentations] = useState<Representation[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedFrameId, setSelectedFrameId] = useState('')
  const [busy, setBusy] = useState('')
  const playerRef = useRef<HTMLVideoElement>(null)
  const sourceId = source.id

  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const value = await request<VideoDetail>(`/videos/${sourceId}?version_id=${encodeURIComponent(version.id)}`)
        if (!active) return
        setDetail(value)
        setSelectedFrameId(current => value.analysis?.frames.some(frame => frame.id === current) ? current : '')
        const items = await request<Representation[]>(`/documents/${version.id}/representations`)
        if (!active) return
        setRepresentations(items)
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
  const queueAiJob = async (kind: 'transcribe' | 'summarize' | 'analyze', forceTier2 = false) => {
    setBusy(forceTier2 ? 'force-tier2' : kind)
    try {
      await request(
        `/videos/${sourceId}/${kind}`,
        kind === 'summarize' ? { method: 'POST', body: JSON.stringify({ force_tier2: forceTier2 }) } : { method: 'POST' },
      )
      onMessage(kind === 'transcribe' ? '已排入语音转写作业' : kind === 'analyze' ? '已排入本地视频分析作业' : '已排入内容摘要作业')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '作业提交失败')
    } finally {
      setBusy('')
    }
  }
  const metadata = detail?.analysis?.metadata
  const frames = detail?.analysis?.frames || []
  const versionQuery = `?version_id=${encodeURIComponent(version.id)}`
  const streamPath = `${API}/videos/${encodeURIComponent(sourceId)}/stream${versionQuery}`
  const ai = detail?.ai_capability
  const summaryRepresentation = representations.filter(item => item.kind === 'summary').at(-1)
  const summary = summaryRepresentation ? parseSummaryMarker(summaryRepresentation.text_content) : null
  const visualRepresentation = representations.filter(item => item.kind === 'visual_understanding').at(-1)
  const suggestions = summary?.suggestions
  const hasSuggestions = Boolean(suggestions && (suggestions.domains.length || suggestions.genres.length || suggestions.tags.length))
  const adoptSuggestions = async () => {
    if (!suggestions) return
    if (!window.confirm('将把建议的领域、体裁与标签合并进当前来源元数据（不覆盖已有取值）。确认采纳？')) return
    setBusy('adopt')
    try {
      const current = await request<SourceDetail>(`/sources/${sourceId}`)
      const domains = [...new Set([...current.domains, ...suggestions.domains])].sort()
      const genres = current.genres.length ? current.genres : suggestions.genres.slice(0, 1)
      const tags = [...new Set([...current.tags, ...suggestions.tags])].sort()
      await request(`/sources/${sourceId}/metadata`, { method: 'PUT', body: JSON.stringify({ domains, genres, tags }) })
      await onRefresh()
      onMessage('已采纳建议')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '采纳建议失败')
    } finally {
      setBusy('')
    }
  }

  return <section className="video-panel">
    <header><div><h2>本地视频</h2><span>{detail?.analysis ? '已提取本地媒体信息（不含 AI 理解）' : loading ? '正在读取视频状态' : '等待本地分析结果'}</span></div><Status value={version.completeness}/></header>
    {detail && detail.analyses.length > 1 && <p className="muted">本结果基于 {detail.analyses.length} 份分析中的最新一份</p>}
    {!disabled && <div className="video-player-wrap"><video ref={playerRef} controls preload="metadata" src={streamPath}>当前浏览器无法播放此本地视频。</video></div>}
    {metadata ? <div className="video-metadata-grid">
      <div><label>时长</label><span>{formatDuration(metadata.duration_ms)}</span></div>
      <div><label>尺寸</label><span>{metadata.width && metadata.height ? `${metadata.width} x ${metadata.height}` : '-'}</span></div>
      <div><label>容器</label><span>{metadata.container_name || '-'}</span></div>
      <div><label>视频编码</label><span>{metadata.video_codec || '-'}</span></div>
      <div><label>音频编码</label><span>{metadata.audio_codec || '-'}</span></div>
    </div> : <p className="muted video-waiting">{detail?.media_capability.enabled === false ? '本机未检测到可用的 FFmpeg/ffprobe，视频分析作业会被阻止。' : '视频原件已保存，分析作业完成后会在这里显示媒体参数和时间采样帧。'}</p>}
    {frames.length > 0 && <section className="video-frames"><header><h3>时间采样关键帧</h3><span>{frames.length} 帧</span><span className="muted">场景切换 + 转写语义锚点 + 等间隔抽样 · 帧仅供人工浏览，AI 画面理解结果见下方摘要区（可在设置开启增强）</span></header><div className="frame-strip">{frames.map(frame => <button type="button" className={selectedFrameId === frame.id ? 'video-frame selected' : 'video-frame'} key={frame.id} onClick={() => seek(frame)} title={`${frameReasonLabels[frame.reason ?? 'even']} · 定位到 ${formatDuration(frame.time_ms)}`}><img src={`${API}/videos/${encodeURIComponent(sourceId)}/frames/${encodeURIComponent(frame.id)}${versionQuery}`} alt={`关键帧 ${frame.ordinal + 1}`} /><span>{formatDuration(frame.time_ms)}</span></button>)}</div></section>}
    <section className="video-ai-status"><header><h3>转写与摘要</h3><Status value={(ai?.enabled || ai?.local_stt?.enabled) ? 'succeeded' : 'blocked'}/></header><div><p>{ai?.enabled || ai?.local_stt?.enabled ? (ai?.local_stt?.enabled ? '本地转写模型已就绪；语音转写默认在本机完成，无需上传。' : '媒体 AI 服务已配置；音频与文本将发送至你配置的云端服务处理。') : '转写与摘要均未配置；不会上传视频或发起外部请求。'}</p><div className="video-ai-actions"><button type="button" className="button secondary" disabled={disabled || !(ai?.transcribe_enabled || ai?.local_stt?.enabled) || Boolean(busy)} onClick={() => void queueAiJob('transcribe')}>{busy === 'transcribe' ? '正在提交' : '语音转写'}</button><button type="button" className="button secondary" disabled={disabled || !ai?.understand_enabled || Boolean(busy)} onClick={() => void queueAiJob('summarize')}>{busy === 'summarize' ? '正在提交' : '内容摘要'}</button><button type="button" className="button secondary" disabled={disabled || Boolean(busy)} title="重新执行本地关键帧分析：转写完成后重分析可让采样锚点融合转写语义（生成一份新分析，历史分析保留）" onClick={() => void queueAiJob('analyze')}>{busy === 'analyze' ? '正在提交' : '重新分析'}</button></div></div>
      {summary && suggestions && <div className="video-summary"><header><h4>内容摘要</h4><span className="tier-badge">{suggestions.tier === 2 ? '深度' : suggestions.tier === 1.5 ? '标准+画面' : '标准'}</span>{suggestions.visual_gap && <span className="muted">可能缺少画面信息</span>}{suggestions.video_direct && <span className="muted">已直送视频补充理解</span>}{suggestions.frame_fallback && <span className="muted">直送不可行，已按关键帧补充画面理解</span>}{suggestions.enriched && <span className="muted">画面理解增强（关键帧）</span>}</header><p className="summary-text">{summary.body}</p>{hasSuggestions && <p className="muted">建议：领域 {suggestions.domains.map(value => taxonomyLabel(taxonomy.domains, value)).join('、') || '-'} · 体裁 {suggestions.genres.map(value => taxonomyLabel(taxonomy.genres, value)).join('、') || '-'} · 标签 {suggestions.tags.join('、') || '-'}</p>}<div className="video-ai-actions"><button type="button" className="button secondary" disabled={disabled || !ai?.video_input?.video_input || Boolean(busy)} title={ai?.video_input?.video_input ? '直送视频给多模态模型补充理解后重新生成摘要' : '在设置中配置视频直送后可用'} onClick={() => void queueAiJob('summarize', true)}>{busy === 'force-tier2' ? '正在提交' : '强制深度理解'}</button>{hasSuggestions && (suggestions.applied ? <span className="muted">建议已自动写入（仅填空缺），可在编辑元数据中修改</span> : <button type="button" className="button secondary" disabled={disabled || Boolean(busy)} onClick={() => void adoptSuggestions()}>{busy === 'adopt' ? '正在采纳' : '采纳建议'}</button>)}</div></div>}
      {visualRepresentation && <div className="video-summary"><header><h4>画面理解（关键帧联络表）</h4><span className="muted">按时间窗定位，来源为缩略图网格单次理解</span></header><p className="summary-text">{visualRepresentation.text_content}</p></div>}
    </section>
  </section>
}

type DownloaderCapability = { enabled: boolean; version?: string; cookies: Record<string, boolean> }

type PrefillResult = { title?: string | null; author?: string | null; language?: string | null; source_date?: string | null }

type DetectedKind = 'text' | 'document' | 'image' | 'video' | 'link' | 'card'
const frameReasonLabels: Record<string, string> = { scene: '场景切换', transcript: '转写语义', silence: '静音空档', even: '等距' }
const fileKinds: Record<string, 'document' | 'image' | 'video'> = {
  pdf: 'document', docx: 'document', md: 'document', markdown: 'document', txt: 'document',
  jpg: 'image', jpeg: 'image', png: 'image', webp: 'image',
  mp4: 'video', webm: 'video',
}
function fileKind(name: string) {
  return fileKinds[name.toLowerCase().split('.').pop() || ''] || null
}
function linkPlatformOf(url: string): 'bilibili' | 'douyin' | null {
  try {
    const host = new URL(url).hostname.toLowerCase()
    if (host === 'bilibili.com' || host.endsWith('.bilibili.com') || host === 'b23.tv' || host.endsWith('.b23.tv')) return 'bilibili'
    if (host === 'douyin.com' || host.endsWith('.douyin.com')) return 'douyin'
  } catch { /* 不是合法 URL */ }
  return null
}
function extractUrl(text: string): string {
  // 从混合文本（如抖音分享口令：前缀码 + 文案 + 话题 + 短链 + 引导语）中提取首个 URL，修剪末尾常见标点。
  const match = text.match(/https?:\/\/\S+/)
  if (!match) return ''
  return match[0].replace(/[，。！？；、）】」』"'<>.,;!?]+$/, '')
}
function shareNotesOf(text: string, url: string): string {
  // 分享口令提取链接后的剩余文案作备注参考：去掉链接、首个中文字符前的口令前缀、开头引导语与结尾口令码段。
  return text.replace(url, ' ')
    .replace(/复制此链接.{0,20}$/g, '')
    .replace(/^[!-~\s]+(?=[一-鿿])/, '')
    .replace(/^复制打开抖音[，,]?\s*/, '')
    .replace(/(\s+[A-Za-z0-9@.:/_-]+){2,}\s*$/, '')
    .replace(/\s+/g, ' ').trim().slice(0, 4000)
}

function ImportPage({ taxonomy, onDone, onDoneLink, onDoneCard, onMessage }: {
  taxonomy: Taxonomy
  onDone: () => void
  onDoneLink: () => void
  onDoneCard: () => void
  onMessage: (message: string) => void
}) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [right, setRight] = useState('')
  const [author, setAuthor] = useState('')
  const [language, setLanguage] = useState('zh')
  const [notes, setNotes] = useState('')
  const [sourceDate, setSourceDate] = useState('')
  const [tags, setTags] = useState('')
  const [selectedDomains, setSelectedDomains] = useState<string[]>([])
  const [selectedGenre, setSelectedGenre] = useState('')
  const [taxonomyOpen, setTaxonomyOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [asCard, setAsCard] = useState(false)
  const [useCookie, setUseCookie] = useState(false)
  const [downloader, setDownloader] = useState<DownloaderCapability | null>(null)
  const [probing, setProbing] = useState(false)
  const touched = useRef<Set<string>>(new Set())
  const markTouched = (field: string) => { touched.current.add(field) }
  useEffect(() => {
    let active = true
    void request<{ downloader: DownloaderCapability }>('/capabilities')
      .then(output => { if (active) setDownloader(output.downloader) })
      .catch(() => undefined)
    return () => { active = false }
  }, [])
  const applyPrefill = (output: PrefillResult) => {
    if (output.title && !title && !touched.current.has('title')) setTitle(output.title)
    if (output.author && !author && !touched.current.has('author')) setAuthor(output.author)
    if (output.language && language === 'zh' && !touched.current.has('language')) setLanguage(output.language)
    if (output.source_date && !sourceDate && !touched.current.has('sourceDate')) setSourceDate(output.source_date)
  }
  const prefill = async (body: FormData) => {
    try {
      applyPrefill(await request<PrefillResult>('/imports/prefill', { method: 'POST', body }))
    } catch { /* 预填失败不阻断表单 */ }
  }
  const prefillFile = (selected: File) => {
    const body = new FormData()
    body.set('file', selected)
    void prefill(body)
  }
  const text = content.trim()
  const foundUrl = !file && text ? extractUrl(text) : ''
  const bareUrl = foundUrl && text === foundUrl ? foundUrl : ''
  const platform = foundUrl ? linkPlatformOf(foundUrl) : null
  // 平台链接（裸链或分享口令混合文本）进链接下载流程；仅单独一条普通 URL 进外部卡；其余为文本
  const detected: DetectedKind | null = file ? fileKind(file.name) : !text ? null : platform ? (asCard ? 'card' : 'link') : bareUrl ? 'card' : 'text'
  const douyinCard = detected === 'card' && platform === 'douyin'
  const downloadEnabled = downloader?.enabled === true
  const platformName = platform === 'douyin' ? '抖音' : '哔哩哔哩'
  const cookieAvailable = detected === 'link' && platform !== null && downloader?.cookies?.[platform] === true
  const prefillText = () => {
    if (file || !text || platform || bareUrl) return
    const body = new FormData()
    body.set('text', content)
    void prefill(body)
  }
  const pickFile = (selected: File | null) => {
    if (!selected) return
    const kind = fileKind(selected.name)
    if (!kind) {
      onMessage('不支持的文件类型')
      return
    }
    setFile(selected)
    if (kind === 'video') {
      if (!title && !touched.current.has('title')) setTitle(selected.name.replace(/\.[^.]+$/, ''))
    } else {
      prefillFile(selected)
    }
  }
  const probeLink = async () => {
    if (!foundUrl || !platform || probing) return
    setProbing(true)
    try {
      const output = await request<PrefillResult>('/videos/link/probe', { method: 'POST', body: JSON.stringify({ url: foundUrl, platform, use_cookie: useCookie }) })
      if (output.title && !title && !touched.current.has('title')) setTitle(output.title)
      if (output.author && !author && !touched.current.has('author')) setAuthor(output.author)
      if (output.source_date && !sourceDate && !touched.current.has('sourceDate')) setSourceDate(output.source_date)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '链接识别失败')
    } finally {
      setProbing(false)
    }
  }
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!detected) {
      onMessage('请先粘贴文本或链接，或选择文件')
      return
    }
    setBusy(true)
    try {
      if (detected === 'card') {
        await request(douyinCard ? '/external/douyin' : '/external/cards', { method: 'POST', body: JSON.stringify({ title, url: foundUrl, author: author || null, notes: notes || null, tags: parseTags(tags) }) })
        onMessage('已保存用户输入的元数据')
        onDoneCard()
        return
      }
      if (!right) {
        onMessage(detected === 'link' ? '下载前必须确认视频的权利来源' : detected === 'video' ? '导入前必须确认视频的权利来源' : '导入前必须确认文档或文本的权利来源')
        return
      }
      if (detected === 'text') {
        await request('/imports/paste', { method: 'POST', body: JSON.stringify({ title, text: content, rights: right, author: author || null, language, notes: notes || null, source_date: sourceDate || null, tags: parseTags(tags), domains: selectedDomains, genres: selectedGenre ? [selectedGenre] : [] }) })
        onMessage('已写入不可变 artifact，并已排入本地解析作业')
      } else if (detected === 'link') {
        await request('/videos/link', { method: 'POST', body: JSON.stringify({
          url: foundUrl, platform, rights: right, use_cookie: useCookie,
          title, author: author || null, language, notes: notes || null,
          source_date: sourceDate || null, domains: selectedDomains, genres: selectedGenre ? [selectedGenre] : [], tags: parseTags(tags),
        }) })
        onMessage('已提交链接下载作业，请到作业页查看进度')
        onDoneLink()
        return
      } else if (file) {
        const body = new FormData()
        body.set('file', file)
        body.set('title', title)
        body.set('rights', right)
        body.set('author', author)
        body.set('language', language)
        body.set('notes', notes)
        if (sourceDate) body.set('source_date', sourceDate)
        body.set('tags', JSON.stringify(parseTags(tags)))
        body.set('domains', JSON.stringify(selectedDomains))
        body.set('genres', JSON.stringify(selectedGenre ? [selectedGenre] : []))
        setUploadProgress(0)
        await uploadFile(detected === 'image' ? '/imports/image' : detected === 'video' ? '/videos/local' : '/imports/file', body, setUploadProgress)
        onMessage(detected === 'video' ? '已写入不可变视频 artifact，并已排入本地分析作业' : '已写入不可变 artifact，并已排入本地解析作业')
      }
      onDone()
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '导入失败')
    } finally {
      setBusy(false)
      setUploadProgress(null)
    }
  }
  const badgeLabel: Record<DetectedKind, string> = { text: '文本', document: '文档', image: '图片', video: '视频', link: '视频链接下载', card: '外部卡' }
  const badgeHint: Record<DetectedKind, string> = {
    text: '识别为粘贴文本，导入后在本地解析',
    document: '识别为文档，导入后在本地解析',
    image: '识别为图片，导入后在本地 OCR 解析',
    video: '识别为本地视频，导入后在本地分析',
    link: `识别为${platform === 'douyin' ? '抖音' : '哔哩哔哩'}视频链接，可提交下载`,
    card: '识别为外部链接，只保存元数据卡片，不下载内容',
  }
  const submitLabel = busy
    ? detected === 'link' ? '正在提交下载作业' : detected === 'card' ? '正在保存' : '正在写入本地存储'
    : detected === 'text' ? '导入文本'
    : detected === 'document' ? '导入文档'
    : detected === 'image' ? '导入图片'
    : detected === 'video' ? '导入视频'
    : detected === 'link' ? downloadEnabled ? '确认权利并提交下载' : '链接下载工具不可用（需 yt-dlp 与 FFmpeg/ffprobe）'
    : detected === 'card' ? '保存外部卡'
    : '导入'
  return <div className="page narrow"><PageHeader title="导入"/><form className="form-stack" onSubmit={submit}>
    <label>文本或链接<textarea maxLength={10 * 1024 * 1024} value={content} onChange={event => {
      const value = event.target.value
      setContent(value)
      setAsCard(false)
      // 分享口令混合文本：提取出平台链接时，把剩余文案带入备注（未填写时）
      const pasted = value.trim()
      const url = pasted ? extractUrl(pasted) : ''
      if (url && pasted !== url && linkPlatformOf(url) && !notes) {
        const cleaned = shareNotesOf(pasted, url)
        if (cleaned) setNotes(cleaned)
      }
    }} onBlur={prefillText} placeholder="粘贴文本、视频链接或网页链接（支持抖音分享口令整段粘贴）" /></label>
    <div className="file-row">
      <label className="button secondary"><Upload size={16}/>选择文件<input className="visually-hidden" type="file" accept=".pdf,.docx,.md,.markdown,.txt,.jpg,.jpeg,.png,.webp,.mp4,.webm,application/pdf,text/plain,image/jpeg,image/png,image/webp,video/mp4,video/webm" onChange={event => { pickFile(event.target.files?.[0] || null); event.target.value = '' }} /></label>
      {file && <span className="file-chip"><FileText size={14}/><span>{file.name}</span><button type="button" className="button text" onClick={() => setFile(null)}>清除文件</button></span>}
    </div>
    {file && text && <p className="hint">已选择文件，将按文件导入，文本不参与。</p>}
    {detected && <div className="detect-row"><span className="detect-badge">{badgeLabel[detected]}</span><span className="hint">{badgeHint[detected]}</span></div>}
    {detected === 'link' && <>
      <label>视频链接<input readOnly value={foundUrl} /></label>
      <div className="notice">联网告知：提交即向所选平台服务器发起下载请求；仅单视频、≤1080p，绝不绕过会员、付费墙或 DRM。不会预览、嗅探或解析展示。</div>
      <div className="link-tools">
        <button type="button" className="button secondary" disabled={probing} onClick={() => void probeLink()}>{probing ? '正在识别' : '识别链接'}</button>
        <label className="check-row"><input type="checkbox" checked={useCookie} disabled={!cookieAvailable} onChange={event => setUseCookie(event.target.checked)}/>使用已导入的{platformName} Cookie{cookieAvailable ? '' : `（尚未导入${platformName} Cookie，请在设置页导入后使用）`}</label>
        <button type="button" className="text-button" onClick={() => setAsCard(true)}>仅保存为外部卡</button>
      </div>
    </>}
    {detected === 'card' && douyinCard && <div className="notice">抖音链接只作为外部来源卡片处理。不会下载、登录、抓取、解析、预览或伪造时间码。</div>}
    {detected === 'card' && platform && <div><button type="button" className="text-button" onClick={() => setAsCard(false)}>改为链接下载</button></div>}
    {detected === 'card' ? <>
      <label>标题<input required value={title} onChange={event => { markTouched('title'); setTitle(event.target.value) }} /></label>
      <label>URL<input readOnly value={foundUrl} /></label>
      <label>作者或账号<input value={author} onChange={event => { markTouched('author'); setAuthor(event.target.value) }} /></label>
      <label>备注<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label>
      <label>自由标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label>
    </> : detected && <>
      <label>标题<input required={detected === 'text'} value={title} onChange={event => { markTouched('title'); setTitle(event.target.value) }} placeholder={detected === 'text' ? '来源标题' : detected === 'link' ? '可留空，默认使用平台标题' : '可留空，默认使用文件名'} /></label>
      <div className="form-row"><label>作者<input value={author} onChange={event => { markTouched('author'); setAuthor(event.target.value) }} /></label><label>语言<input required value={language} onChange={event => { markTouched('language'); setLanguage(event.target.value) }}/></label></div>
      <div className="form-row"><label>来源日期<input type="date" value={sourceDate} onChange={event => { markTouched('sourceDate'); setSourceDate(event.target.value) }}/></label><label>自由标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label></div>
      <label>备注<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label>
      <div className="advanced"><button type="button" className="text-button" onClick={() => setTaxonomyOpen(value => !value)}><ChevronDown size={16} className={taxonomyOpen ? 'turned' : ''}/>手工分类（可跳过，AI 可自动分类）</button></div>
      {taxonomyOpen && <>
      <fieldset><legend>领域（可多选）</legend><div className="check-grid">{taxonomy.domains.map(item => <label key={item.value}><input type="checkbox" checked={selectedDomains.includes(item.value)} onChange={() => setSelectedDomains(current => current.includes(item.value) ? current.filter(value => value !== item.value) : [...current, item.value])}/>{item.label}</label>)}</div></fieldset>
      <fieldset><legend>体裁（单选，可不选）</legend><div className="check-grid">{taxonomy.genres.map(item => <label key={item.value}><input type="radio" name="import-genre" checked={selectedGenre === item.value} onChange={() => setSelectedGenre(item.value)}/>{item.label}</label>)}</div>{selectedGenre && <button type="button" className="button text" onClick={() => setSelectedGenre('')}>清除体裁</button>}</fieldset>
      </>}
      <label>权利确认<select required value={right} onChange={event => setRight(event.target.value)}><option value="" disabled>请选择</option>{rights.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label>
    </>}
    {uploadProgress !== null && <div className="upload-progress" aria-live="polite"><span style={{ width: `${uploadProgress}%` }}/><small>{uploadProgress}%</small></div>}
    <button className="button primary" disabled={busy || !detected || (detected === 'link' && !downloadEnabled)}>{submitLabel}</button>
  </form></div>
}

function SearchPage({
  taxonomy, topics, onSelectSource, onSelectKnowledge, onSelectCard, onMessage,
}: {
  taxonomy: Taxonomy
  topics: Topic[]
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
  const [filters, setFilters] = useState({ source_type: '', genre: '', tag: '', author: '', language: '', processing_state: '', source_date_from: '', source_date_to: '', imported_at_from: '', imported_at_to: '', topic_id: '' })
  const [domainFilters, setDomainFilters] = useState<string[]>([])
  const searchGate = useRef(new LatestRequestGate())
  const search = async (event?: React.FormEvent) => {
    event?.preventDefault()
    const params = new URLSearchParams({ q: query, include_historical: String(includeHistorical), include_incomplete: String(includeIncomplete), sort })
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
    for (const value of domainFilters) params.append('domains', value)
    // 异步栅栏（加固计划 Task 10）：仅最新一次检索落地，慢的旧响应被丢弃。
    const { epoch, signal } = searchGate.current.begin('search')
    try {
      const response = await request<{ items: SearchItem[] }>(`/search?${params.toString()}`, { signal })
      if (!searchGate.current.isCurrent('search', epoch)) return
      setResults(response.items)
    } catch (error) {
      if (isAbort(error)) return
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
      <div className="filter-grid"><label>主题<select value={filters.topic_id} onChange={event => setFilters(current => ({ ...current, topic_id: event.target.value }))}><option value="">全部</option>{topics.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>来源类型<select value={filters.source_type} onChange={event => setFilters(current => ({ ...current, source_type: event.target.value }))}><option value="">全部</option><option value="file">本地文件</option><option value="paste">粘贴文本</option><option value="external">外部卡</option><option value="douyin">抖音参考</option><option value="video_link">链接视频</option></select></label><label>体裁<select value={filters.genre} onChange={event => setFilters(current => ({ ...current, genre: event.target.value }))}><option value="">全部</option><option value="_none">未分类</option>{taxonomy.genres.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><label>标签<input value={filters.tag} onChange={event => setFilters(current => ({ ...current, tag: event.target.value }))}/></label><label>作者<input value={filters.author} onChange={event => setFilters(current => ({ ...current, author: event.target.value }))}/></label><label>语言<input value={filters.language} onChange={event => setFilters(current => ({ ...current, language: event.target.value }))}/></label><label>处理状态<select value={filters.processing_state} onChange={event => setFilters(current => ({ ...current, processing_state: event.target.value }))}><option value="">全部</option><option value="queued">排队</option><option value="running">处理中</option><option value="succeeded">已完成</option><option value="failed">失败</option><option value="blocked">已阻止</option></select></label><label>来源日期起<input type="date" value={filters.source_date_from} onChange={event => setFilters(current => ({ ...current, source_date_from: event.target.value }))}/></label><label>来源日期止<input type="date" value={filters.source_date_to} onChange={event => setFilters(current => ({ ...current, source_date_to: event.target.value }))}/></label><label>导入日期起<input type="date" value={filters.imported_at_from} onChange={event => setFilters(current => ({ ...current, imported_at_from: event.target.value }))}/></label><label>导入日期止<input type="date" value={filters.imported_at_to} onChange={event => setFilters(current => ({ ...current, imported_at_to: event.target.value }))}/></label></div>
      <fieldset className="filter-domains"><legend>领域（可多选）</legend><div className="check-grid">{taxonomy.domains.map(item => <label key={item.value}><input type="checkbox" checked={domainFilters.includes(item.value)} onChange={() => setDomainFilters(current => current.includes(item.value) ? current.filter(value => value !== item.value) : [...current, item.value])}/>{item.label}</label>)}<label><input type="checkbox" checked={domainFilters.includes('_none')} onChange={() => setDomainFilters(current => current.includes('_none') ? current.filter(value => value !== '_none') : [...current, '_none'])}/>未分类</label></div></fieldset>
    </div>}
    <p className="hint">仅进行中文短语、关键词和子串匹配，不提供语义检索。</p>
    {results.length ? <div className="result-list">{results.map(item => <button type="button" key={`${item.kind}-${item.id}`} className="result" onClick={() => open(item)}><span className="result-kind">{item.kind === 'source' ? '来源' : item.kind === 'knowledge' ? '知识' : '外部卡'}</span><b>{item.title}</b><small>匹配 {item.relevance} 次 · {formatDate(item.updated_at)}</small></button>)}</div> : <Empty icon={<Search size={36}/>} text="输入条件后开始本地检索" />}
  </div>
}

function KnowledgePage({ knowledge, focusedId, onRefresh, onMessage }: { knowledge: Knowledge[]; focusedId: string | null; onRefresh: () => Promise<void>; onMessage: (message: string) => void }) {
  const [kind, setKind] = useState('unverified')
  const [statement, setStatement] = useState('')
  const [evidenceIds, setEvidenceIds] = useState('')
  const [creating, setCreating] = useState(false)
  const [publishingId, setPublishingId] = useState<string | null>(null)
  const selected = knowledge.find(item => item.id === focusedId) || null
  const create = async (event: React.FormEvent) => {
    event.preventDefault()
    if (creating) return
    setCreating(true)
    try {
      await request('/knowledge', { method: 'POST', body: JSON.stringify({ kind, statement, evidence_ids: parseTags(evidenceIds) }) })
      setStatement('')
      setEvidenceIds('')
      await onRefresh()
      onMessage('知识草稿已创建')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '知识草稿创建失败')
    } finally {
      setCreating(false)
    }
  }
  const publish = async (id: string) => {
    if (publishingId) return
    setPublishingId(id)
    try {
      await request(`/knowledge/${id}/publish`, { method: 'POST' })
      await onRefresh()
      onMessage('知识已发布')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '知识发布失败')
    } finally {
      setPublishingId(null)
    }
  }
  return <div className="page split-page"><section><PageHeader title="知识"/><form className="form-stack compact-form" onSubmit={create}><label>知识类型<select value={kind} onChange={event => setKind(event.target.value)}>{knowledgeTypes.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label><label>陈述<textarea required value={statement} onChange={event => setStatement(event.target.value)}/></label><label>证据摘录 ID（可选，逗号分隔）<input value={evidenceIds} onChange={event => setEvidenceIds(event.target.value)}/></label><button className="button primary" disabled={creating}>{creating ? '正在创建' : '创建草稿'}</button></form></section><section className="card-column"><h2>{selected ? '选中知识' : '知识列表'}</h2>{knowledge.length ? knowledge.map(item => <article className={item.id === selected?.id ? 'knowledge-item selected' : 'knowledge-item'} key={item.id}><div><span className="result-kind">{labelFor(knowledgeTypes, item.kind)}</span><Status value={item.status}/></div><p>{item.statement}</p><small>{item.evidence_ids.length} 条证据摘录 · {formatDate(item.created_at)}</small>{item.status !== 'published' && <button type="button" className="button secondary" disabled={publishingId === item.id} onClick={() => void publish(item.id)}>{publishingId === item.id ? '正在发布' : '发布'}</button>}</article>) : <Empty icon={<Brain size={36}/>} text="尚无知识项" />}</section></div>
}

function jobLabel(kind: string) {
  const labels: Record<string, string> = {
    parse: '本地解析',
    backup: '日常备份',
    integrity_sample: '完整性抽样校验',
    video_analyze: '本地媒体信息提取',
    video_transcribe: '视频语音转写',
    video_summarize: '视频内容摘要',
    video_download: '链接下载',
    source_classify: 'AI 分类',
    artifact_cleanup: 'artifact 清理重试',
  }
  return labels[kind] || kind
}

function JobsPage({ jobs, onRefresh, onMessage }: { jobs: Job[]; onRefresh: () => Promise<void>; onMessage: (message: string) => void }) {
  const [selected, setSelected] = useState<string[]>([])
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const act = async (id: string, action: 'cancel' | 'retry') => {
    try { await request(`/jobs/${id}/${action}`, { method: 'POST' }); await onRefresh() } catch (error) { onMessage(error instanceof Error ? error.message : '作业操作失败') }
  }
  const toggle = (id: string) => {
    setConfirming(false)
    setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])
  }
  const selectable = jobs.filter(job => job.state !== 'running')
  const allSelected = selectable.length > 0 && selectable.every(job => selected.includes(job.id))
  const toggleAll = () => {
    setConfirming(false)
    setSelected(allSelected ? [] : selectable.map(job => job.id))
  }
  const removeSelected = async () => {
    if (!confirming) { setConfirming(true); return }
    setBusy(true)
    try {
      await request('/jobs/delete', { method: 'POST', body: JSON.stringify({ job_ids: selected }) })
      setSelected([])
      setConfirming(false)
      await onRefresh()
      onMessage('已删除所选作业记录')
    } catch (error) {
      setConfirming(false)
      onMessage(error instanceof Error ? error.message : '作业删除失败')
    } finally {
      setBusy(false)
    }
  }
  return <div className="page"><PageHeader title="作业"><button type="button" className="icon-button" onClick={() => void onRefresh()} title="刷新作业"><ArchiveRestore size={18}/></button><button type="button" className="icon-button" disabled={!selectable.length} onClick={toggleAll} title={allSelected ? '取消全选' : '全选可删除的作业'}><Check size={18}/></button>{selected.length > 0 && <button type="button" className={confirming ? 'button danger' : 'button secondary'} disabled={busy} onClick={() => void removeSelected()}>{busy ? '正在删除' : confirming ? `确认删除（${selected.length}）` : `删除所选（${selected.length}）`}</button>}</PageHeader>{jobs.length ? <div className="job-list">{jobs.map(job => <article className="job" key={job.id}><div className="job-top"><div><input type="checkbox" checked={selected.includes(job.id)} disabled={job.state === 'running'} onChange={() => toggle(job.id)} title={job.state === 'running' ? '运行中的作业不能删除' : '选择删除'} /><b>{jobLabel(job.kind)}</b><small>{formatDate(job.created_at)} · 已尝试 {job.attempt_count} 次</small></div><Status value={job.state}/></div><div className="progress"><span style={{ width: `${job.progress}%` }} /></div><div className="job-foot"><span>{job.message || '等待本地 worker'}</span>{['queued', 'running', 'retry_wait'].includes(job.state) && <button type="button" className="icon-button" title="取消作业" onClick={() => void act(job.id, 'cancel')}><X size={17}/></button>}{['failed', 'blocked', 'cancelled'].includes(job.state) && <button type="button" className="icon-button" title="重试作业" onClick={() => void act(job.id, 'retry')}><ArchiveRestore size={17}/></button>}</div></article>)}</div> : <Empty icon={<ListChecks size={36}/>} text="没有作业记录" />}</div>
}

function ExternalCardsPage({ cards, focusedId, onMessage }: { cards: Card[]; focusedId: string | null; onMessage: (message: string) => void }) {
  useEffect(() => {
    if (focusedId) document.getElementById(`external-card-${focusedId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [focusedId])
  return <div className="page narrow"><PageHeader title="外部卡"/>
    <p className="muted">新增外部卡请在导入页粘贴链接。</p>
    {cards.length ? <div>{cards.map(card => <article className={card.id === focusedId ? 'external-card selected' : 'external-card'} id={`external-card-${card.id}`} key={card.id}><span className="result-kind">{card.card_type === 'douyin' ? '抖音参考' : '一般 URL'}</span><b>{card.title}</b><small>{card.author || '未署名'}{card.tags.length ? ` · ${card.tags.join('、')}` : ''}</small><p>{card.notes}</p><a href={card.url} target="_blank" rel="noreferrer" onClick={() => card.card_type === 'douyin' && onMessage('将在外部浏览器打开原始页面') }><ExternalLink size={16}/>在浏览器打开原 URL</a></article>)}</div> : <Empty icon={<Video size={36}/>} text="尚无外部卡" />}
  </div>
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

type AiSettings = {
  transcribe: { provider: string; base_url: string; model: string; has_key: boolean; key_hint: string | null }
  understand: { provider: string; base_url: string; chat_model: string; has_key: boolean; key_hint: string | null }
  transcriber: { engine: string; local_stt_model: string; stt_timeout_seconds: number; stt_memory_limit_mb: number; stt_disk_limit_mb: number }
  local_stt: { model_name: string; model_available: boolean; downloaded_at: string | null }
  video: { provider: string; model: string; max_bytes: number; reencode: boolean; chunk_seconds: number; frames_fallback: boolean; frames_enrich: boolean; sheet_frames: number; qwen: { has_key: boolean; key_hint: string | null }; mimo: { has_key: boolean; key_hint: string | null }; relay: { kind: string; base_url: string; has_secret: boolean; secret_hint: string | null; cos_bucket: string; cos_region: string; cos_has_key: boolean; cos_key_hint: string | null } }
  timeout_seconds: number
  auto_pipeline: boolean
}

function AiSettingsSection({ onMessage }: { onMessage: (message: string) => void }) {
  const [transcribe, setTranscribe] = useState({ provider: 'off', base_url: '', model: '', api_key: '' })
  const [understand, setUnderstand] = useState({ provider: 'off', base_url: '', chat_model: '', api_key: '' })
  const [transcriber, setTranscriber] = useState({ engine: 'auto', local_stt_model: 'paraformer-zh', stt_timeout_seconds: '3600', stt_memory_limit_mb: '2048', stt_disk_limit_mb: '1024' })
  const [video, setVideo] = useState({ provider: 'off', model: '', max_bytes: '314572800', reencode: true, chunk_seconds: '600', frames_fallback: true, frames_enrich: false, sheet_frames: '24', relay_kind: 'http', relay_base_url: '', relay_secret: '', cos_bucket: '', cos_region: 'ap-shanghai', cos_secret_id: '', cos_secret_key: '', qwen_api_key: '', mimo_api_key: '' })
  const [localStt, setLocalStt] = useState<{ model_name: string; model_available: boolean }>({ model_name: 'paraformer-zh', model_available: false })
  const [timeoutSeconds, setTimeoutSeconds] = useState('300')
  const [autoPipeline, setAutoPipeline] = useState(true)
  const [keyHints, setKeyHints] = useState<{ transcribe: string | null; understand: string | null }>({ transcribe: null, understand: null })
  const [videoHints, setVideoHints] = useState<{ qwen: string | null; mimo: string | null; relay: string | null; cos: string | null }>({ qwen: null, mimo: null, relay: null, cos: null })
  const [testResult, setTestResult] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState('')
  const apply = useCallback((value: AiSettings) => {
    setTranscribe({ provider: value.transcribe.provider, base_url: value.transcribe.base_url, model: value.transcribe.model, api_key: '' })
    setUnderstand({ provider: value.understand.provider, base_url: value.understand.base_url, chat_model: value.understand.chat_model, api_key: '' })
    setTranscriber({ engine: value.transcriber.engine, local_stt_model: value.transcriber.local_stt_model, stt_timeout_seconds: String(value.transcriber.stt_timeout_seconds), stt_memory_limit_mb: String(value.transcriber.stt_memory_limit_mb), stt_disk_limit_mb: String(value.transcriber.stt_disk_limit_mb) })
    setVideo({ provider: value.video.provider, model: value.video.model, max_bytes: String(value.video.max_bytes), reencode: value.video.reencode, chunk_seconds: String(value.video.chunk_seconds), frames_fallback: value.video.frames_fallback, frames_enrich: value.video.frames_enrich, sheet_frames: String(value.video.sheet_frames), relay_kind: value.video.relay.kind, relay_base_url: value.video.relay.base_url, relay_secret: '', cos_bucket: value.video.relay.cos_bucket, cos_region: value.video.relay.cos_region, cos_secret_id: '', cos_secret_key: '', qwen_api_key: '', mimo_api_key: '' })
    setLocalStt({ model_name: value.local_stt.model_name, model_available: value.local_stt.model_available })
    setTimeoutSeconds(String(value.timeout_seconds))
    setAutoPipeline(value.auto_pipeline)
    setKeyHints({
      transcribe: value.transcribe.has_key ? value.transcribe.key_hint : null,
      understand: value.understand.has_key ? value.understand.key_hint : null,
    })
    setVideoHints({
      qwen: value.video.qwen.has_key ? value.video.qwen.key_hint : null,
      mimo: value.video.mimo.has_key ? value.video.mimo.key_hint : null,
      relay: value.video.relay.has_secret ? value.video.relay.secret_hint : null,
      cos: value.video.relay.cos_has_key ? value.video.relay.cos_key_hint : null,
    })
  }, [])
  useEffect(() => {
    void request<AiSettings>('/settings/ai').then(apply).catch(error => onMessage(error instanceof Error ? error.message : '读取媒体 AI 设置失败'))
  }, [apply, onMessage])
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy('save')
    try {
      const value = await request<AiSettings>('/settings/ai', {
        method: 'PUT',
        body: JSON.stringify({
          transcribe: { provider: transcribe.provider, base_url: transcribe.base_url, model: transcribe.model, ...(transcribe.api_key ? { api_key: transcribe.api_key } : {}) },
          understand: { provider: understand.provider, base_url: understand.base_url, chat_model: understand.chat_model, ...(understand.api_key ? { api_key: understand.api_key } : {}) },
          transcriber: {
            engine: transcriber.engine,
            local_stt_model: transcriber.local_stt_model,
            stt_timeout_seconds: Number(transcriber.stt_timeout_seconds) || 3600,
            stt_memory_limit_mb: Number(transcriber.stt_memory_limit_mb) || 2048,
            stt_disk_limit_mb: Number(transcriber.stt_disk_limit_mb) || 1024,
          },
          video: {
            provider: video.provider,
            model: video.model,
            max_bytes: Number(video.max_bytes) || 314572800,
            reencode: video.reencode ? 'on' : 'off',
            chunk_seconds: Number(video.chunk_seconds) || 600,
            frames_fallback: video.frames_fallback ? 'on' : 'off',
            frames_enrich: video.frames_enrich ? 'on' : 'off',
            sheet_frames: Number(video.sheet_frames) || 24,
            relay_kind: video.relay_kind,
            relay_base_url: video.relay_base_url,
            cos_bucket: video.cos_bucket,
            cos_region: video.cos_region,
            ...(video.relay_secret ? { relay_secret: video.relay_secret } : {}),
            ...(video.cos_secret_id ? { cos_secret_id: video.cos_secret_id } : {}),
            ...(video.cos_secret_key ? { cos_secret_key: video.cos_secret_key } : {}),
            ...(video.qwen_api_key ? { qwen_api_key: video.qwen_api_key } : {}),
            ...(video.mimo_api_key ? { mimo_api_key: video.mimo_api_key } : {}),
          },
          timeout_seconds: Number(timeoutSeconds) || 300,
          auto_pipeline: autoPipeline,
        }),
      })
      apply(value)
      setTestResult({})
      onMessage('媒体 AI 设置已保存')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '媒体 AI 设置保存失败')
    } finally {
      setBusy('')
    }
  }
  const test = async (part: 'transcribe' | 'understand') => {
    setBusy(`test-${part}`)
    try {
      const result = await request<{ ok: boolean; message?: string }>('/settings/ai/test', { method: 'POST', body: JSON.stringify({ part }) })
      setTestResult(current => ({ ...current, [part]: result.ok ? '连接成功' : result.message || '连接失败' }))
    } catch (error) {
      setTestResult(current => ({ ...current, [part]: error instanceof Error ? error.message : '连接测试失败' }))
    } finally {
      setBusy('')
    }
  }
  const sttModel = async (action: 'download' | 'delete') => {
    setBusy(`model-${action}`)
    try {
      await request('/settings/ai/stt-model', { method: 'POST', body: JSON.stringify({ action }) })
      onMessage(action === 'download' ? '已提交本地转写模型下载，请到作业页查看进度' : '本地转写模型已删除')
      const value = await request<AiSettings>('/settings/ai')
      apply(value)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '模型操作失败')
    } finally {
      setBusy('')
    }
  }
  const providers = [['off', '关闭'], ['openai_compatible', 'OpenAI 兼容']] as const
  const transcriberEngines = [['auto', '自动（本地优先，失败降级 API）'], ['local', '仅本地'], ['api', '仅 API']] as const
  const videoProviders = [['off', '关闭'], ['qwen', '通义千问'], ['mimo', '小米 MiMo']] as const
  return <section className="form-stack media-ai"><h2>媒体 AI</h2>
    <p className="field-hint">启用后，音频、视频（直送时）与文本将发送至你配置的云端服务处理。API 密钥仅保存在本机凭据文件，不会进入备份、导出或日志。</p>
    <form className="form-stack" onSubmit={save}>
      <fieldset className="settings-section"><legend>语音转写（API 路径）</legend><div className="settings-grid">
        <label>提供方<select value={transcribe.provider} onChange={event => setTranscribe(current => ({ ...current, provider: event.target.value }))}>{providers.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label>
        <label>转写模型<input value={transcribe.model} onChange={event => setTranscribe(current => ({ ...current, model: event.target.value }))} placeholder="whisper-1"/></label>
        <label>Base URL（可空，使用提供方默认端点）<input value={transcribe.base_url} onChange={event => setTranscribe(current => ({ ...current, base_url: event.target.value }))} placeholder="https://…"/></label>
        <label>API 密钥<input type="password" autoComplete="off" value={transcribe.api_key} onChange={event => setTranscribe(current => ({ ...current, api_key: event.target.value }))} placeholder={keyHints.transcribe ? `已配置（${keyHints.transcribe}），输入以替换` : '未配置'}/></label>
      </div><div className="inline-actions"><button type="button" className="button secondary" disabled={Boolean(busy)} onClick={() => void test('transcribe')}>{busy === 'test-transcribe' ? '正在测试' : '测试连接'}</button>{testResult.transcribe && <span className="hint">{testResult.transcribe}</span>}</div></fieldset>
      <fieldset className="settings-section"><legend>本地转写（默认路径）</legend><div className="settings-grid">
        <label>路径策略<select value={transcriber.engine} onChange={event => setTranscriber(current => ({ ...current, engine: event.target.value }))}>{transcriberEngines.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label>
        <label>本地模型<select value={transcriber.local_stt_model} onChange={event => setTranscriber(current => ({ ...current, local_stt_model: event.target.value }))}><option value="paraformer-zh">paraformer-zh</option><option value="paraformer-zh-quant">paraformer-zh-quant</option></select></label>
        <label>转写超时（秒）<input type="number" min="60" max="86400" value={transcriber.stt_timeout_seconds} onChange={event => setTranscriber(current => ({ ...current, stt_timeout_seconds: event.target.value }))}/></label>
        <label>内存上限（MB）<input type="number" min="64" max="32768" value={transcriber.stt_memory_limit_mb} onChange={event => setTranscriber(current => ({ ...current, stt_memory_limit_mb: event.target.value }))}/></label>
        <label>磁盘上限（MB）<input type="number" min="64" max="32768" value={transcriber.stt_disk_limit_mb} onChange={event => setTranscriber(current => ({ ...current, stt_disk_limit_mb: event.target.value }))}/></label>
      </div><div className="inline-actions"><button type="button" className="button secondary" disabled={Boolean(busy)} onClick={() => void sttModel('download')}>{busy === 'model-download' ? '正在提交' : localStt.model_available ? '重新下载模型' : '下载模型'}</button><button type="button" className="button secondary" disabled={Boolean(busy) || !localStt.model_available} onClick={() => void sttModel('delete')}>删除模型</button><span className="hint">{localStt.model_available ? `模型 ${localStt.model_name} 已就绪` : '模型未下载：本地路径不可用，将按路径策略降级或阻塞'}</span></div></fieldset>
      <fieldset className="settings-section"><legend>理解与摘要</legend><div className="settings-grid">
        <label>提供方<select value={understand.provider} onChange={event => setUnderstand(current => ({ ...current, provider: event.target.value }))}>{providers.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label>
        <label>摘要模型<input value={understand.chat_model} onChange={event => setUnderstand(current => ({ ...current, chat_model: event.target.value }))} placeholder="qwen-plus"/></label>
        <label>Base URL（可空，使用提供方默认端点）<input value={understand.base_url} onChange={event => setUnderstand(current => ({ ...current, base_url: event.target.value }))} placeholder="https://…"/></label>
        <label>API 密钥<input type="password" autoComplete="off" value={understand.api_key} onChange={event => setUnderstand(current => ({ ...current, api_key: event.target.value }))} placeholder={keyHints.understand ? `已配置（${keyHints.understand}），输入以替换` : '未配置'}/></label>
      </div><div className="inline-actions"><button type="button" className="button secondary" disabled={Boolean(busy)} onClick={() => void test('understand')}>{busy === 'test-understand' ? '正在测试' : '测试连接'}</button>{testResult.understand && <span className="hint">{testResult.understand}</span>}</div></fieldset>
      <fieldset className="settings-section"><legend>视频直送（判定核心内容缺失时）</legend><div className="settings-grid">
        <label>供应商<select value={video.provider} onChange={event => setVideo(current => ({ ...current, provider: event.target.value }))}>{videoProviders.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label>
        <label>视频模型<input value={video.model} onChange={event => setVideo(current => ({ ...current, model: event.target.value }))} placeholder="空则按供应商默认"/></label>
        <label>直送上限（字节）<input type="number" min="1048576" max="536870912" value={video.max_bytes} onChange={event => setVideo(current => ({ ...current, max_bytes: event.target.value }))}/></label>
        <label>分块时长（秒）<input type="number" min="60" max="3600" value={video.chunk_seconds} onChange={event => setVideo(current => ({ ...current, chunk_seconds: event.target.value }))}/></label>
        <label>联络表帧数上限<input type="number" min="8" max="48" value={video.sheet_frames} onChange={event => setVideo(current => ({ ...current, sheet_frames: event.target.value }))}/></label>
      </div>
        <div className="group-title">帧理解（v1.7）</div>
        <label className="toggle-row"><input type="checkbox" checked={video.frames_fallback} onChange={event => setVideo(current => ({ ...current, frames_fallback: event.target.checked }))}/><span className="toggle-text"><span className="toggle-title">直送不可行时按关键帧联络表兜底理解画面</span><span className="toggle-desc">仅当视频直送已配置且失败/不可行时触发；只发送少量缩略图，帧字节远小于整片直送</span></span></label>
        <label className="toggle-row"><input type="checkbox" checked={video.frames_enrich} onChange={event => setVideo(current => ({ ...current, frames_enrich: event.target.checked }))}/><span className="toggle-text"><span className="toggle-title">转写完整时也做画面理解增强</span><span className="toggle-desc">默认关闭；开启后每条视频会额外消耗一次多模态调用</span></span></label>
        <label className="toggle-row"><input type="checkbox" checked={video.reencode} onChange={event => setVideo(current => ({ ...current, reencode: event.target.checked }))}/><span className="toggle-text"><span className="toggle-title">MiMo 直送前显式重编码</span><span className="toggle-desc">超过 base64 上限时降低码率与分辨率，保留 ≥48kbps 音轨；显式策略而非静默降质</span></span></label>
        <div className="credential-sub"><div className="sub-title">供应商凭据（掩码回显，仅存本机凭据文件）</div><div className="settings-grid"><label>通义千问密钥<input type="password" autoComplete="off" value={video.qwen_api_key} onChange={event => setVideo(current => ({ ...current, qwen_api_key: event.target.value }))} placeholder={videoHints.qwen ? `已配置（${videoHints.qwen}），输入以替换` : '未配置'}/></label><label>MiMo 密钥<input type="password" autoComplete="off" value={video.mimo_api_key} onChange={event => setVideo(current => ({ ...current, mimo_api_key: event.target.value }))} placeholder={videoHints.mimo ? `已配置（${videoHints.mimo}），输入以替换` : '未配置'}/></label></div></div>
        <div className="credential-sub"><div className="sub-title">自备中转（配置后直送优先经中转 URL）</div><div className="settings-grid"><label>中转形态<select value={video.relay_kind} onChange={event => setVideo(current => ({ ...current, relay_kind: event.target.value }))}><option value="off">关闭</option><option value="http">自建中转（需已备案域名）</option><option value="cos">腾讯云 COS（未备案可用）</option></select></label>{video.relay_kind === 'http' && <label>中转地址<input value={video.relay_base_url} onChange={event => setVideo(current => ({ ...current, relay_base_url: event.target.value }))} placeholder="https://你的域名"/></label>}{video.relay_kind === 'cos' && <><label>存储桶<input value={video.cos_bucket} onChange={event => setVideo(current => ({ ...current, cos_bucket: event.target.value }))} placeholder="如 my-bucket-1250000000"/></label>
        <label>地域<input value={video.cos_region} onChange={event => setVideo(current => ({ ...current, cos_region: event.target.value }))} placeholder="ap-shanghai"/></label></>}{video.relay_kind === 'http' && <label className="toggle-row"><input type="password" autoComplete="off" value={video.relay_secret} onChange={event => setVideo(current => ({ ...current, relay_secret: event.target.value }))} placeholder={videoHints.relay ? `已配置（${videoHints.relay}），输入以替换` : '未配置'}/><span className="toggle-text"><span className="toggle-title">中转密钥</span><span className="toggle-desc">Bearer 上传密钥，仅存本机凭据文件</span></span></label>}{video.relay_kind === 'cos' && <><label>SecretId<input type="password" autoComplete="off" value={video.cos_secret_id} onChange={event => setVideo(current => ({ ...current, cos_secret_id: event.target.value }))} placeholder={videoHints.cos ? `已配置（${videoHints.cos}），输入以替换` : '未配置'}/></label>
        <label>SecretKey<input type="password" autoComplete="off" value={video.cos_secret_key} onChange={event => setVideo(current => ({ ...current, cos_secret_key: event.target.value }))} placeholder="输入以替换"/></label></>}</div></div>
        <p className="field-hint">直送仅在完整性判定「可能缺失」或你点「强制深度理解」时发生；直送不可行时按关键帧联络表兜底理解画面（可关闭），仅发送少量缩略图。COS 形态使用预签名 URL（30 分钟）并在拉取后自动删除对象；未配置中转时 MiMo 走 base64（超限重编码/分块）、Qwen 走 DashScope 临时上传。</p>
      </fieldset>
      <fieldset className="settings-section"><legend>全局与流水线</legend><div className="settings-grid"><label>AI 调用超时（秒）<input type="number" min="60" max="86400" value={timeoutSeconds} onChange={event => setTimeoutSeconds(event.target.value)}/></label></div>
      <label className="toggle-row"><input type="checkbox" checked={autoPipeline} onChange={event => setAutoPipeline(event.target.checked)}/><span className="toggle-text"><span className="toggle-title">自动流水线</span><span className="toggle-desc">开启后视频导入即排队转写+分析（转写先行）并自动接力摘要；文档与粘贴解析后自动分类（内容将发送至所配置端点）；关闭则视频只自动分析，转写/摘要需在详情页手动触发</span></span></label>
      <p className="field-hint">测试连接使用已保存的配置与密钥；修改后请先保存。</p></fieldset>
      <div className="form-actions"><button className="button primary" disabled={Boolean(busy)}>{busy === 'save' ? '正在保存' : '保存媒体 AI 设置'}</button></div>
    </form>
  </section>
}

function SettingsPage({ onMessage }: { onMessage: (message: string) => void }) {
  const [settings, setSettings] = useState<Record<string, string>>({})
  const [cookies, setCookies] = useState<Record<string, boolean>>({})
  useEffect(() => { void request<Record<string, string>>('/settings').then(setSettings).catch(error => onMessage(error instanceof Error ? error.message : '读取设置失败')) }, [onMessage])
  const loadCookies = useCallback(async () => {
    const output = await request<{ downloader: DownloaderCapability }>('/capabilities')
    setCookies(output.downloader.cookies || {})
  }, [])
  useEffect(() => { void loadCookies().catch(() => undefined) }, [loadCookies])
  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      const result = await request<Record<string, string>>('/settings', { method: 'PUT', body: JSON.stringify({ parser_timeout_seconds: Number(settings.parser_timeout_seconds), parser_no_progress_seconds: Number(settings.parser_no_progress_seconds), parser_memory_limit_mb: Number(settings.parser_memory_limit_mb), parser_disk_limit_mb: Number(settings.parser_disk_limit_mb), video_timeout_seconds: Number(settings.video_timeout_seconds), video_memory_limit_mb: Number(settings.video_memory_limit_mb), video_disk_limit_mb: Number(settings.video_disk_limit_mb), video_max_frames: Number(settings.video_max_frames), image_timeout_seconds: Number(settings.image_timeout_seconds), image_memory_limit_mb: Number(settings.image_memory_limit_mb), image_disk_limit_mb: Number(settings.image_disk_limit_mb), job_lease_seconds: Number(settings.job_lease_seconds), max_retry_attempts: Number(settings.max_retry_attempts), download_timeout_seconds: Number(settings.download_timeout_seconds), download_no_progress_seconds: Number(settings.download_no_progress_seconds), download_disk_limit_mb: Number(settings.download_disk_limit_mb) }) })
      setSettings(result)
      onMessage('设置已保存到本地 state')
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '保存设置失败')
    }
  }
  const importCookie = async (platform: 'bilibili' | 'douyin', event: React.ChangeEvent<HTMLInputElement>) => {
    const picked = event.target.files?.[0]
    if (!picked) return
    const name = platform === 'douyin' ? '抖音' : '哔哩哔哩'
    try {
      const body = new FormData()
      body.set('file', picked)
      await uploadFile(`/settings/download-cookies/${platform}`, body, () => undefined)
      await loadCookies()
      onMessage(`已导入${name} Cookie，链接下载时可选择使用`)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : 'cookies.txt 导入失败')
    } finally {
      event.target.value = ''
    }
  }
  const removeCookie = async (platform: 'bilibili' | 'douyin') => {
    const name = platform === 'douyin' ? '抖音' : '哔哩哔哩'
    if (!window.confirm(`确定删除已导入的${name} Cookie？`)) return
    try {
      await request(`/settings/download-cookies/${platform}`, { method: 'DELETE' })
      await loadCookies()
      onMessage(`已删除${name} Cookie`)
    } catch (error) {
      onMessage(error instanceof Error ? error.message : '删除失败')
    }
  }
  return <div className="page narrow"><PageHeader title="设置"/><form className="form-stack" onSubmit={save}><fieldset className="settings-section"><legend>解析断路器</legend><div className="settings-grid"><label>解析总超时（秒）<input type="number" min="60" max="86400" value={settings.parser_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, parser_timeout_seconds: event.target.value }))}/></label><label>无进展断路器（秒）<input type="number" min="60" max="86400" value={settings.parser_no_progress_seconds || ''} onChange={event => setSettings(current => ({ ...current, parser_no_progress_seconds: event.target.value }))}/></label><label>解析内存上限（MB）<input type="number" min="64" max="32768" value={settings.parser_memory_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, parser_memory_limit_mb: event.target.value }))}/></label><label>解析磁盘上限（MB）<input type="number" min="64" max="32768" value={settings.parser_disk_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, parser_disk_limit_mb: event.target.value }))}/></label></div><p className="field-hint">超时、内存、磁盘、无进展四道闸，超出即中断作业；一般无需改动。</p></fieldset><fieldset className="settings-section"><legend>本地视频分析</legend><div className="settings-grid"><label>视频总超时（秒）<input type="number" min="60" max="86400" value={settings.video_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, video_timeout_seconds: event.target.value }))}/></label><label>视频内存上限（MB）<input type="number" min="64" max="32768" value={settings.video_memory_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, video_memory_limit_mb: event.target.value }))}/></label><label>视频磁盘上限（MB）<input type="number" min="64" max="32768" value={settings.video_disk_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, video_disk_limit_mb: event.target.value }))}/></label><label>最大关键帧数<input type="number" min="1" max="32" value={settings.video_max_frames || ''} onChange={event => setSettings(current => ({ ...current, video_max_frames: event.target.value }))}/></label></div></fieldset><fieldset className="settings-section"><legend>本地图片分析</legend><div className="settings-grid"><label>图片总超时（秒）<input type="number" min="60" max="86400" value={settings.image_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, image_timeout_seconds: event.target.value }))}/></label><label>图片内存上限（MB）<input type="number" min="64" max="32768" value={settings.image_memory_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, image_memory_limit_mb: event.target.value }))}/></label><label>图片磁盘上限（MB）<input type="number" min="64" max="32768" value={settings.image_disk_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, image_disk_limit_mb: event.target.value }))}/></label></div></fieldset><fieldset className="settings-section"><legend>链接下载</legend><div className="settings-grid"><label>下载总超时（秒）<input type="number" min="60" max="86400" value={settings.download_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, download_timeout_seconds: event.target.value }))}/></label><label>下载无进展观察窗口（秒）<input type="number" min="10" max="86400" value={settings.download_no_progress_seconds || ''} onChange={event => setSettings(current => ({ ...current, download_no_progress_seconds: event.target.value }))}/></label><label>下载 staging 磁盘上限（MB）<input type="number" min="64" max="32768" value={settings.download_disk_limit_mb || ''} onChange={event => setSettings(current => ({ ...current, download_disk_limit_mb: event.target.value }))}/></label></div></fieldset><fieldset className="settings-section"><legend>作业运行</legend><div className="settings-grid"><label>作业租约（秒）<input type="number" min="60" max="86400" value={settings.job_lease_seconds || ''} onChange={event => setSettings(current => ({ ...current, job_lease_seconds: event.target.value }))}/></label><label>最大重试次数<input type="number" min="0" max="10" value={settings.max_retry_attempts || ''} onChange={event => setSettings(current => ({ ...current, max_retry_attempts: event.target.value }))}/></label></div><p className="field-hint">运行中作业按租约续期，失联即回收重排；普通失败按此处次数自动重试。</p></fieldset><button className="button primary">保存设置</button></form><section className="settings-section download-cookie"><h2>下载 Cookie</h2>{([['bilibili', '哔哩哔哩'], ['douyin', '抖音']] as const).map(([key, name]) => <div className="form-row" key={key}><span className="hint">{name}：{cookies[key] === true ? '已导入' : '未导入'}</span><label className="file-pick">导入 cookies.txt（Netscape 格式，≤1MB）<input className="visually-hidden" type="file" accept=".txt" onChange={event => void importCookie(key, event)} /><span><Upload size={20}/>选择 cookies.txt</span></label><button type="button" className="button secondary" disabled={cookies[key] !== true} onClick={() => void removeCookie(key)}><Trash2 size={16}/>删除</button></div>)}<p className="hint">按平台分别保存 Cookie；识别链接平台后自动选用对应文件。Cookie 内容不会进入备份、导出或日志。</p></section><AiSettingsSection onMessage={onMessage}/><section className="settings-section policy-list"><h2>本地运行策略</h2><div><Check size={16}/>仅绑定 127.0.0.1</div><div><Check size={16}/>无遥测、无本地 HTTPS、无加密层</div><div><Check size={16}/>解析仅本地回退，禁止静默云服务</div><div><Check size={16}/>视频分析仅限本地 MP4/WebM</div><div><Check size={16}/>链接下载仅白名单平台、单视频、≤1080p，出站经回环过滤代理</div><div><Check size={16}/>下载 Cookie 仅 cookies.txt 单通道，绝不进入备份、导出或日志</div><div><Check size={16}/>操作日志不记录正文、路径或令牌</div></section></div>
}

function Empty({ icon, text }: { icon: React.ReactNode; text: string }) { return <div className="empty">{icon}<span>{text}</span></div> }
