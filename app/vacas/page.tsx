'use client'
import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { supabase, type Monta } from '@/lib/supabase'
import Sidebar from '@/components/Sidebar'
import { CaravanaTag } from '@/components/ui'
import { agruparEnCelos, type Celo } from '@/lib/celos'

type Vaca = {
  id: number
  id_negocio: string | number
  ruta_fotos: string | null
  fecha_alta: string | null
}

type Inseminacion = {
  id: number
  id_negocio: string | null
  celo_inicio: string
  fecha_inseminacion: string
}

type EventoHistoria =
  | { tipo: 'celo'; fecha: string; celo: Celo }
  | { tipo: 'inseminacion'; fecha: string; ins: Inseminacion }

const DIA = 24 * 60 * 60 * 1000
const CICLO_DIAS = 21          // ciclo estral bovino promedio
const CICLO_MARGEN = 2         // ±2 días de margen sobre el ciclo
const ANESTRO_DIAS = 40        // sin celo hace 40+ días = alerta
const CHEQUEO_PRENEZ_DIAS = 32 // chequeo de preñez ~30-35 días post servicio

function fecha(d: Date | string) {
  return new Date(d).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: '2-digit' })
}
function hhmm(d: Date | string) {
  return new Date(d).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
}
function diasDesde(d: Date | string, ahora: Date) {
  return Math.floor((ahora.getTime() - new Date(d).getTime()) / DIA)
}
function diasHasta(d: Date | string, ahora: Date) {
  return Math.ceil((new Date(d).getTime() - ahora.getTime()) / DIA)
}

export default function VacasPage() {
  const [vacas, setVacas] = useState<Vaca[]>([])
  const [montas, setMontas] = useState<Monta[]>([])
  const [inseminaciones, setInseminaciones] = useState<Inseminacion[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [search, setSearch] = useState('')
  const [ahora] = useState(new Date())

  const cargar = useCallback(async () => {
    setLoading(true)
    const [{ data: dv }, { data: dm }, { data: di }] = await Promise.all([
      supabase.from('vacas').select('id, id_negocio, ruta_fotos, fecha_alta').order('id_negocio', { ascending: true }),
      supabase
        .from('montas')
        .select(`*, frames_formateados ( ruta_archivo ), vacas ( id_negocio, ruta_fotos )`)
        .order('fecha_monta', { ascending: false })
        .limit(1000),
      supabase.from('inseminaciones').select('id, id_negocio, celo_inicio, fecha_inseminacion'),
    ])
    if (dv) setVacas(dv)
    if (dm) setMontas(dm)
    if (di) setInseminaciones(di)
    setLoading(false)
  }, [])

  useEffect(() => { cargar() }, [cargar])

  // --- armado de historia por vaca ---
  const celos = agruparEnCelos(montas)

  function celosDe(caravana: string): Celo[] {
    return celos
      .filter(c => c.idNegocio === caravana)
      .sort((a, b) => new Date(b.inicio).getTime() - new Date(a.inicio).getTime())
  }
  function inseminacionesDe(caravana: string): Inseminacion[] {
    return inseminaciones
      .filter(i => i.id_negocio != null && String(i.id_negocio) === caravana)
      .sort((a, b) => new Date(b.fecha_inseminacion).getTime() - new Date(a.fecha_inseminacion).getTime())
  }

  type Resumen = {
    ultimoCelo: Celo | null
    ultimaIns: Inseminacion | null
    proximoCeloDesde: Date | null
    proximoCeloHasta: Date | null
    chequeoPrenez: Date | null
    alertaAnestro: boolean
    repetidora: boolean
    totalCelos: number
    totalIns: number
  }

  function resumenDe(caravana: string): Resumen {
    const cs = celosDe(caravana)
    const is = inseminacionesDe(caravana)
    const ultimoCelo = cs[0] ?? null
    const ultimaIns = is[0] ?? null

    let proximoCeloDesde: Date | null = null
    let proximoCeloHasta: Date | null = null
    if (ultimoCelo) {
      const base = new Date(ultimoCelo.inicio).getTime()
      proximoCeloDesde = new Date(base + (CICLO_DIAS - CICLO_MARGEN) * DIA)
      proximoCeloHasta = new Date(base + (CICLO_DIAS + CICLO_MARGEN) * DIA)
    }

    const chequeoPrenez = ultimaIns
      ? new Date(new Date(ultimaIns.fecha_inseminacion).getTime() + CHEQUEO_PRENEZ_DIAS * DIA)
      : null

    const alertaAnestro = ultimoCelo != null && diasDesde(ultimoCelo.inicio, ahora) >= ANESTRO_DIAS
    // Repetidora: 3+ inseminaciones sin que dejen de aparecer celos posteriores
    const repetidora = is.length >= 3 && ultimoCelo != null &&
      new Date(ultimoCelo.inicio).getTime() > new Date(is[0].fecha_inseminacion).getTime()

    return {
      ultimoCelo, ultimaIns, proximoCeloDesde, proximoCeloHasta,
      chequeoPrenez, alertaAnestro, repetidora,
      totalCelos: cs.length, totalIns: is.length,
    }
  }

  const filtradas = vacas.filter(v => !search || String(v.id_negocio).includes(search.trim()))
  const selected = vacas.find(v => v.id === selectedId) ?? null
  const resumen = selected ? resumenDe(String(selected.id_negocio)) : null

  const historia: EventoHistoria[] = selected
    ? [
        ...celosDe(String(selected.id_negocio)).map(c => ({ tipo: 'celo' as const, fecha: c.inicio, celo: c })),
        ...inseminacionesDe(String(selected.id_negocio)).map(i => ({ tipo: 'inseminacion' as const, fecha: i.fecha_inseminacion, ins: i })),
      ].sort((a, b) => new Date(b.fecha).getTime() - new Date(a.fecha).getTime())
    : []

  const conActividad = vacas.filter(v => celosDe(String(v.id_negocio)).length > 0).length

  return (
    <div className="flex min-h-screen w-full">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">

        <div className="h-12 bg-white border-b border-gray-200 flex items-center px-5 gap-3 flex-shrink-0 sticky top-0 z-10">
          <span className="text-sm font-medium text-gray-900 flex-1">Vacas</span>
          <input
            type="text"
            placeholder="Buscar caravana..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="h-8 text-xs px-3 border border-gray-200 rounded-lg w-40 focus:outline-none"
          />
        </div>

        <div className="flex-1 p-5 flex flex-col gap-4">

          <div className="grid grid-cols-3 gap-3 flex-shrink-0">
            {[
              { label: 'Vacas registradas', value: vacas.length, color: 'text-gray-900' },
              { label: 'Con actividad detectada', value: conActividad, color: 'text-green-700' },
              { label: 'Sin actividad registrada', value: vacas.length - conActividad, color: 'text-gray-500' },
            ].map(m => (
              <div key={m.label} className="bg-white border border-[#E7E5E4] rounded-xl p-3">
                <div className="text-xs text-gray-500 mb-1">{m.label}</div>
                <div className={`text-2xl font-medium ${m.color}`}>{m.value}</div>
              </div>
            ))}
          </div>

          <div className="flex gap-4 flex-1 items-start">

            {/* Lista de vacas */}
            <div className="w-64 flex-shrink-0 flex flex-col gap-2 overflow-y-auto pr-1 sticky top-16" style={{ maxHeight: 'calc(100vh - 5rem)' }}>
              {loading && <div className="text-sm text-gray-400 text-center py-8">Cargando...</div>}
              {!loading && filtradas.length === 0 && (
                <div className="text-sm text-gray-400 text-center py-8">Sin resultados</div>
              )}
              {filtradas.map(v => {
                const r = resumenDe(String(v.id_negocio))
                return (
                  <div
                    key={v.id}
                    onClick={() => setSelectedId(v.id)}
                    className={`bg-white border rounded-xl p-3 cursor-pointer transition-colors ${
                      selectedId === v.id ? 'border-blue-400' : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <CaravanaTag numero={v.id_negocio} size="sm" />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs text-gray-500">
                          {r.ultimoCelo
                            ? `Último celo ${fecha(r.ultimoCelo.inicio)}`
                            : 'Sin celos registrados'}
                        </div>
                        <div className="flex gap-1 mt-1 flex-wrap">
                          {r.totalCelos > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600">
                              {r.totalCelos} {r.totalCelos === 1 ? 'celo' : 'celos'}
                            </span>
                          )}
                          {r.totalIns > 0 && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-50 text-green-700">
                              {r.totalIns} ins.
                            </span>
                          )}
                          {r.alertaAnestro && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-orange-50 text-orange-700">
                              vigilar
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Ficha */}
            <div className="flex-1 flex flex-col gap-3 min-w-0">
              {!selected || !resumen ? (
                <div className="flex-1 flex items-center justify-center text-gray-400 text-sm py-24">
                  ← Seleccioná una vaca para ver su ficha reproductiva
                </div>
              ) : (
                <>
                  {/* Cabecera */}
                  <div className="bg-white border border-gray-200 rounded-xl p-4">
                    <div className="flex items-start gap-5">
                      <div className="flex flex-col items-center flex-shrink-0">
                        <CaravanaTag numero={selected.id_negocio} size="lg" />
                        {selected.fecha_alta && (
                          <div className="text-[10px] text-gray-400 mt-1.5">alta {fecha(selected.fecha_alta)}</div>
                        )}
                      </div>
                      <div className="flex-1 min-w-0 grid grid-cols-3 gap-x-4 gap-y-3">
                        <Dato
                          label="Último celo"
                          valor={resumen.ultimoCelo ? `${fecha(resumen.ultimoCelo.inicio)} ${hhmm(resumen.ultimoCelo.inicio)}` : '—'}
                          sub={resumen.ultimoCelo ? `hace ${diasDesde(resumen.ultimoCelo.inicio, ahora)} días` : undefined}
                        />
                        <Dato
                          label="Última inseminación"
                          valor={resumen.ultimaIns ? fecha(resumen.ultimaIns.fecha_inseminacion) : '—'}
                          sub={resumen.ultimaIns ? `hace ${diasDesde(resumen.ultimaIns.fecha_inseminacion, ahora)} días` : undefined}
                        />
                        <Dato
                          label="Celos totales"
                          valor={`${resumen.totalCelos}`}
                          sub={`${resumen.totalIns} inseminaciones`}
                        />
                        {resumen.proximoCeloDesde && resumen.proximoCeloHasta && (
                          <div className="col-span-3 bg-blue-50 rounded-lg px-3 py-2.5 flex items-center gap-3">
                            <span className="text-lg">🔭</span>
                            <div>
                              <div className="text-xs text-blue-500">Próximo celo esperado (ciclo de ~21 días)</div>
                              <div className="text-sm font-medium text-blue-900">
                                Entre el {fecha(resumen.proximoCeloDesde)} y el {fecha(resumen.proximoCeloHasta)}
                                {diasHasta(resumen.proximoCeloDesde, ahora) > 0
                                  ? ` · en ${diasHasta(resumen.proximoCeloDesde, ahora)} días`
                                  : diasHasta(resumen.proximoCeloHasta, ahora) >= 0
                                    ? ' · ¡vigilar estos días!'
                                    : ' · la ventana ya pasó sin detección'}
                              </div>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Alertas */}
                    {(resumen.alertaAnestro || resumen.repetidora || resumen.chequeoPrenez) && (
                      <div className="mt-3 pt-3 border-t border-gray-100 flex flex-col gap-1.5">
                        {resumen.chequeoPrenez && (
                          <div className="text-xs bg-green-50 text-green-800 rounded-lg px-3 py-2">
                            🩺 Chequeo de preñez sugerido alrededor del {fecha(resumen.chequeoPrenez)} (~{CHEQUEO_PRENEZ_DIAS} días post servicio)
                          </div>
                        )}
                        {resumen.alertaAnestro && (
                          <div className="text-xs bg-orange-50 text-orange-800 rounded-lg px-3 py-2">
                            ⚠ Sin celo detectado hace {resumen.ultimoCelo ? diasDesde(resumen.ultimoCelo.inicio, ahora) : '—'} días. Puede estar preñada, en anestro, o el celo pasó sin detectarse — conviene revisarla.
                          </div>
                        )}
                        {resumen.repetidora && (
                          <div className="text-xs bg-red-50 text-red-800 rounded-lg px-3 py-2">
                            ⚠ Repetidora: volvió a mostrar celo después de {resumen.totalIns} servicios. Consultar con el veterinario.
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Historia */}
                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-gray-100">
                      <span className="text-sm font-medium text-gray-900">Historia reproductiva</span>
                    </div>
                    <div className="p-4">
                      {historia.length === 0 ? (
                        <div className="text-sm text-gray-400 py-4 text-center">
                          Sin eventos registrados para esta vaca todavía.
                        </div>
                      ) : (
                        <div className="flex flex-col">
                          {historia.map((ev, i) => (
                            <div key={i} className="flex gap-3">
                              <div className="flex flex-col items-center">
                                <div className={`w-2.5 h-2.5 rounded-full mt-1.5 flex-shrink-0 ${
                                  ev.tipo === 'celo' ? 'bg-red-400' : 'bg-green-500'
                                }`} />
                                {i < historia.length - 1 && <div className="w-px flex-1 bg-gray-200 my-1" />}
                              </div>
                              <div className="pb-4 flex-1 min-w-0">
                                {ev.tipo === 'celo' ? (
                                  <>
                                    <div className="text-sm font-medium text-gray-900">
                                      Celo detectado
                                      <span className="text-xs text-gray-400 font-normal ml-2">
                                        {fecha(ev.fecha)} {hhmm(ev.fecha)}
                                      </span>
                                    </div>
                                    <div className="text-xs text-gray-500 mt-0.5">
                                      {ev.celo.cantidadMontas} {ev.celo.cantidadMontas === 1 ? 'monta' : 'montas'}
                                      {ev.celo.confianzaId != null && ` · ID ${Math.round(ev.celo.confianzaId * 100)}%`}
                                      {' · '}
                                      <Link href="/celos" className="text-blue-600 hover:underline">ver evidencia</Link>
                                    </div>
                                  </>
                                ) : (
                                  <>
                                    <div className="text-sm font-medium text-gray-900">
                                      Inseminación registrada
                                      <span className="text-xs text-gray-400 font-normal ml-2">
                                        {fecha(ev.fecha)} {hhmm(ev.fecha)}
                                      </span>
                                    </div>
                                    <div className="text-xs text-gray-500 mt-0.5">
                                      Sobre el celo del {fecha(ev.ins.celo_inicio)}
                                    </div>
                                  </>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function Dato({ label, valor, sub }: { label: string; valor: string; sub?: string }) {
  return (
    <div>
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-sm font-medium text-gray-900">{valor}</div>
      {sub && <div className="text-[11px] text-gray-400">{sub}</div>}
    </div>
  )
}