'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

const navItems = [
  { href: '/', label: 'Hoy', seccion: 'Panel' },
  { href: '/calendario', label: 'Calendario', seccion: 'Panel' },
  { href: '/celos', label: 'Eventos de celo', seccion: 'Detección' },
  { href: '/montas', label: 'Montas', seccion: 'Detección' },
  { href: '/vacas', label: 'Vacas', seccion: 'Gestión' },
  { href: '/camaras', label: 'Cámaras', seccion: 'Sistema' },
]

export default function Sidebar() {
  const pathname = usePathname()
  return (
    <div className="w-52 flex-shrink-0 bg-[#14532D] flex flex-col sticky top-0 h-screen">
      <div className="px-4 py-4 border-b border-[#166534]">
        <div className="font-medium text-[#F0FDF4]">AgroIA</div>
        <div className="text-xs text-[#86EFAC] mt-1">Mi Tambo</div>
      </div>
      <nav className="flex-1 py-2 overflow-y-auto">
        {['Panel', 'Detección', 'Gestión', 'Sistema'].map(sec => (
          <div key={sec}>
            <div className="px-4 py-2 text-[11px] font-medium text-[#4ADE80] uppercase tracking-wider">
              {sec}
            </div>
            {navItems.filter(i => i.seccion === sec).map(item => (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2 px-4 py-2 text-sm transition-colors ${
                  pathname === item.href
                    ? 'bg-[#166534] text-[#F0FDF4] font-medium border-l-[3px] border-[#86EFAC]'
                    : 'text-[#BBF7D0] hover:bg-[#166534]/60 hover:text-[#F0FDF4]'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </div>
        ))}
      </nav>
      <div className="px-4 py-3 border-t border-[#166534] text-[10px] text-[#4ADE80]">
        Detección de celos con IA
      </div>
    </div>
  )
}