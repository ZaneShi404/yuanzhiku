import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ArchiveRestore, BookOpen, Box, BriefcaseBusiness, Check, ChevronDown, CircleAlert,
  ExternalLink, FileText, FolderOpen, HardDriveDownload, Import, Library, ListChecks,
  Search, Settings, ShieldCheck, Tags, Trash2, Upload, Video, X
} from 'lucide-react'

type Source = {
  id: string; title: string; source_type: string; author?: string; language: string; notes?: string;
  rights?: string; categories: string[]; tags: string[]; processing_state: string; imported_at: string; updated_at: string; deleted_at?: string | null;
  versions?: Version[]; relations?: Relation[]
}
type Version = { id: string; artifact_sha256: string; original_name: string; media_type?: string; completeness: string; created_at: string }
type Relation = { id: string; source_id: string; related_source_id: string; relation_type: string; created_at: string }
type Job = { id: string; kind: string; state: string; progress: number; message?: string; source_id?: string; created_at: string; updated_at: string; attempt_count: number }
type Card = { id: string; card_type: string; url: string; title: string; author?: string; notes?: string; tags: string[]; created_at: string }
type SearchItem = { kind: string; id: string; title: string; relevance: number; processing_state?: string; source_type?: string; updated_at: string }
type Backup = { id: string; archive_name: string; created_at: string; state: string }

type Page = 'library' | 'import' | 'search' | 'jobs' | 'external' | 'transfers' | 'settings'
const API = '/api/v1'
const categories = [
  ['technical', '技术'], ['business', '商业'], ['education', '教育'], ['news', '新闻'], ['interview', '访谈'], ['podcast', '播客'], ['document', '文档']
] as const
const rights = [['owned', '本人拥有'], ['authorized', '已获授权'], ['permitted', '已获许可'], ['open_license', '开放许可'], ['other', '其他']]

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...options, headers: { ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...options.headers } })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: '本地请求失败' }))
    throw new Error(payload.detail || '本地请求失败')
  }
  return response.json() as Promise<T>
}

function date(value?: string) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function stateLabel(value: string) {
  const map: Record<string, string> = { queued: '排队', running: '处理中', retry_wait: '等待重试', succeeded: '已完成', failed: '失败', blocked: '已阻止', cancelled: '已取消', awaiting_ocr: '等待 OCR', pending: '待处理', complete: '完整', incomplete: '不完整' }
  return map[value] || value
}
function sourceType(value: string) { return value === 'paste' ? '粘贴文本' : value === 'file' ? '本地文件' : value === 'douyin' ? '抖音参考' : '外部卡' }

function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => { const timer = window.setTimeout(onClose, 4500); return () => clearTimeout(timer) }, [onClose])
  return <div className="toast" role="status"><CircleAlert size={18} /> <span>{message}</span><button onClick={onClose} aria-label="关闭"><X size={16} /></button></div>
}

function PageHeader({ title, children }: { title: string; children?: React.ReactNode }) {
  return <header className="page-header"><h1>{title}</h1>{children && <div className="page-actions">{children}</div>}</header>
}

function Status({ value }: { value: string }) { return <span className={`status status-${value}`}>{stateLabel(value)}</span> }

export function App() {
  const [page, setPage] = useState<Page>('library')
  const [sources, setSources] = useState<Source[]>([])
  const [selected, setSelected] = useState<Source | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [cards, setCards] = useState<Card[]>([])
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(true)

  const loadSources = useCallback(async () => {
    const data = await request<Source[]>('/sources')
    setSources(data)
    setSelected(current => current ? data.find(item => item.id === current.id) || current : null)
  }, [])
  const loadJobs = useCallback(async () => setJobs(await request<Job[]>('/jobs')), [])
  const loadCards = useCallback(async () => setCards(await request<Card[]>('/external/cards')), [])
  const refresh = useCallback(async () => {
    try { await Promise.all([loadSources(), loadJobs(), loadCards()]) } catch (error) { setMessage(error instanceof Error ? error.message : '读取本地数据失败') } finally { setLoading(false) }
  }, [loadSources, loadJobs, loadCards])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const timer = window.setInterval(() => { void loadJobs(); void loadSources() }, 2500)
    return () => window.clearInterval(timer)
  }, [loadJobs, loadSources])

  const openSource = async (id: string) => {
    if (!id) { setSelected(null); return }
    try { setSelected(await request<Source>(`/sources/${id}`)); setPage('library') } catch (error) { setMessage(error instanceof Error ? error.message : '无法读取来源') }
  }
  const navigate = (next: Page) => { setSelected(null); setPage(next) }

  const nav: { id: Page; label: string; icon: React.ReactNode }[] = [
    { id: 'library', label: '资料库', icon: <Library size={18} /> }, { id: 'import', label: '导入', icon: <Import size={18} /> },
    { id: 'search', label: '检索', icon: <Search size={18} /> }, { id: 'jobs', label: '作业', icon: <ListChecks size={18} /> },
    { id: 'external', label: '外部卡', icon: <Video size={18} /> }, { id: 'transfers', label: '备份与导出', icon: <HardDriveDownload size={18} /> },
    { id: 'settings', label: '设置', icon: <Settings size={18} /> }
  ]

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><BookOpen size={22} /><span>源知库</span></div>
      <nav aria-label="主导航">{nav.map(item => <button className={page === item.id ? 'nav-item active' : 'nav-item'} onClick={() => navigate(item.id)} key={item.id}>{item.icon}<span>{item.label}</span></button>)}</nav>
      <div className="sidebar-foot"><ShieldCheck size={16} /><span>仅本机 · 无遥测</span></div>
    </aside>
    <main className="main-content">
      {loading ? <div className="loading">正在读取本地资料库</div> : <>
        {page === 'library' && <LibraryPage sources={sources} selected={selected} onSelect={openSource} onRefresh={refresh} onMessage={setMessage} />}
        {page === 'import' && <ImportPage onDone={() => { void refresh(); navigate('library') }} onMessage={setMessage} />}
        {page === 'search' && <SearchPage onSelect={openSource} onMessage={setMessage} />}
        {page === 'jobs' && <JobsPage jobs={jobs} onRefresh={refresh} onMessage={setMessage} />}
        {page === 'external' && <ExternalCardsPage cards={cards} onDone={loadCards} onMessage={setMessage} />}
        {page === 'transfers' && <TransfersPage onMessage={setMessage} />}
        {page === 'settings' && <SettingsPage onMessage={setMessage} />}
      </>}
    </main>
    {message && <Toast message={message} onClose={() => setMessage('')} />}
  </div>
}

function LibraryPage({ sources, selected, onSelect, onRefresh, onMessage }: { sources: Source[]; selected: Source | null; onSelect: (id: string) => void; onRefresh: () => Promise<void>; onMessage: (message: string) => void }) {
  const [filter, setFilter] = useState('')
  const visible = useMemo(() => sources.filter(source => source.title.toLowerCase().includes(filter.toLowerCase()) || source.tags.some(tag => tag.toLowerCase().includes(filter.toLowerCase()))), [sources, filter])
  return <div className="page library-page">
    <PageHeader title={selected ? selected.title : '资料库'}>{selected ? <button className="button secondary" onClick={() => onSelect('')}>返回列表</button> : <span className="count">{sources.length} 项来源</span>}</PageHeader>
    {selected ? <SourceDetail source={selected} onRefresh={onRefresh} onMessage={onMessage} /> : <>
      <div className="toolbar"><label className="search-field"><Search size={17}/><input value={filter} onChange={event => setFilter(event.target.value)} placeholder="按标题或标签筛选" /></label><button className="icon-button" onClick={() => void onRefresh()} title="刷新资料库"><ArchiveRestore size={18}/></button></div>
      {visible.length ? <div className="source-table" role="table"><div className="table-head" role="row"><span>标题</span><span>类型</span><span>状态</span><span>更新时间</span></div>{visible.map(source => <button className="table-row" role="row" onClick={() => void onSelect(source.id)} key={source.id}><span className="source-title"><FileText size={18}/><b>{source.title}</b><small>{source.author || '未署名'}{source.tags.length ? ` · ${source.tags.join('、')}` : ''}</small></span><span>{sourceType(source.source_type)}</span><span><Status value={source.processing_state}/></span><time>{date(source.updated_at)}</time></button>)}</div> : <Empty icon={<FolderOpen size={36}/>} text="资料库尚无来源" />}
    </>}
  </div>
}

function SourceDetail({ source, onRefresh, onMessage }: { source: Source; onRefresh: () => Promise<void>; onMessage: (message: string) => void }) {
  const [showPurge, setShowPurge] = useState(false)
  const [text, setText] = useState('')
  const [editing, setEditing] = useState(false)
  const [revisedText, setRevisedText] = useState('')
  const [humanRevised, setHumanRevised] = useState(false)
  const [evidence, setEvidence] = useState<{ id: string; excerpt: string; locator: Record<string, unknown> }[]>([])
  const version = source.versions?.[0]
  const loadText = async () => {
    if (!version) return
    try {
      const items = await request<{ id: string; text_content: string }[]>(`/documents/${version.id}/representations`)
      const current = items.at(-1)
      setText(current?.text_content || '当前版本尚无可显示的本地文本。')
      setHumanRevised(Boolean(current && (current as { kind?: string }).kind === 'manual'))
      if (current) setEvidence(await request<{ id: string; excerpt: string; locator: Record<string, unknown> }[]>(`/representations/${current.id}/evidence`))
    } catch (error) { onMessage(error instanceof Error ? error.message : '读取表示失败') }
  }
  useEffect(() => { void loadText() }, [source.id])
  const saveManualRevision = async () => {
    if (!version || !revisedText.trim()) { onMessage('请输入人工修订文本'); return }
    try {
      await request(`/documents/${version.id}/representations/manual`, { method: 'POST', body: JSON.stringify({ text: revisedText }) })
      setEditing(false); setRevisedText(''); await loadText(); onMessage('已创建人工修订表示；引用将标明人工修订并可查看原始视图')
    } catch (error) { onMessage(error instanceof Error ? error.message : '人工修订保存失败') }
  }
  const lifecycle = async (action: 'delete' | 'restore' | 'purge') => {
    try { await request(`/sources/${source.id}/${action}`, { method: 'POST' }); onMessage(action === 'purge' ? '已永久删除来源与无引用 artifact' : action === 'restore' ? '已恢复来源' : '已移至软删除'); await onRefresh() } catch (error) { onMessage(error instanceof Error ? error.message : '操作失败') }
  }
  return <section className="detail-layout"><div className="detail-main"><div className="metadata-grid"><div><label>来源类型</label><span>{sourceType(source.source_type)}</span></div><div><label>处理状态</label><Status value={source.processing_state}/></div><div><label>权利确认</label><span>{rights.find(item => item[0] === source.rights)?.[1] || source.rights || '-'}</span></div><div><label>语言</label><span>{source.language}</span></div><div className="wide"><label>固定分类</label><span>{source.categories.map(value => categories.find(item => item[0] === value)?.[1] || value).join('、') || '-'}</span></div><div className="wide"><label>标签</label><span>{source.tags.join('、') || '-'}</span></div></div>
    <section className="document-panel"><header><h2>文本表示</h2><span>{humanRevised ? '人工修订表示 · 可查看原始视图' : version?.completeness === 'complete' ? '当前完整版本' : version ? stateLabel(version.completeness) : '-'}</span></header>{text ? <pre>{text}</pre> : <div className="loading">读取文本表示</div>}</section>
    {version && <section className="manual-revision"><header><h2>人工修订</h2>{!editing && <button className="button secondary" onClick={() => { setRevisedText(text); setEditing(true) }}>新建修订表示</button>}</header>{editing && <><textarea value={revisedText} onChange={event => setRevisedText(event.target.value)} aria-label="人工修订文本"/><div><button className="button primary" onClick={() => void saveManualRevision()}>保存新表示</button><button className="button text" onClick={() => setEditing(false)}>取消</button></div></>}</section>}
    <section className="evidence-panel"><header><h2>证据与引用</h2><span>{humanRevised ? '人工修订引用' : '不可变 evidence'}</span></header>{evidence.length ? evidence.map(item => <article key={item.id}><p>{item.excerpt}</p><small>{JSON.stringify(item.locator)}</small><button className="text-button" onClick={() => { const index = text.indexOf(item.excerpt); if (index >= 0) onMessage(`定位到文本表示第 ${index + 1} 个字符`) }}>定位至文本表示</button></article>) : <p className="muted">当前版本尚无可引用 evidence。</p>}</section>
    {version?.media_type === 'application/pdf' && <section className="document-panel"><header><h2>PDF 只读预览</h2><span>嵌入链接已禁用</span></header><iframe title="PDF 只读预览" sandbox="allow-same-origin" src={`${API}/sources/${source.id}/original#toolbar=0&navpanes=0`} /></section>}
  </div><aside className="detail-side"><h2>版本</h2>{source.versions?.map(item => <div className="version" key={item.id}><Box size={17}/><div><b>{item.original_name}</b><small>{item.artifact_sha256.slice(0, 16)}...</small><Status value={item.completeness}/></div></div>)}<h2>关系</h2>{source.relations?.length ? source.relations.map(item => <div className="relation" key={item.id}>{item.relation_type}</div>) : <p className="muted">尚无手工关系</p>}<div className="danger-zone">{source.deleted_at ? <button className="button secondary" onClick={() => void lifecycle('restore')}>恢复来源</button> : <button className="button danger" onClick={() => void lifecycle('delete')}><Trash2 size={16}/>软删除</button>}{source.deleted_at && <button className="button danger" onClick={() => setShowPurge(true)}><Trash2 size={16}/>永久删除</button>}{showPurge && <div className="confirm"><p>将移除来源、派生数据和无引用 artifact。此操作不可撤销。</p><button className="button danger" onClick={() => void lifecycle('purge')}>确认永久删除</button><button className="button text" onClick={() => setShowPurge(false)}>取消</button></div>}</div></aside></section>
}

function ImportPage({ onDone, onMessage }: { onDone: () => void; onMessage: (message: string) => void }) {
  const [mode, setMode] = useState<'paste' | 'file'>('paste')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [right, setRight] = useState('')
  const [tags, setTags] = useState('')
  const [selectedCategories, setSelectedCategories] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!right) { onMessage('导入前必须确认文档或文本的权利来源'); return }
    setBusy(true)
    try {
      if (mode === 'paste') {
        await request('/imports/paste', { method: 'POST', body: JSON.stringify({ title, text: content, rights: right, tags: tags.split(/[,，]/).map(item => item.trim()).filter(Boolean), categories: selectedCategories }) })
      } else {
        if (!file) { onMessage('请选择 PDF、DOCX、Markdown 或 TXT 文件'); return }
        const body = new FormData(); body.set('file', file); body.set('title', title || file.name); body.set('rights', right); body.set('tags', JSON.stringify(tags.split(/[,，]/).map(item => item.trim()).filter(Boolean))); body.set('categories', JSON.stringify(selectedCategories)); body.set('language', 'zh')
        await request('/imports/file', { method: 'POST', body })
      }
      onMessage('已写入不可变 artifact，并已排入本地解析作业'); onDone()
    } catch (error) { onMessage(error instanceof Error ? error.message : '导入失败') } finally { setBusy(false) }
  }
  return <div className="page narrow"><PageHeader title="导入"/><form className="form-stack" onSubmit={submit}><div className="segmented"><button type="button" className={mode === 'paste' ? 'selected' : ''} onClick={() => setMode('paste')}>粘贴文本</button><button type="button" className={mode === 'file' ? 'selected' : ''} onClick={() => setMode('file')}>本地文件</button></div><label>标题<input required value={title} onChange={event => setTitle(event.target.value)} placeholder={mode === 'file' ? '可留空，默认使用文件名' : '来源标题'} /></label>{mode === 'paste' ? <label>UTF-8 文本或 Markdown<textarea required maxLength={10 * 1024 * 1024} value={content} onChange={event => setContent(event.target.value)} placeholder="粘贴不超过 10MB 的文本" /></label> : <label className="file-pick"><Upload size={20}/><span>{file?.name || '选择 PDF、DOCX、Markdown 或 TXT'}</span><input type="file" accept=".pdf,.docx,.md,.markdown,.txt,text/plain,application/pdf" onChange={event => setFile(event.target.files?.[0] || null)} /></label>}<label>权利确认<select required value={right} onChange={event => setRight(event.target.value)}><option value="" disabled>请选择</option>{rights.map(item => <option key={item[0]} value={item[0]}>{item[1]}</option>)}</select></label><fieldset><legend>固定分类（可多选）</legend><div className="check-grid">{categories.map(item => <label key={item[0]}><input type="checkbox" checked={selectedCategories.includes(item[0])} onChange={() => setSelectedCategories(current => current.includes(item[0]) ? current.filter(value => value !== item[0]) : [...current, item[0]])}/>{item[1]}</label>)}</div></fieldset><label>自由标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label><button className="button primary" disabled={busy}>{busy ? '正在写入本地存储' : '确认权利并导入'}</button></form></div>
}

function SearchPage({ onSelect, onMessage }: { onSelect: (id: string) => void; onMessage: (message: string) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchItem[]>([])
  const [advanced, setAdvanced] = useState(false)
  const [incomplete, setIncomplete] = useState(false)
  const [sort, setSort] = useState('relevance')
  const search = async (event?: React.FormEvent) => { event?.preventDefault(); try { const response = await request<{ items: SearchItem[] }>(`/search?q=${encodeURIComponent(query)}&include_historical=${advanced}&include_incomplete=${incomplete}&sort=${encodeURIComponent(sort)}`); setResults(response.items) } catch (error) { onMessage(error instanceof Error ? error.message : '检索失败') } }
  return <div className="page"><PageHeader title="检索"/><form className="search-command" onSubmit={search}><Search size={20}/><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="输入中文短语、关键词或任意子串"/><button className="button primary">检索</button></form><div className="search-options"><label>排序<select value={sort} onChange={event => setSort(event.target.value)}><option value="relevance">相关度</option><option value="updated">导入/更新时间</option><option value="title">标题</option></select></label><div className="advanced"><button className="text-button" onClick={() => setAdvanced(value => !value)}><ChevronDown size={16} className={advanced ? 'turned' : ''}/>高级范围</button>{advanced && <div><label><input type="checkbox" checked={advanced} onChange={event => setAdvanced(event.target.checked)}/>包含历史版本</label><label><input type="checkbox" checked={incomplete} onChange={event => setIncomplete(event.target.checked)}/>包含不完整版本</label></div>}</div></div><p className="hint">仅进行中文短语、关键词和子串匹配，不提供语义检索。</p>{results.length ? <div className="result-list">{results.map(item => <button key={`${item.kind}-${item.id}`} className="result" onClick={() => item.kind === 'source' ? onSelect(item.id) : undefined}><span className="result-kind">{item.kind === 'source' ? '来源' : item.kind === 'knowledge' ? '知识' : '外部卡'}</span><b>{item.title}</b><small>匹配 {item.relevance} 次 · {date(item.updated_at)}</small></button>)}</div> : <Empty icon={<Search size={36}/>} text="输入条件后开始本地检索" />}</div>
}

function JobsPage({ jobs, onRefresh, onMessage }: { jobs: Job[]; onRefresh: () => Promise<void>; onMessage: (message: string) => void }) {
  const act = async (id: string, action: 'cancel' | 'retry') => { try { await request(`/jobs/${id}/${action}`, { method: 'POST' }); await onRefresh() } catch (error) { onMessage(error instanceof Error ? error.message : '作业操作失败') } }
  return <div className="page"><PageHeader title="作业"><button className="icon-button" onClick={() => void onRefresh()} title="刷新作业"><ArchiveRestore size={18}/></button></PageHeader>{jobs.length ? <div className="job-list">{jobs.map(job => <article className="job" key={job.id}><div className="job-top"><div><b>{job.kind === 'parse' ? '本地解析' : job.kind === 'backup' ? '日常备份' : job.kind}</b><small>{date(job.created_at)} · 已尝试 {job.attempt_count} 次</small></div><Status value={job.state}/></div><div className="progress"><span style={{ width: `${job.progress}%` }} /></div><div className="job-foot"><span>{job.message || '等待本地 worker'}</span>{['queued', 'running', 'retry_wait'].includes(job.state) && <button className="icon-button" title="取消作业" onClick={() => void act(job.id, 'cancel')}><X size={17}/></button>}{['failed', 'blocked', 'cancelled'].includes(job.state) && <button className="icon-button" title="重试作业" onClick={() => void act(job.id, 'retry')}><ArchiveRestore size={17}/></button>}</div></article>)}</div> : <Empty icon={<ListChecks size={36}/>} text="没有作业记录" />}</div>
}

function ExternalCardsPage({ cards, onDone, onMessage }: { cards: Card[]; onDone: () => Promise<void>; onMessage: (message: string) => void }) {
  const [mode, setMode] = useState<'general' | 'douyin'>('general'); const [title, setTitle] = useState(''); const [url, setUrl] = useState(''); const [author, setAuthor] = useState(''); const [notes, setNotes] = useState(''); const [tags, setTags] = useState('')
  const submit = async (event: React.FormEvent) => { event.preventDefault(); try { await request(mode === 'douyin' ? '/external/douyin' : '/external/cards', { method: 'POST', body: JSON.stringify({ title, url, author: author || null, notes: notes || null, tags: tags.split(/[,，]/).map(item => item.trim()).filter(Boolean) }) }); setTitle(''); setUrl(''); setAuthor(''); setNotes(''); setTags(''); await onDone(); onMessage('已保存用户输入的元数据，系统未访问该 URL') } catch (error) { onMessage(error instanceof Error ? error.message : '保存外部卡失败') } }
  return <div className="page split-page"><section><PageHeader title="外部卡"/><form className="form-stack" onSubmit={submit}><div className="segmented"><button type="button" className={mode === 'general' ? 'selected' : ''} onClick={() => setMode('general')}>一般 URL</button><button type="button" className={mode === 'douyin' ? 'selected' : ''} onClick={() => setMode('douyin')}>抖音参考</button></div>{mode === 'douyin' && <div className="notice">只保存用户输入的 HTTPS douyin.com 或子域 URL。不会请求、跳转、抓取、解析、预览或嵌入内容。</div>}<label>标题<input required value={title} onChange={event => setTitle(event.target.value)} /></label><label>URL<input required type="url" value={url} onChange={event => setUrl(event.target.value)} placeholder={mode === 'douyin' ? 'https://www.douyin.com/...' : 'https://...'} /></label><label>作者或账号<input value={author} onChange={event => setAuthor(event.target.value)} /></label><label>备注<textarea value={notes} onChange={event => setNotes(event.target.value)} /></label><label>标签<input value={tags} onChange={event => setTags(event.target.value)} placeholder="用逗号分隔" /></label><button className="button primary">保存元数据</button></form></section><section className="card-column"><h2>已保存参考</h2>{cards.length ? cards.map(card => <article className="external-card" key={card.id}><span className="result-kind">{card.card_type === 'douyin' ? '抖音参考' : '一般 URL'}</span><b>{card.title}</b><small>{card.author || '未署名'}{card.tags.length ? ` · ${card.tags.join('、')}` : ''}</small><p>{card.notes}</p><a href={card.url} target="_blank" rel="noreferrer" onClick={() => card.card_type === 'douyin' && window.alert('将在原始页面中手工定位内容。源知库不会提取或预览抖音内容。')}><ExternalLink size={16}/>在浏览器打开原 URL</a></article>) : <Empty icon={<Video size={36}/>} text="尚无外部卡" />}</section></div>
}

function TransfersPage({ onMessage }: { onMessage: (message: string) => void }) {
  const [backups, setBackups] = useState<Backup[]>([]); const [target, setTarget] = useState(''); const [archive, setArchive] = useState('')
  const load = useCallback(async () => { try { setBackups(await request<Backup[]>('/backups')) } catch (error) { onMessage(error instanceof Error ? error.message : '读取备份失败') } }, [onMessage])
  useEffect(() => { void load() }, [load])
  const backup = async () => { try { await request('/backups', { method: 'POST' }); await load(); onMessage('备份已创建并完成 SHA-256 校验') } catch (error) { onMessage(error instanceof Error ? error.message : '备份失败') } }
  const exportData = async () => { if (!window.confirm('导出会包含原始 artifact、派生数据和逻辑记录，不包含凭据、原路径或日志正文。确认继续？')) return; try { const result = await request<{ archive_path: string }>('/exports', { method: 'POST', body: JSON.stringify({ confirmed: true }) }); onMessage(`已创建并校验导出归档：${result.archive_path}`) } catch (error) { onMessage(error instanceof Error ? error.message : '导出失败') } }
  const restore = async (id: string) => { if (!target) { onMessage('填写一个不存在或为空的新数据根'); return } try { await request(`/backups/${id}/restore`, { method: 'POST', body: JSON.stringify({ target_data_root: target }) }); onMessage('已还原到新的数据根并校验 artifact') } catch (error) { onMessage(error instanceof Error ? error.message : '还原失败') } }
  const reimport = async () => { if (!archive) return; try { const output = await request<{ report: { reason: string } }>('/reimports', { method: 'POST', body: JSON.stringify({ archive_path: archive }) }); onMessage(output.report.reason) } catch (error) { onMessage(error instanceof Error ? error.message : '再导入失败') } }
  return <div className="page split-page"><section><PageHeader title="备份、还原与导出"/><div className="transfer-actions"><button className="button secondary" onClick={() => void backup()}><ArchiveRestore size={17}/>立即备份</button><button className="button primary" onClick={() => void exportData()}><HardDriveDownload size={17}/>确认并导出</button></div><h2>可还原备份</h2>{backups.length ? <div className="backup-list">{backups.map(item => <article className="backup" key={item.id}><div><b>{item.archive_name}</b><small>{date(item.created_at)} · {stateLabel(item.state)}</small></div><button className="icon-button" title="还原到新数据根" onClick={() => void restore(item.id)}><ArchiveRestore size={18}/></button></article>)}</div> : <p className="muted">尚无完成的备份。</p>}</section><aside className="transfer-form"><label>新数据根（仅还原）<input value={target} onChange={event => setTarget(event.target.value)} placeholder="E:\\新位置\\data" /></label><p className="hint">还原不覆盖当前数据根，目标必须不存在或为空。</p><label>导出归档路径（再导入）<input value={archive} onChange={event => setArchive(event.target.value)} placeholder="E:\\...\\export-*.zip" /></label><button className="button secondary" onClick={() => void reimport()}><Import size={17}/>校验并再导入</button></aside></div>
}

function SettingsPage({ onMessage }: { onMessage: (message: string) => void }) {
  const [settings, setSettings] = useState<Record<string, string>>({})
  useEffect(() => { void request<Record<string, string>>('/settings').then(setSettings).catch(error => onMessage(error instanceof Error ? error.message : '读取设置失败')) }, [onMessage])
  const save = async (event: React.FormEvent) => { event.preventDefault(); try { const result = await request<Record<string, string>>('/settings', { method: 'PUT', body: JSON.stringify({ parser_timeout_seconds: Number(settings.parser_timeout_seconds), parser_no_progress_seconds: Number(settings.parser_no_progress_seconds), max_retry_attempts: Number(settings.max_retry_attempts) }) }); setSettings(result); onMessage('设置已保存到本地 state') } catch (error) { onMessage(error instanceof Error ? error.message : '保存设置失败') } }
  return <div className="page narrow"><PageHeader title="设置"/><form className="form-stack" onSubmit={save}><label>解析总超时（秒）<input type="number" min="60" max="86400" value={settings.parser_timeout_seconds || ''} onChange={event => setSettings(current => ({ ...current, parser_timeout_seconds: event.target.value }))}/></label><label>无进度断路器（秒）<input type="number" min="60" max="86400" value={settings.parser_no_progress_seconds || ''} onChange={event => setSettings(current => ({ ...current, parser_no_progress_seconds: event.target.value }))}/></label><label>最大重试次数<input type="number" min="0" max="10" value={settings.max_retry_attempts || ''} onChange={event => setSettings(current => ({ ...current, max_retry_attempts: event.target.value }))}/></label><button className="button primary">保存设置</button></form><section className="policy-list"><h2>本地运行策略</h2><div><Check size={16}/>仅绑定 127.0.0.1</div><div><Check size={16}/>无遥测、无本地 HTTPS、无加密层</div><div><Check size={16}/>解析仅本地回退，禁止静默云服务</div><div><Check size={16}/>操作日志不记录正文、路径或令牌</div></section></div>
}

function Empty({ icon, text }: { icon: React.ReactNode; text: string }) { return <div className="empty">{icon}<span>{text}</span></div> }
