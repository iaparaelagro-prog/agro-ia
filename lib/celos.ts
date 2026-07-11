import { type Monta } from '@/lib/supabase'

// ---------------------------------------------------------------------------
// MODELO
// ---------------------------------------------------------------------------
// Una MONTA es una detección puntual de la IA.
// Un CELO es el evento biológico real: la misma vaca montada varias veces
// dentro de una ventana de horas. El usuario trabaja sobre CELOS, no montas.
// ---------------------------------------------------------------------------

export type Celo = {
  key: string                 // identificador lógico estable del celo
  idNegocio: string | null    // caravana de la vaca (null si no está identificada)
  identificada: boolean
  confianzaId: number | null  // mejor confianza de identificación del grupo
  inicio: string              // ISO — primera monta del evento
  fin: string                 // ISO — última monta del evento
  montas: Monta[]             // toda la evidencia del celo
  cantidadMontas: number      // más montas = celo más confiable
}

export type Ventana = {
  inicio: Date  // abre la ventana de inseminación
  optima: Date  // punto óptimo (~12 h del inicio del celo)
  fin: Date     // cierra la ventana
}

export type EstadoCelo =
  | 'proxima'     // la ventana todavía no abrió
  | 'abierta'     // inseminar ahora
  | 'perdida'     // la ventana se cerró sin inseminar
  | 'inseminada'  // ya se marcó como inseminada

const HORA = 60 * 60 * 1000
const MINUTO = 60 * 1000

// Misma vaca identificada montada dentro de estas horas = el mismo celo.
const VENTANA_ID_H = 10
// Montas SIN identificar muy cercanas en el tiempo = probablemente el mismo
// evento físico. Se agrupan corto para no mezclar vacas distintas.
const VENTANA_SININD_MIN = 30

// Ventana de inseminación relativa al inicio del celo (regla AM/PM).
// Ovulación ~24-30 h post inicio de celo; se insemina antes de ovular.
const VENTANA_ABRE_H = 8
const VENTANA_OPTIMA_H = 12
const VENTANA_CIERRA_H = 16

// Clave lógica estable de un celo. Normalizamos el timestamp a ISO canónico
// para que coincida siempre, aunque Postgres devuelva otro formato de fecha.
export function celoKey(idNegocio: string | null, inicioISO: string): string {
  const t = new Date(inicioISO).toISOString()
  return `${idNegocio ?? 'sinid'}-${t}`
}

// Agrupa una lista de montas en celos.
export function agruparEnCelos(montas: Monta[]): Celo[] {
  const orden = [...montas].sort(
    (a, b) => new Date(a.fecha_monta).getTime() - new Date(b.fecha_monta).getTime()
  )

  const celos: Celo[] = []

  for (const m of orden) {
    const idNegocio = m.vacas?.id_negocio != null ? String(m.vacas.id_negocio) : null
    const t = new Date(m.fecha_monta).getTime()
    const ventanaMs = idNegocio ? VENTANA_ID_H * HORA : VENTANA_SININD_MIN * MINUTO

    // ¿Hay un celo compatible todavía "abierto" para sumar esta monta?
    const candidato = celos.find(
      c => c.idNegocio === idNegocio && t - new Date(c.fin).getTime() <= ventanaMs
    )

    if (candidato) {
      candidato.montas.push(m)
      candidato.fin = m.fecha_monta
      candidato.cantidadMontas = candidato.montas.length
      if (m.confianza_id != null) {
        candidato.confianzaId = Math.max(candidato.confianzaId ?? 0, m.confianza_id)
      }
    } else {
      celos.push({
        key: celoKey(idNegocio, m.fecha_monta),
        idNegocio,
        identificada: idNegocio != null,
        confianzaId: m.confianza_id ?? null,
        inicio: m.fecha_monta,
        fin: m.fecha_monta,
        montas: [m],
        cantidadMontas: 1,
      })
    }
  }

  return celos
}

// Calcula la ventana de inseminación de un celo.
export function calcularVentana(celo: Celo): Ventana {
  const inicio = new Date(celo.inicio).getTime()
  return {
    inicio: new Date(inicio + VENTANA_ABRE_H * HORA),
    optima: new Date(inicio + VENTANA_OPTIMA_H * HORA),
    fin: new Date(inicio + VENTANA_CIERRA_H * HORA),
  }
}

// Determina el estado actual de un celo según su ventana y las inseminaciones
// ya registradas.
export function estadoCelo(
  celo: Celo,
  ventana: Ventana,
  inseminadas: Set<string>,
  ahora: Date = new Date()
): EstadoCelo {
  if (inseminadas.has(celo.key)) return 'inseminada'
  if (ahora < ventana.inicio) return 'proxima'
  if (ahora <= ventana.fin) return 'abierta'
  return 'perdida'
}