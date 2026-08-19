export type CaseRecord = {
  caseId: string
  flow?: string
  status: string
  customerMessage: string
  currentStep: number
  payload: Record<string, unknown>
  result: Record<string, unknown>
  artifacts: {
    qrImageUrl?: string | null
    screenshots?: string[]
  }
  reason?: string | null
  updatedAt?: string
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export function createCase(flow = 'full_registration') {
  return req<CaseRecord>('/api/cases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ flow }),
  })
}

export function getCase(id: string) {
  return req<CaseRecord>(`/api/cases/${id}`)
}

export function patchPayload(id: string, payload: Record<string, unknown>, wizardStep?: number) {
  return req<CaseRecord>(`/api/cases/${id}/payload`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ payload, wizardStep }),
  })
}

export function validateCase(id: string) {
  return req<{ ok: boolean; errors: string[]; case: CaseRecord }>(`/api/cases/${id}/validate`, {
    method: 'POST',
  })
}

export function startCase(id: string) {
  return req<CaseRecord>(`/api/cases/${id}/start`, { method: 'POST' })
}

export function cancelCase(id: string) {
  return req<CaseRecord>(`/api/cases/${id}/cancel`, { method: 'POST' })
}

export async function uploadFile(id: string, file: File) {
  const fd = new FormData()
  fd.append('file', file)
  return req<{ fileId: string }>(`/api/cases/${id}/files`, { method: 'POST', body: fd })
}
