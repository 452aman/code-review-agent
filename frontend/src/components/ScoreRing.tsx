import { gradeColor } from '../api'

export default function ScoreRing({ score, grade, size = 96 }: { score: number | null; grade: string | null; size?: number }) {
  const r = size / 2 - 8
  const c = 2 * Math.PI * r
  const pct = (score ?? 0) / 100
  const color = gradeColor(grade)
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e2e8f0" strokeWidth="8" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth="8" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct)} style={{ transition: 'stroke-dashoffset .8s ease' }} />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-bold leading-none">{score ?? '–'}</span>
        <span className="text-xs font-semibold" style={{ color }}>{grade ?? ''}</span>
      </div>
    </div>
  )
}
