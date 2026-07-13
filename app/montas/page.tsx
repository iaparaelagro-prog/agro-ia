'use client'
import { useEffect, useState, useRef } from 'react'
import { supabase, type Monta } from '@/lib/supabase'
import Sidebar from '@/components/Sidebar'

function FrameConBBox({
  src,
  bbox,
  label,
  show,
}: {
  src: string
  bbox: string | null | undefined
  label: string
  show: boolean
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null)
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [isDragging, setIsDragging] = useState(false)
  const dragStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 })

  function recompute() {
    const container = containerRef.current
    const img = imgRef.current
    if (!container || !img || !img.naturalWidth) return
    const cw = container.clientWidth
    const ch = container.clientHeight
    const iw = img.naturalWidth
    const ih = img.naturalHeight
    // 'contain': la imagen se escala al máximo que entre sin recortarse
    const scale = Math.min(cw / iw, ch / ih)
    const renderedW = iw * scale
    const renderedH = ih * scale
    setRect({
      left: (cw - renderedW) / 2,
      top: (ch - renderedH) / 2,
      width: renderedW,
      height: renderedH,
    })
  }

  useEffect(() => {
    recompute()
    setZoom(1)
    setPan({ x: 0, y: 0 })
    window.addEventListener('resize', recompute)
    return () => window.removeEventListener('resize', recompute)
  }, [src])

  function clampPan(nextPan: { x: number; y: number }, z: number) {
    const container = containerRef.current
    if (!container) return nextPan
    const maxX = (container.clientWidth * (z - 1)) / 2
    const maxY = (container.clientHeight * (z - 1)) / 2
    return {
      x: Math.min(maxX, Math.max(-maxX, nextPan.x)),
      y: Math.min(maxY, Math.max(-maxY, nextPan.y)),
    }
  }

  // Re-clampea el pan cada vez que cambia el zoom (por ejemplo al hacer zoom out)
  useEffect(() => {
    setPan(p => clampPan(p, zoom))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom])

  function clampZoom(z: number) {
    return Math.min(6, Math.max(1, z))
  }

  // React registra onWheel como "passive" por defecto, lo que hace que
  // e.preventDefault() no tenga efecto y la página scrollee igual.
  // Por eso enganchamos el evento nativo del DOM con { passive: false }.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    function onWheelNative(e: WheelEvent) {
      e.preventDefault()
      const delta = -e.deltaY * 0.0015
      setZoom(z => clampZoom(z + delta * z))
    }
    el.addEventListener('wheel', onWheelNative, { passive: false })
    return () => el.removeEventListener('wheel', onWheelNative)
  }, [])

  function zoomIn() {
    setZoom(z => clampZoom(z * 1.4))
  }
  function zoomOut() {
    setZoom(z => clampZoom(z / 1.4))
  }
  function resetZoom() {
    setZoom(1)
    setPan({ x: 0, y: 0 })
  }

  function handleMouseDown(e: React.MouseEvent) {
    if (zoom <= 1) return
    setIsDragging(true)
    dragStart.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y }
  }
  function handleMouseMove(e: React.MouseEvent) {
    if (!isDragging) return
    const dx = e.clientX - dragStart.current.x
    const dy = e.clientY - dragStart.current.y
    setPan(clampPan({ x: dragStart.current.panX + dx, y: dragStart.current.panY + dy }, zoom))
  }
  function handleMouseUp() {
    setIsDragging(false)
  }
  function panStep(dx: number, dy: number) {
    setPan(p => clampPan({ x: p.x + dx, y: p.y + dy }, zoom))
  }

  let bboxStyle: { left: number; top: number; width: number; height: number } | null = null
  if (show && bbox && rect && imgRef.current?.naturalWidth) {
    try {
      const b = JSON.parse(bbox) // [x, y, width, height] en píxeles de la imagen original
      const scaleX = rect.width / imgRef.current.naturalWidth
      const scaleY = rect.height / imgRef.current.naturalHeight
      bboxStyle = {
        left: rect.left + b[0] * scaleX,
        top: rect.top + b[1] * scaleY,
        width: b[2] * scaleX,
        height: b[3] * scaleY,
      }
    } catch {}
  }

  const canPan = zoom > 1

  return (
    <div className="flex flex-row gap-3 items-stretch">
    <div
      ref={containerRef}
      className="relative flex-1 min-w-0 h-[560px] bg-gray-900 rounded-lg overflow-hidden select-none"
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onDoubleClick={resetZoom}
      style={{ cursor: zoom > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default' }}
    >
      <div
        style={{
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transformOrigin: 'center center',
          width: '100%',
          height: '100%',
          position: 'relative',
          transition: isDragging ? 'none' : 'transform 0.1s ease-out',
        }}
      >
        {src ? (
          <img
            ref={imgRef}
            src={src}
            alt="Frame"
            className="w-full h-full object-contain pointer-events-none"
            onLoad={recompute}
            draggable={false}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <span className="text-gray-500 text-sm">Imagen no disponible</span>
          </div>
        )}
        {bboxStyle && (
          <div
            className="absolute border-2 border-red-400 rounded pointer-events-none"
            style={{ left: bboxStyle.left, top: bboxStyle.top, width: bboxStyle.width, height: bboxStyle.height }}
          >
            <span className="absolute -top-5 left-0 bg-red-400 text-white text-xs px-1 rounded whitespace-nowrap">
              {label}
            </span>
          </div>
        )}
      </div>

    </div>

      <div className="w-36 flex-shrink-0 flex flex-col gap-3 bg-gray-50 border border-gray-200 rounded-lg p-3">
        <div>
          <div className="text-xs text-gray-500 mb-1.5 text-center">Zoom</div>
          <div className="flex items-center justify-center gap-1 bg-white border border-gray-200 rounded-lg p-1">
            <button
              type="button"
              onClick={zoomOut}
              className="w-8 h-8 flex items-center justify-center text-gray-700 text-base font-medium rounded hover:bg-gray-100"
            >
              −
            </button>
            <button
              type="button"
              onClick={resetZoom}
              className="px-1.5 h-8 flex items-center justify-center text-gray-700 text-xs font-medium rounded hover:bg-gray-100"
              title="Restablecer zoom"
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              onClick={zoomIn}
              className="w-8 h-8 flex items-center justify-center text-gray-700 text-base font-medium rounded hover:bg-gray-100"
            >
              +
            </button>
          </div>
        </div>

        <div>
          <div className="text-xs text-gray-500 mb-1.5 text-center">Mover</div>
          <div
            className="grid gap-0.5 bg-white border border-gray-200 rounded-lg p-1 mx-auto"
            style={{ gridTemplateColumns: 'repeat(3, 28px)', gridTemplateRows: 'repeat(3, 28px)', opacity: canPan ? 1 : 0.4, width: 'fit-content' }}
          >
            <div />
            <button
              type="button"
              disabled={!canPan}
              onClick={() => panStep(0, 40)}
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed"
              title="Mover arriba"
            >
              ↑
            </button>
            <div />
            <button
              type="button"
              disabled={!canPan}
              onClick={() => panStep(40, 0)}
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed"
              title="Mover izquierda"
            >
              ←
            </button>
            <button
              type="button"
              disabled={!canPan}
              onClick={resetZoom}
              className="flex items-center justify-center text-gray-400 rounded hover:bg-gray-100 disabled:cursor-not-allowed text-xs"
              title="Centrar"
            >
              ⟲
            </button>
            <button
              type="button"
              disabled={!canPan}
              onClick={() => panStep(-40, 0)}
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed"
              title="Mover derecha"
            >
              →
            </button>
            <div />
            <button
              type="button"
              disabled={!canPan}
              onClick={() => panStep(0, -40)}
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed"
              title="Mover abajo"
            >
              ↓
            </button>
            <div />
          </div>
        </div>

        <div className="text-[11px] text-gray-400 text-center leading-tight mt-auto">
          Rueda del mouse y arrastre también funcionan
        </div>
      </div>
    </div>
  )
}

export default function MontasPage() {
  const [montas, setMontas] = useState<Monta[]>([])
  const [selected, setSelected] = useState<Monta | null>(null)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [filtroEstado, setFiltroEstado] = useState('')
  const [showBbox, setShowBbox] = useState(true)

  useEffect(() => {
    fetchMontas()
    const channel = supabase
      .channel('montas-realtime')
      .on('postgres_changes', {
        event: 'INSERT',
        schema: 'public',
        table: 'montas',
      }, (payload) => {
        setMontas(prev => [payload.new as Monta, ...prev])
      })
      .subscribe()
    return () => { supabase.removeChannel(channel) }
  }, [])

  async function fetchMontas() {
    setLoading(true)
    const { data, error } = await supabase
      .from('montas')
      .select(`*, frames_formateados ( ruta_archivo ), vacas ( id_negocio, ruta_fotos )`)
      .order('fecha_monta', { ascending: false })
      .limit(100)
    if (!error && data) setMontas(data)
    setLoading(false)
  }

  const filtradas = montas.filter(m => {
    const matchEstado = !filtroEstado || m.estado_id === filtroEstado
    const matchSearch = !search ||
      (m.vacas?.id_negocio && String(m.vacas.id_negocio).includes(search)) ||
      m.estado_id.includes(search)
    return matchEstado && matchSearch
  })

  const identificadas = montas.filter(m => m.estado_id === 'identificada').length
  const pendientes = montas.filter(m => m.estado_id === 'pendiente').length
  const fallidas = montas.filter(m => m.estado_id === 'fallida').length

  function exportCSV() {
    const rows = [['ID','Fecha','Estado','Confianza','Vaca ID','Conf ID']]
    montas.forEach(m => rows.push([
      String(m.id), m.fecha_monta, m.estado_id, String(m.confianza),
      m.vacas?.id_negocio ? String(m.vacas.id_negocio) : '',
      m.confianza_id ? String(m.confianza_id) : '',
    ]))
    const csv = rows.map(r => r.join(',')).join('\n')
    const a = document.createElement('a')
    a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv)
    a.download = 'montas.csv'
    a.click()
  }

  function pillColor(estado: string) {
    if (estado === 'identificada') return 'bg-green-100 text-green-800'
    if (estado === 'pendiente') return 'bg-yellow-100 text-yellow-800'
    return 'bg-red-100 text-red-800'
  }

  function pillLabel(estado: string) {
    if (estado === 'identificada') return 'Identificada'
    if (estado === 'pendiente') return 'Pendiente'
    return 'Fallida'
  }


  return (
    <div className="flex h-screen w-full">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">

        <div className="h-12 bg-white border-b border-gray-200 flex items-center px-5 gap-3 flex-shrink-0">
          <span className="text-sm font-medium text-gray-900 flex-1">Montas detectadas</span>
          <input
            type="text"
            placeholder="Buscar vaca..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="h-8 text-xs px-3 border border-gray-200 rounded-lg w-40 focus:outline-none"
          />
          <select
            value={filtroEstado}
            onChange={e => setFiltroEstado(e.target.value)}
            className="h-8 text-xs px-2 border border-gray-200 rounded-lg focus:outline-none"
          >
            <option value="">Todos los estados</option>
            <option value="identificada">Identificadas</option>
            <option value="pendiente">Pendientes</option>
            <option value="fallida">Fallidas</option>
          </select>
          <button onClick={exportCSV} className="h-8 px-3 text-xs border border-gray-200 rounded-lg hover:bg-gray-50">
            ↓ Exportar CSV
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 flex flex-col gap-4">
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: 'Montas totales', value: montas.length, color: 'text-gray-900' },
              { label: 'Identificadas', value: identificadas, color: 'text-green-700' },
              { label: 'Pendientes', value: pendientes, color: 'text-yellow-700' },
              { label: 'Fallidas', value: fallidas, color: 'text-red-700' },
            ].map(m => (
              <div key={m.label} className="bg-white border border-[#E7E5E4] rounded-xl p-3">
                <div className="text-xs text-gray-500 mb-1">{m.label}</div>
                <div className={`text-2xl font-medium ${m.color}`}>{m.value}</div>
              </div>
            ))}
          </div>

          <div className="flex gap-4 flex-1 min-h-0">
            <div className="w-72 flex-shrink-0 flex flex-col gap-2 overflow-y-auto">
              {loading && <div className="text-sm text-gray-400 text-center py-8">Cargando...</div>}
              {!loading && filtradas.length === 0 && (
                <div className="text-sm text-gray-400 text-center py-8">Sin resultados</div>
              )}
              {filtradas.map(m => (
                <div
                  key={m.id}
                  onClick={() => setSelected(m)}
                  className={`bg-white border rounded-xl p-3 cursor-pointer transition-colors ${
                    selected?.id === m.id ? 'border-blue-400' : 'border-gray-200 hover:border-gray-300'
                  }`}
                >
                  <div className="flex items-center gap-2 mb-2">
                    <div className="w-12 h-9 rounded bg-gray-100 flex items-center justify-center text-lg flex-shrink-0">📷</div>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-medium text-gray-900">Monta #{m.id}</div>
                      <div className="text-xs text-gray-400">
                        {new Date(m.fecha_monta).toLocaleString('es-AR', {
                          day: '2-digit', month: '2-digit',
                          hour: '2-digit', minute: '2-digit'
                        })}
                      </div>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${pillColor(m.estado_id)}`}>
                      {pillLabel(m.estado_id)}
                    </span>
                  </div>
                  <div className="flex gap-1 flex-wrap">
                    <span className="text-xs px-2 py-0.5 rounded-full bg-blue-50 text-blue-700">
                      Det {m.confianza != null ? Math.round(m.confianza * 100) : 0}%
                    </span>
                    {m.vacas?.id_negocio && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-green-50 text-green-700">
                        Vaca #{m.vacas.id_negocio}
                      </span>
                    )}
                    {m.confianza_id && (
                      <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                        ID {Math.round(m.confianza_id * 100)}%
                      </span>
                    )}
                  </div>
                  <div className="mt-2 h-1 rounded-full bg-gray-100 overflow-hidden">
                    <div className="h-full rounded-full bg-green-500" style={{ width: `${m.confianza != null ? Math.round(m.confianza * 100) : 0}%` }} />
                  </div>
                </div>
              ))}
            </div>
            <div className="flex-1 flex flex-col gap-3 min-w-0">
              {!selected ? (
                <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
                  ← Seleccioná una monta para ver el detalle
                </div>
              ) : (
                <>
                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-gray-100 flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-900">Frame capturado</span>
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
                      <FrameConBBox
                        src={selected.frames_formateados?.ruta_archivo || ''}
                        bbox={selected.bbox_monta}
                        label={selected.confianza != null ? `Monta ${selected.confianza.toFixed(2)}` : 'Monta'}
                        show={showBbox}
                      />
                    </div>
                  </div>

                  <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
                    <div className="px-4 py-2.5 border-b border-gray-100 flex items-center justify-between">
                      <span className="text-sm font-medium text-gray-900">Identificación</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${pillColor(selected.estado_id)}`}>
                        {pillLabel(selected.estado_id)}
                      </span>
                    </div>
                    <div className="p-4">
                      {selected.vacas?.id_negocio ? (
                        <>
                          <div className="flex items-center gap-3 mb-4">
                            <div className="w-11 h-11 rounded-lg bg-gray-100 flex items-center justify-center text-2xl flex-shrink-0">🐄</div>
                            <div>
                              <div className="text-base font-medium text-gray-900">Vaca #{selected.vacas.id_negocio}</div>
                              <div className="text-xs text-gray-500">
                                Confianza: {selected.confianza_id ? Math.round(selected.confianza_id * 100) : 0}%
                              </div>
                            </div>
                          </div>
                          <div className="grid grid-cols-2 gap-2">
                            <div className="bg-gray-50 rounded-lg p-3">
                              <div className="text-xs text-gray-400">Fecha detección</div>
                              <div className="text-sm font-medium text-gray-900 mt-0.5">
                                {new Date(selected.fecha_monta).toLocaleString('es-AR')}
                              </div>
                            </div>
                            <div className="bg-gray-50 rounded-lg p-3">
                              <div className="text-xs text-gray-400">Confianza detección</div>
                              <div className="text-sm font-medium text-gray-900 mt-0.5">
                                {selected.confianza != null ? Math.round(selected.confianza * 100) : 0}%
                              </div>
                            </div>
                          </div>
                        </>
                      ) : (
                        <div className="text-sm text-gray-400 py-2">
                          {selected.estado_id === 'pendiente'
                            ? 'Identificando vaca montada...'
                            : 'No se pudo identificar la vaca (confianza insuficiente)'}
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