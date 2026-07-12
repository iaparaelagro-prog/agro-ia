'use client'
import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { supabase, type Monta } from '@/lib/supabase'
import Sidebar from '@/components/Sidebar'
import { agruparEnCelos, type Celo } from '@/lib/celos'

type Inseminacion = {
  id: number
  id_negocio: string | null
  celo_inicio: string
  fecha_inseminacion: string
}

type TipoEvento = 'celo' | 'inseminacion' | 'celo_esperado' | 'chequeo'

type Evento = {
  tipo: TipoEvento
  fecha: Date
  caravana: string | null
  detalle: string
}

const DIA = 24 * 60 * 60 * 1000
const CICLO_DIAS = 21
const CICLO_MARGEN = 2
const CHEQUEO_PRENEZ_DIAS = 32

const TIPO_INFO: Record<TipoEvento, { label: string; dot: string; pill: string }> = {
  celo:          { label: 'Celo detectado',   dot: 'bg-red-400',    pill: 'bg-red-50 text-red-700' },
  inseminacion:  { label: 'Inseminación',     dot: 'bg-green-500',  pill: 'bg-green-50 text-green-700' },
  celo_esperado: { label: 'Celo esperado',    dot: 'bg-blue-400',   pill: 'bg-blue-50 text-blue-700' },
  chequeo:       { label: 'Chequeo preñez',   dot: 'bg-purple-400', pill: 'bg-purple-50 text-purple-700' },
}

const MESES = ['Enero','Febrero','Marzo','Abril','Mayo','Junio','Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']
const DIAS_SEMANA = ['Lun','Mar','Mié','Jue','Vie','Sáb','Dom']

function mismoDia(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}
function hhmm(d: Date) {
  return d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
}

export default function CalendarioPage() {
  const [montas, setMontas] = useState<Monta[]>([])
  const [inseminaciones, setInseminaciones] = useState<Inseminacion[]>([])
  const [loading, setLoading] = useState(true)
  const hoy = new Date()
  const [anio, setAnio] = useState(hoy.getFullYear())
  const [mes, setMes] = useState(hoy.getMonth())
  const [diaSel, setDiaSel] = useState<Date | null>(null)

  const cargar = useCallback(async () => {
    setLoading(true)
    const [{ data: dm }, { data: di }] = await Promise.all([
      supabase
        .from('montas')
        .select(`*, frames_formateados ( ruta_archivo ), vacas ( id_negocio, ruta_fotos )`)
        .order('fecha_monta', { ascending: false })
        .limit(1000),
      supabase.from('inseminaciones').select('id, id_negocio, celo_inicio, fecha_inseminacion'),
    ])
    if (dm) setMontas(dm)
    if (di) setInseminaciones(di)
    setLoading(false)
  }, [])

  useEffect(() => { cargar() }, [cargar])

  // --- eventos ---
  const celos = agruparEnCelos(montas)

  const eventos: Evento[] = []

  for (const c of celos) {
    eventos.push({
      tipo: 'celo',
      fecha: new Date(c.inicio),
      caravana: c.idNegocio,
      detalle: `${c.cantidadMontas} ${c.cantidadMontas === 1 ? 'monta' : 'montas'} · ${hhmm(new Date(c.inicio))}`,
    })
  }

  for (const i of inseminaciones) {
    eventos.push({
      tipo: 'inseminacion',
      fecha: new Date(i.fecha_inseminacion),
      caravana: i.id_negocio != null ? String(i.id_negocio) : null,
      detalle: `sobre celo del ${new Date(i.celo_inicio).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' })}`,
    })
    eventos.push({
      tipo: 'chequeo',
      fecha: new Date(new Date(i.fecha_inseminacion).getTime() + CHEQUEO_PRENEZ_DIAS * DIA),
      caravana: i.id_negocio != null ? String(i.id_negocio) : null,
      detalle: `~${CHEQUEO_PRENEZ_DIAS} días post servicio`,
    })
  }

  // Próximo celo esperado: por vaca identificada, a partir de su último celo
  const ultimoCeloPorVaca = new Map<string, Celo>()
  for (const c of celos) {
    if (!c.idNegocio) continue
    const prev = ultimoCeloPorVaca.get(c.idNegocio)
    if (!prev || new Date(c.inicio) > new Date(prev.inicio)) {
      ultimoCeloPorVaca.set(c.idNegocio, c)
    }
  }
  ultimoCeloPorVaca.forEach((c, caravana) => {
    const base = new Date(c.inicio).getTime()
    for (let d = CICLO_DIAS - CICLO_MARGEN; d <= CICLO_DIAS + CICLO_MARGEN; d++) {
      eventos.push({
        tipo: 'celo_esperado',
        fecha: new Date(base + d * DIA),
        caravana,
        detalle: `ciclo de ~${CICLO_DIAS} días desde el último celo`,
      })
    }
  })

  // --- grilla del mes ---
  const primerDia = new Date(anio, mes, 1)
  // getDay(): 0=domingo. Queremos lunes=0.
  const offset = (primerDia.getDay() + 6) % 7
  const diasEnMes = new Date(anio, mes + 1, 0).getDate()
  const celdas: (Date | null)[] = [
    ...Array(offset).fill(null),
    ...Array.from({ length: diasEnMes }, (_, i) => new Date(anio, mes, i + 1)),
  ]
  while (celdas.length % 7 !== 0) celdas.push(null)

  function eventosDe(d: Date) {
    return eventos.filter(e => mismoDia(e.fecha, d))
  }

  function mesAnterior() {
    setDiaSel(null)
    if (mes === 0) { setMes(11); setAnio(a => a - 1) } else setMes(m => m - 1)
  }
  function mesSiguiente() {
    setDiaSel(null)
    if (mes === 11) { setMes(0); setAnio(a => a + 1) } else setMes(m => m + 1)
  }
  function irAHoy() {
    setAnio(hoy.getFullYear())
    setMes(hoy.getMonth())
    setDiaSel(new Date(hoy.getFullYear(), hoy.getMonth(), hoy.getDate()))
  }

  const eventosDiaSel = diaSel ? eventosDe(diaSel) : []

  return (
    <div className="flex min-h-screen w-full">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">

        <div className="h-12 bg-white border-b border-gray-200 flex items-center px-5 gap-3 flex-shrink-0 sticky top-0 z-10">
          <span className="text-sm font-medium text-gray-900 flex-1">Calendario</span>
          <button onClick={irAHoy} className="h-8 px-3 text-xs border border-gray-200 rounded-lg hover:bg-gray-50">
            Hoy
          </button>
          <div className="flex items-center gap-1">
            <button onClick={mesAnterior} className="w-8 h-8 flex items-center justify-center border border-gray-200 rounded-lg hover:bg-gray-50 text-gray-600">‹</button>
            <span className="text-sm font-medium text-gray-900 w-40 text-center">{MESES[mes]} {anio}</span>
            <button onClick={mesSiguiente} className="w-8 h-8 flex items-center justify-center border border-gray-200 rounded-lg hover:bg-gray-50 text-gray-600">›</button>
          </div>
        </div>

        <div className="flex-1 p-5 flex flex-col gap-4">

          {/* Leyenda */}
          <div className="flex items-center gap-4 flex-wrap">
            {(Object.keys(TIPO_INFO) as TipoEvento[]).map(t => (
              <div key={t} className="flex items-center gap-1.5">
                <span className={`w-2.5 h-2.5 rounded-full ${TIPO_INFO[t].dot}`} />
                <span className="text-xs text-gray-600">{TIPO_INFO[t].label}</span>
              </div>
            ))}
            {loading && <span className="text-xs text-gray-400 ml-auto">Cargando...</span>}
          </div>

          <div className="flex gap-4 items-start">

            {/* Grilla mensual */}
            <div className="flex-1 min-w-0 bg-white border border-gray-200 rounded-xl overflow-hidden">
              <div className="grid grid-cols-7 border-b border-gray-100">
                {DIAS_SEMANA.map(d => (
                  <div key={d} className="text-xs text-gray-400 font-medium text-center py-2">{d}</div>
                ))}
              </div>
              <div className="grid grid-cols-7">
                {celdas.map((d, i) => {
                  if (!d) return <div key={i} className="min-h-[92px] border-b border-r border-gray-50" />
                  const evs = eventosDe(d)
                  const esHoy = mismoDia(d, hoy)
                  const esSel = diaSel != null && mismoDia(d, diaSel)
                  // resumen por tipo para no saturar la celda
                  const porTipo = new Map<TipoEvento, number>()
                  evs.forEach(e => porTipo.set(e.tipo, (porTipo.get(e.tipo) ?? 0) + 1))
                  return (
                    <div
                      key={i}
                      onClick={() => setDiaSel(d)}
                      className={`min-h-[92px] border-b border-r border-gray-50 p-1.5 cursor-pointer transition-colors ${
                        esSel ? 'bg-blue-50' : 'hover:bg-gray-50'
                      }`}
                    >
                      <div className={`text-xs w-6 h-6 flex items-center justify-center rounded-full mb-1 ${
                        esHoy ? 'bg-blue-600 text-white font-medium' : 'text-gray-500'
                      }`}>
                        {d.getDate()}
                      </div>
                      <div className="flex flex-col gap-0.5">
                        {Array.from(porTipo.entries()).map(([t, n]) => (
                          <div key={t} className={`text-[10px] px-1.5 py-0.5 rounded flex items-center gap-1 ${TIPO_INFO[t].pill}`}>
                            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${TIPO_INFO[t].dot}`} />
                            <span className="truncate">{n} {TIPO_INFO[t].label.toLowerCase()}{n > 1 ? 's' : ''}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Detalle del día */}
            <div className="w-80 flex-shrink-0 sticky top-16">
              <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                <div className="px-4 py-2.5 border-b border-gray-100">
                  <span className="text-sm font-medium text-gray-900">
                    {diaSel
                      ? diaSel.toLocaleDateString('es-AR', { weekday: 'long', day: 'numeric', month: 'long' })
                      : 'Seleccioná un día'}
                  </span>
                </div>
                <div className="p-3">
                  {!diaSel ? (
                    <div className="text-sm text-gray-400 py-6 text-center">
                      Clic en un día del calendario para ver el detalle.
                    </div>
                  ) : eventosDiaSel.length === 0 ? (
                    <div className="text-sm text-gray-400 py-6 text-center">
                      Sin eventos este día.
                    </div>
                  ) : (
                    <div className="flex flex-col gap-2">
                      {eventosDiaSel
                        .sort((a, b) => a.fecha.getTime() - b.fecha.getTime())
                        .map((e, i) => (
                          <div key={i} className="flex items-start gap-2.5 bg-gray-50 rounded-lg p-2.5">
                            <span className={`w-2.5 h-2.5 rounded-full mt-1 flex-shrink-0 ${TIPO_INFO[e.tipo].dot}`} />
                            <div className="flex-1 min-w-0">
                              <div className="text-sm font-medium text-gray-900">
                                {TIPO_INFO[e.tipo].label}
                                {e.caravana && (
                                  <span className="ml-1.5 text-gray-500 font-normal">· Vaca {e.caravana}</span>
                                )}
                              </div>
                              <div className="text-xs text-gray-500 mt-0.5">{e.detalle}</div>
                            </div>
                          </div>
                        ))}
                      {eventosDiaSel.some(e => e.tipo === 'celo_esperado') && (
                        <div className="text-[11px] text-gray-400 leading-tight px-1">
                          Los celos esperados son estimaciones (~21 días desde el último celo). Vigilar esas vacas estos días.
                        </div>
                      )}
                      <Link
                        href="/celos"
                        className="text-xs text-blue-600 hover:underline text-center py-1"
                      >
                        Ver eventos de celo →
                      </Link>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}