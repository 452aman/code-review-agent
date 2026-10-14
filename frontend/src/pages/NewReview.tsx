import Editor from '@monaco-editor/react'
import { useMutation } from '@tanstack/react-query'
import { FilePlus2, GitPullRequest, X } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, errMsg } from '../api'

const SAMPLE = `import sqlite3, requests

DB_PASSWORD = "hunter2hunter2"

def get_user(name, cache={}):
    conn = sqlite3.connect("app.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE name = '%s'" % name)
    row = cur.fetchone()
    cache[name] = row
    return row

def load_all(ids):
    out = []
    for i in ids:
        r = requests.get(f"http://api.internal/users/{i}")
        out.append(r.json())
    return out
`
const langOf = (p: string) => ({ py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript', java: 'java', go: 'go', rb: 'ruby', php: 'php', sql: 'sql' } as Record<string, string>)[p.split('.').pop() ?? ''] ?? 'plaintext'

interface F { path: string; content: string }

export default function NewReview() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'code' | 'pr'>('code')
  const [files, setFiles] = useState<F[]>([{ path: 'service.py', content: '' }])
  const [active, setActive] = useState(0)
  const [prUrl, setPrUrl] = useState('')
  const [post, setPost] = useState(false)
  const submit = useMutation({
    mutationFn: async () => (await api.post('/reviews', mode === 'pr' ? { pr_url: prUrl, post_to_github: post } : { files: files.filter((f) => f.content.trim()) })).data,
    onSuccess: (r) => navigate(`/reviews/${r.id}`),
  })
  const update = (patch: Partial<F>) => setFiles(files.map((f, i) => (i === active ? { ...f, ...patch } : f)))
  const addFile = () => { setFiles([...files, { path: `file${files.length + 1}.py`, content: '' }]); setActive(files.length) }
  const removeFile = (i: number) => { const n = files.filter((_, j) => j !== i); setFiles(n); setActive(Math.max(0, Math.min(active, n.length - 1))) }
  const canSubmit = mode === 'pr' ? /github\.com\/.+\/pull\/\d+/.test(prUrl) : files.some((f) => f.content.trim())

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <h1 className="text-2xl font-bold">New review</h1>
      <div className="inline-flex rounded-lg bg-slate-200/70 p-1 text-sm">
        <button onClick={() => setMode('code')} className={`rounded-md px-4 py-1.5 font-medium ${mode === 'code' ? 'bg-white shadow-sm' : 'text-slate-500'}`}>Paste code</button>
        <button onClick={() => setMode('pr')} className={`flex items-center gap-1 rounded-md px-4 py-1.5 font-medium ${mode === 'pr' ? 'bg-white shadow-sm' : 'text-slate-500'}`}><GitPullRequest size={14} />GitHub PR</button>
      </div>
      {mode === 'pr' ? (
        <div className="card space-y-3 p-6">
          <label className="label">Pull request URL</label>
          <input className="input" placeholder="https://github.com/owner/repo/pull/123" value={prUrl} onChange={(e) => setPrUrl(e.target.value)} />
          <p className="text-sm text-slate-500">Only lines changed in the PR are reviewed. Private repos need <code>GITHUB_TOKEN</code> on the server.</p>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={post} onChange={(e) => setPost(e.target.checked)} />Post findings back to the PR as inline comments (needs a write token)</label>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <div className="flex items-center gap-1 overflow-x-auto bg-ink-900 px-2 pt-2">
            {files.map((f, i) => (
              <div key={i} className={`flex items-center gap-2 rounded-t-lg px-3 py-1.5 text-sm ${i === active ? 'bg-[#1e1e1e] text-white' : 'text-slate-400 hover:text-white'}`}>
                <button onClick={() => setActive(i)}>{f.path}</button>
                {files.length > 1 && <button onClick={() => removeFile(i)}><X size={12} /></button>}
              </div>
            ))}
            <button onClick={addFile} className="ml-1 rounded p-1.5 text-slate-400 hover:text-white" aria-label="Add file"><FilePlus2 size={16} /></button>
          </div>
          <div className="flex items-center gap-3 border-b border-slate-200 bg-slate-50 px-3 py-2">
            <label className="text-xs font-medium uppercase text-slate-500">File name</label>
            <input className="input max-w-xs py-1" value={files[active].path} onChange={(e) => update({ path: e.target.value })} />
            <button className="btn-ghost ml-auto" onClick={() => update({ content: SAMPLE, path: 'service.py' })}>Load sample</button>
          </div>
          <Editor height="380px" theme="vs-dark" language={langOf(files[active].path)} path={`input-${active}-${files[active].path}`} value={files[active].content}
            onChange={(v) => update({ content: v ?? '' })} options={{ minimap: { enabled: false }, fontSize: 13 }} />
        </div>
      )}
      {submit.isError && <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">{errMsg(submit.error)}</p>}
      <button className="btn" disabled={!canSubmit || submit.isPending} onClick={() => submit.mutate()}>{submit.isPending ? 'Starting…' : 'Run review'}</button>
    </div>
  )
}
