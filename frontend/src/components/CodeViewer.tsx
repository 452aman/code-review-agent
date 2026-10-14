import Editor, { type OnMount } from '@monaco-editor/react'
import { useEffect, useRef } from 'react'
import type { Finding, ReviewFile } from '../types'

type Monaco = Parameters<OnMount>[1]
type EditorInstance = Parameters<OnMount>[0]

export default function CodeViewer({ file, findings, focus }: { file: ReviewFile; findings: Finding[]; focus: Finding | null }) {
  const ref = useRef<EditorInstance | null>(null)
  const monacoRef = useRef<Monaco | null>(null)
  const decos = useRef<{ clear: () => void } | null>(null)

  const paint = () => {
    const ed = ref.current, mon = monacoRef.current
    if (!ed || !mon) return
    decos.current?.clear()
    decos.current = ed.createDecorationsCollection(
      findings.filter((f) => f.file === file.path && f.triage !== 'dismissed').map((f) => ({
        range: new mon.Range(f.line, 1, f.end_line ?? f.line, 1),
        options: { isWholeLine: true, className: `lf-${f.severity}`, hoverMessage: { value: `**${f.severity.toUpperCase()}** ${f.title}` } },
      })),
    )
  }

  useEffect(paint, [findings, file])
  useEffect(() => {
    if (focus && focus.file === file.path) { ref.current?.revealLineInCenter(focus.line); ref.current?.setPosition({ lineNumber: focus.line, column: 1 }) }
  }, [focus, file])

  return (
    <Editor height="520px" language={file.language === 'text' ? 'plaintext' : file.language} value={file.content} theme="vs-dark" path={file.path}
      options={{ readOnly: true, minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, renderLineHighlight: 'none' }}
      onMount={(ed, mon) => { ref.current = ed; monacoRef.current = mon; paint() }} />
  )
}
