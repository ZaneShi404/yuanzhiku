// 统一 API 请求层（加固计划 Task 10）：错误信封解析、XHR 上传与 AbortSignal。

type ApiErrorLike = {
  status: number
  code: string
  message: string
  conflicts: string[]
  reason?: string
}

export class ApiError extends Error implements ApiErrorLike {
  status: number
  code: string
  conflicts: string[]
  reason?: string

  constructor(status: number, code: string, message: string, conflicts: string[] = [], reason?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.conflicts = conflicts
    this.reason = reason
  }
}

export function isAbort(value: unknown): boolean {
  return typeof value === 'object' && value !== null && (value as { name?: string }).name === 'AbortError'
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export const API = '/api/v1'

export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => ({}))
    const detail = isObject(payload) && isObject(payload.detail) ? payload.detail : {}
    const message = typeof detail.message === 'string' ? detail.message : '本地请求失败'
    const code = typeof detail.code === 'string' ? detail.code : `http_${response.status}`
    const conflicts = Array.isArray(detail.conflicts)
      ? detail.conflicts.filter((item): item is string => typeof item === 'string')
      : []
    const reason = typeof detail.reason === 'string' ? detail.reason : undefined
    throw new ApiError(response.status, code, message, conflicts, reason)
  }
  const text = await response.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export async function uploadFile(path: string, body: FormData, onProgress: (value: number) => void): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API}${path}`)
    xhr.responseType = 'json'
    xhr.upload.addEventListener('progress', event => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    })
    xhr.addEventListener('load', () => {
      const payload: unknown = xhr.response || (() => {
        try { return JSON.parse(xhr.responseText) } catch { return {} }
      })()
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload)
        return
      }
      const detail = isObject(payload) && isObject(payload.detail) ? payload.detail : {}
      reject(new ApiError(
        xhr.status,
        typeof detail.code === 'string' ? detail.code : `http_${xhr.status}`,
        typeof detail.message === 'string' ? detail.message : '文件导入失败',
      ))
    })
    xhr.addEventListener('error', () => reject(new ApiError(0, 'network_error', '本地请求失败')))
    xhr.send(body)
  })
}
