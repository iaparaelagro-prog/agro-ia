'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

const navItems = [
  { href: '/montas', label: 'Montas', seccion: 'Detección' },
  { href: '/celos', label: 'Eventos de celo', seccion: 'Detección' },
  { href: '/vacas', label: 'Vacas', seccion: 'Gestión' },
  { href: '/camaras', label: 'Cámaras', seccion: 'Sistema' },
]

export default function Sidebar() {
  const pathname = usePathname()
  return (
    <div className="w-52 flex-shrink-0 bg-white border-r border-gray-200 flex flex-col">
      <div className="px-4 py-4 border-b border-gray-200">
        <div className="font-medium text-gray-900">AgroIA</div>
        <div className="text-xs text-gray-400 mt-1">Mi Tambo</div>
      </div>
      <nav className="flex-1 py-2">
        {['Detección', 'Gestión', 'Sistema'].map(sec => (
          <div key={sec}>
            <div className="px-4 py-2 text-xs font-medium text-gray-400 uppercase tracking-wider">
              {sec}
            </div>
            {navItems.filter(i => i.seccion === sec).map(item => (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2 px-4 py-2 text-sm transition-colors ${
                  pathname === item.href
                    ? 'bg-gray-100 text-gray-900 font-medium border-l-2 border-blue-500'
                    : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </div>
        ))}
      </nav>
    </div>
  )
}