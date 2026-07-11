'use client'
import { useEffect, useState, useRef } from 'react'

type Props = {
  src: string
  bbox: string | null | undefined
  label: string
  show: boolean
  alto?: number
}

export default function FrameViewer({ src, bbox, label, show, alto = 420 }: Props) {
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

  useEffect(() => {
    setPan(p => clampPan(p, zoom))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom])

  function clampZoom(z: number) {
    return Math.min(6, Math.max(1, z))
  }

  // React registra onWheel como passive; usamos listener nativo para poder
  // hacer preventDefault y que no scrollee la página.
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

  function zoomIn() { setZoom(z => clampZoom(z * 1.4)) }
  function zoomOut() { setZoom(z => clampZoom(z / 1.4)) }
  function resetZoom() { setZoom(1); setPan({ x: 0, y: 0 }) }
  function panStep(dx: number, dy: number) {
    setPan(p => clampPan({ x: p.x + dx, y: p.y + dy }, zoom))
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
  function handleMouseUp() { setIsDragging(false) }

  let bboxStyle: { left: number; top: number; width: number; height: number } | null = null
  if (show && bbox && rect && imgRef.current?.naturalWidth) {
    try {
      const b = JSON.parse(bbox)
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
        className="relative flex-1 min-w-0 bg-gray-900 rounded-lg overflow-hidden select-none"
        style={{ height: alto, cursor: zoom > 1 ? (isDragging ? 'grabbing' : 'grab') : 'default' }}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onDoubleClick={resetZoom}
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
            <button type="button" onClick={zoomOut}
              className="w-8 h-8 flex items-center justify-center text-gray-700 text-base font-medium rounded hover:bg-gray-100">−</button>
            <button type="button" onClick={resetZoom} title="Restablecer"
              className="px-1.5 h-8 flex items-center justify-center text-gray-700 text-xs font-medium rounded hover:bg-gray-100">
              {Math.round(zoom * 100)}%
            </button>
            <button type="button" onClick={zoomIn}
              className="w-8 h-8 flex items-center justify-center text-gray-700 text-base font-medium rounded hover:bg-gray-100">+</button>
          </div>
        </div>

        <div>
          <div className="text-xs text-gray-500 mb-1.5 text-center">Mover</div>
          <div
            className="grid gap-0.5 bg-white border border-gray-200 rounded-lg p-1 mx-auto"
            style={{ gridTemplateColumns: 'repeat(3, 28px)', gridTemplateRows: 'repeat(3, 28px)', opacity: canPan ? 1 : 0.4, width: 'fit-content' }}
          >
            <div />
            <button type="button" disabled={!canPan} onClick={() => panStep(0, 40)} title="Arriba"
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed">↑</button>
            <div />
            <button type="button" disabled={!canPan} onClick={() => panStep(40, 0)} title="Izquierda"
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed">←</button>
            <button type="button" disabled={!canPan} onClick={resetZoom} title="Centrar"
              className="flex items-center justify-center text-gray-400 rounded hover:bg-gray-100 disabled:cursor-not-allowed text-xs">⟲</button>
            <button type="button" disabled={!canPan} onClick={() => panStep(-40, 0)} title="Derecha"
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed">→</button>
            <div />
            <button type="button" disabled={!canPan} onClick={() => panStep(0, -40)} title="Abajo"
              className="flex items-center justify-center text-gray-700 rounded hover:bg-gray-100 disabled:cursor-not-allowed">↓</button>
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