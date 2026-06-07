import { createClient } from '@supabase/supabase-js'

export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
)

export type Monta = {
  id: number
  frame_formateado_id: number
  bbox_monta: string
  confianza: number
  vaca_id: number | null
  bbox_vaca: string | null
  confianza_id: number | null
  estado_id: 'pendiente' | 'identificada' | 'fallida'
  fecha_monta: string
  fecha_alta: string
  frames_formateados?: { ruta_archivo: string }
  vacas?: { id_negocio: number; ruta_fotos: string | null }
}

export type Vaca = {
  id: number
  tambo_id: number
  parcela_id: number | null
  id_negocio: number
  ruta_fotos: string | null
  fecha_alta: string
}