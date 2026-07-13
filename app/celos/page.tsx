'use client'
import { useEffect, useState, useCallback } from 'react'
import { supabase, type Monta } from '@/lib/supabase'
import Sidebar from '@/components/Sidebar'
import { CaravanaTag } from '@/components/ui'
import FrameViewer from '@/components/FrameViewer'
import {
  agruparEnCelos,
  calcularVentana,
  estadoCelo,
  celoKey,
  type Celo,
  type EstadoCelo,
} from '@/lib/celos'

function hhmm(d: Date | string) {
  return new Date(d).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
}
function diaMes(d: Date | string) {
  return new Date(d).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' })
}
function duracion(inicio: string, fin: string) {
  const ms = new Date(fin).getTime() - new Date(inicio).getTime()
  const h = Math.floor(ms / 3600000)
  const min = Math.floor((ms % 3600000) / 60000)
  if (h === 0 && min === 0) return 'puntual'
  return h > 0 ? `${h}h ${min}m` : `${min}m`
}

const ESTADO_INFO: Record<EstadoCelo, { label: string; pill: string; dot: string }> = {
  abierta:    { label: 'Inseminar ahora', pill: 'bg-red-100 text-red-800',       dot: 'bg-red-500' },
  proxima:    { label: 'Ventana próxima', pill: 'bg-yellow-100 text-yellow-800', dot: 'bg-yellow-400' },
  inseminada: { label: 'Inseminada',      pill: 'bg-green-100 text-green-800',   dot: 'bg-green-500' },
  perdida:    { label: 'Ventana perdida', pill: 'bg-gray-100 text-gray-600',     dot: 'bg-gray-400' },
}

type Vaca = {
  id: number
  id_negocio: string | number
  ruta_fotos: string | null
}

export default function CelosPage() {
  const [montas, setMontas] = useState<Monta[]>([])
  const [vacas, setVacas] = useState<Vaca[]>([])
  const [busquedaCaravana, setBusquedaCaravana] = useState('')
  const [asignando, setAsignando] = useState(false)
  const [inseminadas, setInseminadas] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [montaIdx, setMontaIdx] = useState(0)
  const [showBbox, setShowBbox] = useState(true)
  const [filtro, setFiltro] = useState<'todos' | 'identificados' | 'sin_identificar'>('todos')
  const [ahora, setAhora] = useState(new Date())

  const cargar = useCallback(async () => {
    setLoading(true)
    const { data: dm } = await supabase
      .from('montas')
      .select(`*, frames_formateados ( ruta_archivo ), vacas ( id_negocio, ruta_fotos )`)
      .order('fecha_monta', { ascending: false })
      .limit(500)
    const { data: di } = await supabase
      .from('inseminaciones')
      .select('id_negocio, celo_inicio')
    const { data: dv } = await supabase
      .from('vacas')
      .select('id, id_negocio, ruta_fotos')
      .order('id_negocio', { ascending: true })
    if (dm) setMontas(dm)
    if (dv) setVacas(dv)
    const set = new Set<string>()
    ;(di ?? []).forEach((r: { id_negocio: string | null; celo_inicio: string }) => {
      set.add(celoKey(r.id_negocio != null ? String(r.id_negocio) : null, r.celo_inicio))
    })
    setInseminadas(set)
    setLoading(false)
  }, [])

  useEffect(() => { cargar() }, [cargar])

  useEffect(() => {
    const t = setInterval(() => setAhora(new Date()), 60 * 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    const ch = supabase
      .channel('celos-montas')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'montas' }, () => cargar())
      .subscribe()
    return () => { supabase.removeChannel(ch) }
  }, [cargar])

  async function marcarInseminada(celo: Celo) {
    const { error } = await supabase.from('inseminaciones').upsert(
      {
        id_negocio: celo.idNegocio,
        celo_inicio: new Date(celo.inicio).toISOString(),
        fecha_inseminacion: new Date().toISOString(),
      },
      { onConflict: 'id_negocio,celo_inicio' }
    )
    if (error) {
      alert('No se pudo guardar la inseminación. Revisá la conexión e intentá de nuevo.')
      return
    }
    setInseminadas(prev => new Set(prev).add(celo.key))
  }

  async function asignarVaca(celo: Celo, vaca: Vaca) {
    if (asignando) return
    setAsignando(true)
    const ids = celo.montas.map(m => m.id)
    const { error } = await supabase
      .from('montas')
      .update({ vaca_id: vaca.id, estado_id: 'identificada' })
      .in('id', ids)
    setAsignando(false)
    if (error) {
      alert('No se pudo asignar la vaca. Revisá la conexión e intentá de nuevo.')
      return
    }
    setBusquedaCaravana('')
    // El celo identificado cambia de key; lo reseleccionamos por su nueva identidad
    setSelectedKey(celoKey(String(vaca.id_negocio), celo.inicio))
    await cargar()
  }

  const celos = agruparEnCelos(montas).sort(
    (a, b) => new Date(b.inicio).getTime() - new Date(a.inicio).getTime()
  )

  const filtrados = celos.filter(c => {
    if (filtro === 'identificados') return c.identificada
    if (filtro === 'sin_identificar') return !c.identificada
    return true
  })

  const selected = celos.find(c => c.key === selectedKey) ?? null
  const montasOrdenadas = selected
    ? [...selected.montas].sort((a, b) => new Date(a.fecha_monta).getTime() - new Date(b.fecha_monta).getTime())
    : []
  const montaActual = montasOrdenadas[Math.min(montaIdx, montasOrdenadas.length - 1)] ?? null

  function seleccionar(c: Celo) {
    setSelectedKey(c.key)
    setMontaIdx(0)
  }

  function infoDe(c: Celo) {
    if (!c.identificada) return null
    const ventana = calcularVentana(c)
    const estado = estadoCelo(c, ventana, inseminadas, ahora)
    return { ventana, estado }
  }

  const totalIdent = celos.filter(c => c.identificada).length
  const totalSinIdent = celos.length - totalIdent

  return (
    <div className="flex min-h-screen w-full">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">

        <div className="h-12 bg-white border-b border-gray-200 flex items-center px-5 gap-3 flex-shrink-0 sticky top-0 z-10">
          <span className="text-sm font-medium text-gray-900 flex-1">Eventos de celo</span>
          <select
            value={filtro}
            onChange={e => setFiltro(e.target.value as typeof filtro)}
            className="h-8 text-xs px-2 border border-gray-200 rounded-lg focus:outline-none"
          >
            <option value="todos">Todos los celos</option>
            <option value="identificados">Identificados</option>
            <option value="sin_identificar">Sin identificar</option>
          </select>
        </div>

        <div className="flex-1 p-5 flex flex-col gap-4">

          <div className="grid grid-cols-3 gap-3 flex-shrink-0">
            {[
              { label: 'Celos detectados', value: celos.length, color: 'text-gray-900' },
              { label: 'Con vaca identificada', value: totalIdent, color: 'text-green-700' },
              { label: 'Sin identificar', value: totalSinIdent, color: 'text-blue-700' },
            ].map(m => (
              <div key={m.label} className="bg-white border border-[#E7E5E4] rounded-xl p-3">
                <div className="text-xs text-gray-500 mb-1">{m.label}</div>
                <div className={`text-2xl font-medium ${m.color}`}>{m.value}</div>
              </div>
            ))}
          </div>

          <div className="flex gap-4 flex-1 items-start">

            {/* Lista de celos */}
            <div className="w-80 flex-shrink-0 flex flex-col gap-2 overflow-y-auto pr-1 sticky top-16" style={{ maxHeight: 'calc(100vh - 5rem)' }}>
              {loading && <div className="text-sm text-gray-400 text-center py-8">Cargando...</div>}
              {!loading && filtrados.length === 0 && (
                <div className="text-sm text-gray-400 text-center py-8">Sin resultados</div>
              )}
              {filtrados.map(c => {
                const info = infoDe(c)
                return (
                  <div
                    key={c.key}
                    onClick={() => seleccionar(c)}
                    className={`bg-white border rounded-xl p-3 cursor-pointer transition-colors ${
                      selectedKey === c.key ? 'border-blue-400' : 'border-gray-200 hover:border-gray-300'
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-2">
                      {c.identificada ? (
                        <CaravanaTag numero={c.idNegocio} size="sm" />
                      ) : (
                        <div className="w-12 h-9 rounded bg-blue-50 flex items-center justify-center text-base flex-shrink-0">?</div>
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-medium text-gray-900">
                          {c.identificada ? `Vaca ${c.idNegocio}` : 'Sin identificar'}
                        </div>
                        <div className="text-xs text-gray-400">
                          {diaMes(c.inicio)} {hhmm(c.inicio)} · {duracion(c.inicio, c.fin)}
                        </div>
                      </div>
                      {info && (
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${ESTADO_INFO[info.estado].pill}`}>
                          {ESTADO_INFO[info.estado].label}
                        </span>
                      )}
                    </div>
                    <div className="flex gap-1 flex-wrap">
                      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                        {c.cantidadMontas} {c.cantidadMontas === 1 ? 'monta' : 'montas'}
                      </span>
                      {c.confianzaId != null && (
                        <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">
                          ID {Math.round(c.confianzaId * 100)}%
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Detalle */}
            <div className="flex-1 flex flex-col gap-3 min-w-0">
              {!selected ? (
                <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
                  ← Seleccioná un celo para ver el detalle
                </div>
              ) : (
                <>
                  {/* Cabecera del celo */}
                  <div className="bg-white border border-gray-200 rounded-xl p-4">
                    <div className="flex items-start gap-5">
                      <div className="flex flex-col items-center flex-shrink-0">
                        {selected.identificada ? (
                          <CaravanaTag numero={selected.idNegocio} size="lg" />
                        ) : (
                          <div className="w-24 h-[72px] rounded-xl bg-[#E6F1FB] flex items-center justify-center text-4xl font-bold text-[#0C447C]">?</div>
                        )}
                        <div className="text-[10px] text-gray-400 mt-1">caravana</div>
                      </div>
                      <div className="flex-1 min-w-0 grid grid-cols-3 gap-x-4 gap-y-2">
                        <Dato label="Inicio del celo" valor={`${diaMes(selected.inicio)} ${hhmm(selected.inicio)}`} />
                        <Dato label="Última monta" valor={hhmm(selected.fin)} />
                        <Dato label="Duración" valor={duracion(selected.inicio, selected.fin)} />
                        <Dato label="Montas detectadas" valor={String(selected.cantidadMontas)} />
                        <Dato
                          label="Confianza ID"
                          valor={selected.confianzaId != null ? `${Math.round(selected.confianzaId * 100)}%` : '—'}
                        />
                        {(() => {
                          const info = infoDe(selected)
                          return info ? (
                            <Dato
                              label="Inseminar entre"
                              valor={`${hhmm(info.ventana.inicio)} – ${hhmm(info.ventana.fin)}`}
                              destacado={info.estado === 'abierta'}
                            />
                          ) : (
                            <Dato label="Ventana" valor="requiere identificar" />
                          )
                        })()}
                      </div>
                      {(() => {
                        const info = infoDe(selected)
                        if (!info) return null
                        if (info.estado === 'inseminada') {
                          return (
                            <span className="flex-shrink-0 bg-green-100 text-green-800 text-sm font-medium px-4 py-2.5 rounded-xl">
                              ✓ Inseminada
                            </span>
                          )
                        }
                        return (
                          <button
                            onClick={() => marcarInseminada(selected)}
                            className="flex-shrink-0 bg-green-600 hover:bg-green-700 text-white text-sm font-medium px-5 py-3 rounded-xl"
                          >
                            ✓ Marcar inseminada
                          </button>
                        )
                      })()}
                    </div>

                    {!selected.identificada && (
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <div className="text-xs text-blue-700 bg-blue-50 rounded-lg px-3 py-2 mb-2">
                          Este celo no tiene vaca identificada. Mirá la evidencia de abajo, reconocé el animal y asignale la caravana:
                        </div>
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            placeholder="Buscar caravana (ej: 5248)..."
                            value={busquedaCaravana}
                            onChange={e => setBusquedaCaravana(e.target.value)}
                            className="h-9 text-sm px-3 border border-gray-200 rounded-lg w-56 focus:outline-none focus:border-blue-300"
                          />
                          {asignando && <span className="text-xs text-gray-400">Asignando...</span>}
                        </div>
                        {busquedaCaravana.trim() !== '' && (
                          <div className="mt-2 flex gap-1.5 flex-wrap">
                            {vacas
                              .filter(v => String(v.id_negocio).includes(busquedaCaravana.trim()))
                              .slice(0, 12)
                              .map(v => (
                                <button
                                  key={v.id}
                                  disabled={asignando}
                                  onClick={() => asignarVaca(selected, v)}
                                  className="text-sm px-3 py-1.5 rounded-lg border border-green-200 text-green-800 bg-green-50 hover:bg-green-100 font-medium disabled:opacity-50"
                                >
                                  ✓ Vaca {v.id_negocio}
                                </button>
                              ))}
                            {vacas.filter(v => String(v.id_negocio).includes(busquedaCaravana.trim())).length === 0 && (
                              <span className="text-xs text-gray-400 py-1">
                                No hay vacas con esa caravana en el registro.
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Evidencia */}
                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-gray-100 flex items-center justify-between gap-3">
                      <span className="text-sm font-medium text-gray-900">
                        Evidencia · monta {montaIdx + 1} de {montasOrdenadas.length}
                      </span>
                      <button
                        onClick={() => setShowBbox(!showBbox)}
                        className={`text-xs px-3 py-1 rounded-full border transition-colors ${
                          showBbox ? 'bg-blue-50 text-blue-700 border-blue-200' : 'bg-gray-50 text-gray-500 border-gray-200'
                        }`}
                      >
                        Bounding box
                      </button>
                    </div>

                    <div className="p-3">
                      {montaActual && (
                        <FrameViewer
                          src={montaActual.frames_formateados?.ruta_archivo || ''}
                          bbox={montaActual.bbox_monta}
                          label={montaActual.confianza != null ? `Monta ${montaActual.confianza.toFixed(2)}` : 'Monta'}
                          show={showBbox}
                          alto={420}
                        />
                      )}

                      {/* Timeline de montas del celo */}
                      {montasOrdenadas.length > 1 && (
                        <div className="mt-3">
                          <div className="text-xs text-gray-400 mb-1.5">
                            Montas de este celo — clic para ver cada una
                          </div>
                          <div className="flex gap-1.5 flex-wrap">
                            {montasOrdenadas.map((m, i) => (
                              <button
                                key={m.id}
                                onClick={() => setMontaIdx(i)}
                                className={`text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
                                  i === montaIdx
                                    ? 'bg-blue-50 text-blue-700 border-blue-300 font-medium'
                                    : 'bg-white text-gray-600 border-gray-200 hover:border-gray-300'
                                }`}
                              >
                                {hhmm(m.fecha_monta)}{m.confianza != null ? ` · ${Math.round(m.confianza * 100)}%` : ''}
                              </button>
                            ))}
                          </div>
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

function Dato({ label, valor, destacado }: { label: string; valor: string; destacado?: boolean }) {
  return (
    <div>
      <div className="text-xs text-gray-400">{label}</div>
      <div className={`text-sm font-medium ${destacado ? 'text-red-600' : 'text-gray-900'}`}>{valor}</div>
    </div>
  )
}