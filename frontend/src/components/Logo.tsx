export default function Logo({ className = 'h-7 w-7' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <defs><linearGradient id="logo-g" x1="2" y1="2" x2="30" y2="30" gradientUnits="userSpaceOnUse"><stop stopColor="#a78bfa" /><stop offset="1" stopColor="#ec4899" /></linearGradient></defs>
      <rect width="32" height="32" rx="9" fill="url(#logo-g)" />
      <path d="M16 5.5 24.5 8.6v6.7c0 5-3.4 8.4-8.5 10.7-5.1-2.3-8.5-5.7-8.5-10.7V8.6L16 5.5Z" stroke="#070a14" strokeWidth="1.9" strokeLinejoin="round"/><path d="m13.2 13.2-2.7 2.8 2.7 2.8M18.8 13.2l2.7 2.8-2.7 2.8" stroke="#070a14" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}
