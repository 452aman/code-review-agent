import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, Download, EyeOff, Lightbulb, RotateCcw, ShieldAlert, ShieldCheck } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, sevColor } from '../api'
import CodeViewer from '../components/CodeViewer'
import Pipeline from '../components/Pipeline'
import ScoreRing from '../components/ScoreRing'
import { SEVERITIES, type Finding, type PipelineEvent, type Review } from '../types'

function FindingCard({ f, active, onSelect, onTriage, before, baseline }: { f: Finding; active: boolean; onSelect: () => void; onTriage: (t: Finding['triage']) => void; before: string; baseline: boolean }) {
  return (
    <div onClick={onSelect} className={`cursor-pointer rounded-lg border p-3 text-sm transition ${active ? 'border-accent bg-violet-400/5' : 'border-white/10 bg-white/[0.04] hover:border-white/15'} ${f.triage === 'dismissed' ? 'opacity-50' : ''}`}>
      <div className="flex items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase ring-1 ${sevColor[f.severity]}`}>{f.severity}</span>
        <span className="text-xs text-slate-500">{f.file}:{f.line}</span>
        {baseline && <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${f.is_new ? 'bg-emerald-400/15 text-emerald-300' : 'bg-white/10 text-slate-400'}`}>{f.is_new ? 'new' : 'existing'}</span>}
        <span className="ml-auto rounded bg-white/[0.07] px-1.5 py-0.5 text-[11px] text-slate-400">{f.source.replace('_agent', '')}</span>
      </div>
      <div className="mt-1.5 font-medium">{f.title}</div>
      {active && (
        <div className="mt-2 space-y-2">
          {f.explanation && <p className="text-slate-300">{f.explanation}</p>}
          {f.suggestion && <p className="flex gap-2 rounded bg-emerald-400/10 p-2 text-emerald-200"><Lightbulb size={14} className="mt-0.5 shrink-0" />{f.suggestion}</p>}
          {f.fix && (
            <div className="overflow-x-auto rounded bg-black/50 p-2 font-mono text-xs">
              {before && <div className="whitespace-pre text-rose-300"><span className="select-none text-rose-500">- </span>{before.trim()}</div>}
              <div className="whitespace-pre text-emerald-300"><span className="select-none text-emerald-500">+ </span>{f.fix.trim()}</div>
            </div>)}
          <div className="flex items-center gap-2 pt-1">
            <button className="btn-ghost" onClick={(e) => { e.stopPropagation(); onTriage(f.triage === 'accepted' ? 'open' : 'accepted') }}><Check size={14} />{f.triage === 'accepted' ? 'Accepted' : 'Accept'}</button>
            <button className="btn-ghost" onClick={(e) => { e.stopPropagation(); onTriage(f.triage === 'dismissed' ? 'open' : 'dismissed') }}><EyeOff size={14} />{f.triage === 'dismissed' ? 'Restore' : 'Dismiss'}</button>
            {f.rule_id && <span className="ml-auto text-xs text-slate-500">{f.rule_id}</span>}
          </div>
        </div>
      )}
    </div>
  )
}

export default function ReviewDetail() {
  const { id } = useParams()
  const qc = useQueryClient()
  const [live, setLive] = useState<PipelineEvent[]>([])
  const { data: review } = useQuery({
    queryKey: ['review', id], queryFn: async () => (await api.get<Review>(`/reviews/${id}`)).data,
    refetchInterval: (q) => (['queued', 'running'].includes(q.state.data?.status ?? '') ? 2500 : false),
  })
  const [file, setFile] = useState(0)
  const [sev, setSev] = useState<string>('all')
  const [onlyNew, setOnlyNew] = useState(true)
  const [selected, setSelected] = useState<Finding | null>(null)

  // Follow the pipeline live over server-sent events; the final state still comes from the REST call.
  useEffect(() => {
    if (!review || !['queued', 'running'].includes(review.status)) return
    const es = new EventSource(`/api/v1/reviews/${id}/events`)
    es.onmessage = (m) => {
      const ev: PipelineEvent = JSON.parse(m.data)
      setLive((l) => [...l, ev])
      if (ev.node === 'complete') { es.close(); qc.invalidateQueries({ queryKey: ['review', id] }); qc.invalidateQueries({ queryKey: ['stats'] }) }
    }
    es.onerror = () => es.close()
    return () => es.close()
  }, [review?.status, id, qc])

  const findings = review?.findings ?? []
  const hasBaseline = !!review?.baseline_id
  const shown = useMemo(() => findings.filter((f) => (sev === 'all' || f.severity === sev) && (!hasBaseline || !onlyNew || f.is_new)), [findings, sev, onlyNew, hasBaseline])
  const counts = review?.stats.by_severity
  const triage = async (f: Finding, t: Finding['triage']) => {
    await api.patch(`/findings/${f.id}`, { triage: t })
    qc.setQueryData<Review>(['review', id], (r) => r && { ...r, findings: r.findings.map((x) => (x.id === f.id ? { ...x, triage: t } : x)) })
  }
  const download = async (path: string, name: string) => {
    const r = await api.get(`/reviews/${id}/${path}`, { responseType: 'blob' }).catch(() => null)
    if (!r) { alert('Nothing to download for this review.'); return }
    const url = URL.createObjectURL(r.data); Object.assign(document.createElement('a'), { href: url, download: name }).click(); URL.revokeObjectURL(url)
  }
  const rerun = async () => { await api.post(`/reviews/${id}/rerun`); qc.invalidateQueries({ queryKey: ['review', id] }) }
  if (!review) return <p className="text-slate-400">Loading…</p>
  const events = review.status === 'done' || review.status === 'failed' ? review.events : live
  const current = review.files[file] ?? review.files[0]
  const running = review.status === 'queued' || review.status === 'running'

  return (
    <div className="space-y-5">
      <div className="card flex flex-wrap items-center gap-5 p-5">
        <ScoreRing score={review.score} grade={review.grade} />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-xl font-bold">{review.title}</h1>
          <p className="text-xs text-slate-400">{review.source.replace('_', ' ')}{review.source_ref && <> · <a className="text-accent hover:underline" href={review.source_ref} target="_blank" rel="noreferrer">open PR</a></>}{review.duration_s != null && ` · ${review.duration_s}s`}</p>
          {review.summary && <p className="mt-2 text-sm text-slate-300">{review.summary}</p>}
          {review.error && <p className="mt-2 flex items-center gap-1 text-sm text-rose-400"><AlertTriangle size={14} />{review.error}</p>}
          {review.status === 'done' && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              {review.gate.passed !== undefined && (review.gate.passed
                ? <span className="flex items-center gap-1 rounded-full bg-emerald-400/15 px-2.5 py-1 font-semibold text-emerald-300"><ShieldCheck size={13} />Quality gate passed</span>
                : <span title={(review.gate.reasons ?? []).join('; ')} className="flex items-center gap-1 rounded-full bg-rose-400/15 px-2.5 py-1 font-semibold text-rose-300"><ShieldAlert size={13} />Quality gate failed: {(review.gate.reasons ?? []).join('; ')}</span>)}
              {hasBaseline && <span className="rounded-full bg-white/10 px-2.5 py-1 text-slate-300">vs previous review: <b className="text-emerald-300">{review.stats.new ?? 0} new</b> · {review.stats.existing ?? 0} existing · <b className="text-sky-300">{review.stats.fixed ?? 0} fixed</b></span>}
              {!!review.stats.suppressed_by_config && <span className="rounded-full bg-white/10 px-2.5 py-1 text-slate-300">{review.stats.suppressed_by_config} hidden by repo config</span>}
              <span className="ml-auto flex gap-1.5">
                <button className="btn-ghost" onClick={() => download('sarif', `review-${review.id.slice(0, 8)}.sarif`)}><Download size={13} />SARIF</button>
                {review.source === 'paste' && <button className="btn-ghost" onClick={() => download('patch.diff', `review-${review.id.slice(0, 8)}.diff`)}><Download size={13} />Patch</button>}
                <button className="btn-ghost" onClick={rerun}><RotateCcw size={13} />Re-run</button>
              </span>
            </div>)}
          {review.status === 'failed' && <button className="btn-ghost mt-3" onClick={rerun}><RotateCcw size={13} />Re-run</button>}
        </div>
        {counts && <div className="flex gap-2">{SEVERITIES.map((s) => <div key={s} className={`rounded-lg px-3 py-2 text-center ring-1 ${sevColor[s]}`}><div className="text-lg font-bold leading-none">{counts[s] ?? 0}</div><div className="text-[10px] uppercase">{s}</div></div>)}</div>}
      </div>

      <div className="grid gap-5 lg:grid-cols-[260px_1fr]">
        <div className="card h-fit p-4"><h2 className="mb-3 text-sm font-semibold">{running ? 'Reviewing…' : 'Pipeline'}</h2><Pipeline events={events} />
          {!!review.stats.rejected_hallucinations && <p className="mt-3 rounded bg-white/[0.04] p-2 text-xs text-slate-400">Verifier rejected {review.stats.rejected_hallucinations} unsupported finding(s) from the agents.</p>}</div>

        {!running && current && (
          <div className="grid gap-5 xl:grid-cols-[1fr_380px]">
            <div className="card overflow-hidden">
              <div className="flex gap-1 overflow-x-auto bg-black/40 px-2 pt-2">{review.files.map((f, i) => (
                <button key={f.path} onClick={() => setFile(i)} className={`rounded-t-lg px-3 py-1.5 text-sm ${i === file ? 'bg-[#1e1e1e] text-white' : 'text-slate-500 hover:text-white'}`}>{f.path}<span className="ml-1.5 text-xs text-rose-300">{findings.filter((x) => x.file === f.path).length || ''}</span></button>))}</div>
              <CodeViewer file={current} findings={findings} focus={selected} />
            </div>
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-1">{hasBaseline && <label className="mr-2 flex items-center gap-1.5 text-xs text-slate-300"><input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />Only new</label>}{['all', ...SEVERITIES].map((s) => <button key={s} onClick={() => setSev(s)} className={`rounded-full px-3 py-1 text-xs font-medium ${sev === s ? 'bg-black/40 text-white' : 'bg-white/[0.04] text-slate-300 ring-1 ring-white/10'}`}>{s}</button>)}</div>
              <div className="max-h-[560px] space-y-2 overflow-y-auto pr-1">
                {shown.length === 0 && <p className="card p-6 text-center text-sm text-slate-400">{findings.length ? 'No findings at this severity.' : '🎉 No issues found.'}</p>}
                {shown.map((f) => <FindingCard key={f.id} f={f} baseline={hasBaseline} before={(review.files.find((x) => x.path === f.file)?.content.split('\n')[f.line - 1]) ?? ''} active={selected?.id === f.id}
                  onSelect={() => { setSelected(f); const i = review.files.findIndex((x) => x.path === f.file); if (i >= 0) setFile(i) }} onTriage={(t) => triage(f, t)} />)}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
