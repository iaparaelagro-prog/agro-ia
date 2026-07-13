'use client'
import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import { supabase, type Monta } from '@/lib/supabase'
import Sidebar from '@/components/Sidebar'
import { CaravanaTag } from '@/components/ui'
import {
  agruparEnCelos,
  calcularVentana,
  estadoCelo,
  celoKey,
  type Celo,
  type Ventana,
} from '@/lib/celos'

function hhmm(d: Date | string) {
  return new Date(d).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
}
function diaMes(d: Date | string) {
  return new Date(d).toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' })
}
function relativo(target: Date, ahora: Date) {
  let diff = Math.abs(target.getTime() - ahora.getTime())
  const h = Math.floor(diff / 3600000)
  const min = Math.floor((diff % 3600000) / 60000)
  return h > 0 ? `${h}h ${min}m` : `${min}m`
}

export default function HoyPage() {
  const [montas, setMontas] = useState<Monta[]>([])
  const [inseminadas, setInseminadas] = useState<Set<string>>(new Set())
  const [inseminadasHoy, setInseminadasHoy] = useState(0)
  const [loading, setLoading] = useState(true)
  const [ahora, setAhora] = useState(new Date())

  const cargar = useCallback(async () => {
    setLoading(true)
    const { data: dm } = await supabase
      .from('montas')
      .select(`*, frames_formateados ( ruta_archivo ), vacas ( id_negocio, ruta_fotos )`)
      .order('fecha_monta', { ascending: false })
      .limit(300)

    const { data: di } = await supabase
      .from('inseminaciones')
      .select('id_negocio, celo_inicio, fecha_inseminacion')

    if (dm) setMontas(dm)

    const set = new Set<string>()
    let hoyCount = 0
    const hoyStr = new Date().toDateString()
    ;(di ?? []).forEach((r: { id_negocio: string | null; celo_inicio: string; fecha_inseminacion: string }) => {
      set.add(celoKey(r.id_negocio != null ? String(r.id_negocio) : null, r.celo_inicio))
      if (r.fecha_inseminacion && new Date(r.fecha_inseminacion).toDateString() === hoyStr) {
        hoyCount++
      }
    })
    setInseminadas(set)
    setInseminadasHoy(hoyCount)
    setLoading(false)
  }, [])

  useEffect(() => { cargar() }, [cargar])

  // Refrescar "ahora" cada minuto para que las ventanas se actualicen solas.
  useEffect(() => {
    const t = setInterval(() => setAhora(new Date()), 60 * 1000)
    return () => clearInterval(t)
  }, [])

  // Realtime: si entra una monta nueva, recargamos.
  useEffect(() => {
    const ch = supabase
      .channel('hoy-montas')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'montas' }, () => cargar())
      .subscribe()
    return () => { supabase.removeChannel(ch) }
  }, [cargar])

  async function marcarInseminada(celo: Celo) {
    const { error } = await supabase
      .from('inseminaciones')
      .upsert(
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
    setInseminadasHoy(n => n + 1)
  }

  // --- clasificación de celos ---
  const celos = agruparEnCelos(montas)
  const DOS_DIAS = 48 * 60 * 60 * 1000
  const recientes = celos.filter(c => ahora.getTime() - new Date(c.inicio).getTime() <= DOS_DIAS)

  const conVentana = recientes
    .filter(c => c.identificada)
    .map(c => {
      const ventana = calcularVentana(c)
      return { celo: c, ventana, estado: estadoCelo(c, ventana, inseminadas, ahora) }
    })

  const abiertas = conVentana
    .filter(x => x.estado === 'abierta')
    .sort((a, b) => a.ventana.fin.getTime() - b.ventana.fin.getTime())
  const proximas = conVentana
    .filter(x => x.estado === 'proxima')
    .sort((a, b) => a.ventana.inicio.getTime() - b.ventana.inicio.getTime())
  const perdidas = conVentana
    .filter(x => x.estado === 'perdida')
    .sort((a, b) => b.ventana.fin.getTime() - a.ventana.fin.getTime())

  const paraRevisar = celos
    .filter(c => !c.identificada)
    .sort((a, b) => new Date(b.inicio).getTime() - new Date(a.inicio).getTime())

  const fechaHoy = ahora.toLocaleDateString('es-AR', {
    weekday: 'long', day: 'numeric', month: 'long',
  })

  return (
    <div className="flex min-h-screen w-full">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">

        <div className="h-12 bg-white border-b border-gray-200 flex items-center px-5 gap-3 flex-shrink-0 sticky top-0 z-10">
          <span className="text-sm font-medium text-gray-900">Hoy</span>
          <span className="text-xs text-gray-400 capitalize">{fechaHoy}</span>
        </div>

        <div className="flex-1 p-5 flex flex-col gap-5">

          {/* Métricas */}
          <div className="grid grid-cols-5 gap-3">
            {[
              { label: 'Inseminar ahora', value: abiertas.length, color: 'text-red-600' },
              { label: 'Próximas ventanas', value: proximas.length, color: 'text-yellow-600' },
              { label: 'Para revisar', value: paraRevisar.length, color: 'text-blue-600' },
              { label: 'Ventanas perdidas (48h)', value: perdidas.length, color: 'text-gray-500' },
              { label: 'Inseminadas hoy', value: inseminadasHoy, color: 'text-green-600' },
            ].map(m => (
              <div key={m.label} className="bg-white border border-[#E7E5E4] rounded-xl p-3">
                <div className="text-xs text-gray-500 mb-1">{m.label}</div>
                <div className={`text-2xl font-medium ${m.color}`}>{m.value}</div>
              </div>
            ))}
          </div>

          {/* INSEMINAR AHORA */}
          <section>
            <h2 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500" /> Inseminar ahora
            </h2>
            {loading ? (
              <div className="text-sm text-gray-400 py-6 text-center">Cargando...</div>
            ) : abiertas.length === 0 ? (
              <div className="bg-white border border-dashed border-gray-200 rounded-xl p-6 text-center text-sm text-gray-400">
                No hay vacas con ventana de inseminación abierta en este momento.
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {abiertas.map(({ celo, ventana }) => (
                  <div key={celo.key} className="bg-white border border-[#F0B4B4] border-l-4 border-l-[#E24B4A] rounded-r-xl p-4 flex items-center gap-5">
                    <div className="flex flex-col items-center flex-shrink-0">
                      <CaravanaTag numero={celo.idNegocio} size="lg" />
                      <div className="text-[10px] text-gray-400 mt-1">caravana</div>
                    </div>
                    <div className="flex-1 min-w-0 grid grid-cols-3 gap-x-4 gap-y-2">
                      <Dato label="Celo detectado" valor={`${diaMes(celo.inicio)} ${hhmm(celo.inicio)}`} />
                      <Dato label="Inseminar entre" valor={`${hhmm(ventana.inicio)} – ${hhmm(ventana.fin)}`} />
                      <Dato label="Punto óptimo" valor={hhmm(ventana.optima)} destacado />
                      <Dato label="Montas" valor={String(celo.cantidadMontas)} />
                      <Dato label="Confianza ID" valor={celo.confianzaId != null ? `${Math.round(celo.confianzaId * 100)}%` : '—'} />
                      <Dato label="Cierra en" valor={relativo(ventana.fin, ahora)} />
                    </div>
                    <button
                      onClick={() => marcarInseminada(celo)}
                      className="flex-shrink-0 bg-green-600 hover:bg-green-700 text-white text-sm font-medium px-5 py-3 rounded-xl"
                    >
                      ✓ Marcar inseminada
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* PRÓXIMAS + PARA REVISAR */}
          <div className="grid grid-cols-2 gap-5">
            <section>
              <h2 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-yellow-400" /> Próximas ventanas
              </h2>
              {proximas.length === 0 ? (
                <div className="bg-white border border-dashed border-gray-200 rounded-xl p-5 text-center text-sm text-gray-400">
                  Sin ventanas próximas.
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {proximas.map(({ celo, ventana }) => (
                    <div key={celo.key} className="bg-white border border-gray-200 rounded-xl p-3 flex items-center gap-3">
                      <CaravanaTag numero={celo.idNegocio} size="sm" />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-900">Abre en {relativo(ventana.inicio, ahora)}</div>
                        <div className="text-xs text-gray-500">
                          Celo {hhmm(celo.inicio)} · inseminar {hhmm(ventana.inicio)}–{hhmm(ventana.fin)}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section>
              <h2 className="text-sm font-semibold text-gray-900 mb-2 flex items-center gap-2">
                <span className="w-2.5 h-2.5 rounded-full bg-blue-400" /> Para revisar
              </h2>
              {paraRevisar.length === 0 ? (
                <div className="bg-white border border-dashed border-gray-200 rounded-xl p-5 text-center text-sm text-gray-400">
                  No hay celos sin identificar.
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  {paraRevisar.slice(0, 12).map(celo => (
                    <div key={celo.key} className="bg-white border border-gray-200 rounded-xl p-3 flex items-center gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-900">Sin identificar</div>
                        <div className="text-xs text-gray-500">
                          {diaMes(celo.inicio)} {hhmm(celo.inicio)} · {celo.cantidadMontas} {celo.cantidadMontas === 1 ? 'monta' : 'montas'}
                        </div>
                      </div>
                      <Link
                        href="/montas"
                        className="flex-shrink-0 text-xs font-medium px-3 py-1.5 rounded-lg border border-blue-200 text-blue-700 hover:bg-blue-50"
                      >
                        Revisar
                      </Link>
                    </div>
                  ))}
                  {paraRevisar.length > 12 && (
                    <div className="text-xs text-gray-400 text-center pt-1">
                      + {paraRevisar.length - 12} más sin identificar
                    </div>
                  )}
                </div>
              )}
            </section>
          </div>

          {/* PERDIDAS (costo) */}
          {perdidas.length > 0 && (
            <section>
              <h2 className="text-sm font-semibold text-gray-500 mb-2">
                Ventanas perdidas (últimas 48 h)
              </h2>
              <div className="flex flex-col gap-2">
                {perdidas.map(({ celo, ventana }) => (
                  <div key={celo.key} className="bg-gray-50 border border-gray-200 rounded-xl p-3 flex items-center gap-3 opacity-80">
                    <CaravanaTag numero={celo.idNegocio} size="sm" apagada />
                    <div className="flex-1 min-w-0 text-xs text-gray-500">
                      Celo {diaMes(celo.inicio)} {hhmm(celo.inicio)} · ventana cerró {hhmm(ventana.fin)} sin inseminar
                    </div>
                    <button
                      onClick={() => marcarInseminada(celo)}
                      className="flex-shrink-0 text-xs px-3 py-1.5 rounded-lg border border-gray-300 text-gray-600 hover:bg-white"
                    >
                      Registrar igual
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

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