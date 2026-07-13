'use client'

// Componentes visuales compartidos del sistema de diseño AgroIA.

// Etiqueta de caravana: réplica visual de la caravana amarilla real,
// para que el usuario reconozca el número igual que en el corral.
export function CaravanaTag({
  numero,
  size = 'md',
  apagada = false,
}: {
  numero: string | number | null | undefined
  size?: 'sm' | 'md' | 'lg'
  apagada?: boolean
}) {
  const font = size === 'lg' ? 'text-4xl' : size === 'md' ? 'text-2xl' : 'text-lg'
  const w = size === 'lg' ? 'w-24' : size === 'md' ? 'w-[76px]' : 'w-14'
  return (
    <div className={`caravana-tag ${w} ${apagada ? 'opacity-50' : ''} flex-shrink-0`}>
      <div className="agujero" />
      <div className={`${font} font-semibold text-[#1C1917] leading-none tabular-nums`}>
        {numero ?? '?'}
      </div>
    </div>
  )
}

// Pastilla de estado con el código de color único de toda la app:
// rojo = actuar ahora · ámbar = se viene · verde = hecho ·
// azul = necesita revisión humana · gris = pasado/perdido
export type Tono = 'rojo' | 'ambar' | 'verde' | 'azul' | 'gris'

const TONOS: Record<Tono, { bg: string; texto: string; punto: string }> = {
  rojo:  { bg: 'bg-[#FCEBEB]', texto: 'text-[#791F1F]', punto: 'bg-[#E24B4A]' },
  ambar: { bg: 'bg-[#FAEEDA]', texto: 'text-[#633806]', punto: 'bg-[#EF9F27]' },
  verde: { bg: 'bg-[#EAF3DE]', texto: 'text-[#27500A]', punto: 'bg-[#639922]' },
  azul:  { bg: 'bg-[#E6F1FB]', texto: 'text-[#0C447C]', punto: 'bg-[#378ADD]' },
  gris:  { bg: 'bg-[#F1EFE8]', texto: 'text-[#444441]', punto: 'bg-[#888780]' },
}

export function Pill({ tono, children }: { tono: Tono; children: React.ReactNode }) {
  const t = TONOS[tono]
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-0.5 rounded-full font-medium ${t.bg} ${t.texto}`}>
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${t.punto}`} />
      {children}
    </span>
  )
}