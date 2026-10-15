import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, EyeOff, Lightbulb } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, sevColor } from '../api'
import CodeViewer from '../components/CodeViewer'
import Pipeline from '../components/Pipeline'
import ScoreRing from '../components/ScoreRing'
import { SEVERITIES, type Finding, type PipelineEvent, type Review } from '../types'

function FindingCard({ f, active, onSelect, onTriage }: { f: Finding; active: boolean; onSelect: () => void; onTriage: (t: Finding['triage']) => void }) {
  return (
    <div onClick={onSelect} className={`cursor-pointer rounded-lg border p-3 text-sm transition ${active ? 'border-accent bg-violet-400/5' : 'border-white/10 bg-white/[0.04] hover:border-white/15'} ${f.triage === 'dismissed' ? 'opacity-50' : ''}`}>
      <div className="flex items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold uppercase ring-1 ${sevColor[f.severity]}`}>{f.severity}</span>
        <span className="text-xs text-slate-500">{f.file}:{f.line}</span>
        <span className="ml-auto rounded bg-white/[0.07] px-1.5 py-0.5 text-[11px] text-slate-400">{f.source.replace('_agent', '')}</span>
      </div>
      <div className="mt-1.5 font-medium">{f.title}</div>
      {active && (
        <div className="mt-2 space-y-2">
          {f.explanation && <p className="text-slate-300">{f.explanation}</p>}
          {f.suggestion && <p className="flex gap-2 rounded bg-emerald-400/10 p-2 text-emerald-200"><Lightbulb size={14} className="mt-0.5 shrink-0" />{f.suggestion}</p>}
          {f.fix && <pre className="overflow-x-auto rounded bg-black/50 p-2 text-xs text-emerald-300"><code>{f.fix}</code></pre>}
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
  const shown = useMemo(() => findings.filter((f) => sev === 'all' || f.severity === sev), [findings, sev])
  const counts = review?.stats.by_severity
  const triage = async (f: Finding, t: Finding['triage']) => {
    await api.patch(`/findings/${f.id}`, { triage: t })
    qc.setQueryData<Review>(['review', id], (r) => r && { ...r, findings: r.findings.map((x) => (x.id === f.id ? { ...x, triage: t } : x)) })
  }
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
              <div className="flex flex-wrap gap-1">{['all', ...SEVERITIES].map((s) => <button key={s} onClick={() => setSev(s)} className={`rounded-full px-3 py-1 text-xs font-medium ${sev === s ? 'bg-black/40 text-white' : 'bg-white/[0.04] text-slate-300 ring-1 ring-white/10'}`}>{s}</button>)}</div>
              <div className="max-h-[560px] space-y-2 overflow-y-auto pr-1">
                {shown.length === 0 && <p className="card p-6 text-center text-sm text-slate-400">{findings.length ? 'No findings at this severity.' : '🎉 No issues found.'}</p>}
                {shown.map((f) => <FindingCard key={f.id} f={f} active={selected?.id === f.id}
                  onSelect={() => { setSelected(f); const i = review.files.findIndex((x) => x.path === f.file); if (i >= 0) setFile(i) }} onTriage={(t) => triage(f, t)} />)}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
