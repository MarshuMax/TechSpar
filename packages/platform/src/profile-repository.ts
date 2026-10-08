import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { defaultProfile, type CandidateProfile, type CandidateProfileRepository } from '@techspar/core'
import { atomicWriteJson } from './provider-settings-repository.ts'

function segment(value: string): string {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) throw new Error('Invalid user id')
  return value
}

/**
 * Python 时代写入的 profile.json 有两处与 v0.4.1 收紧后的响应契约不兼容：
 *  1) weak_points[].sr.last_score 等可选字段被写成显式 null（契约只接受 number/undefined）；
 *  2) 缺少后加的 due_reviews 字段。
 * 这里在**加载边界**做一次无损归一化：只删除已知集合里的 null 键、补空数组，
 * 其余字段（topic_mastery / 30 条 weak_points / behavior_signals 等）原样保留。
 */
function stripNulls(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stripNulls)
  if (value && typeof value === 'object') {
    const output: Record<string, unknown> = {}
    for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
      if (entry === null) continue
      output[key] = stripNulls(entry)
    }
    return output
  }
  return value
}

export function normalizeStoredProfile(value: unknown): CandidateProfile {
  const profile = stripNulls(value) as CandidateProfile
  if (!Array.isArray(profile.due_reviews)) profile.due_reviews = []
  for (const key of ['topic_mastery', 'behavior_signals', 'session_extractions'] as const) {
    const entry = (profile as Record<string, unknown>)[key]
    if (entry === undefined || entry === null) (profile as Record<string, unknown>)[key] = {}
  }
  for (const key of ['weak_points', 'strong_points', 'due_reviews'] as const) {
    const entry = (profile as Record<string, unknown>)[key]
    if (!Array.isArray(entry)) (profile as Record<string, unknown>)[key] = []
  }
  return profile
}

export class FileCandidateProfileRepository implements CandidateProfileRepository {
  private readonly tails = new Map<string, Promise<void>>()

  constructor(private readonly dataDir: string) {}

  private path(userId: string): string { return join(this.dataDir, 'users', segment(userId), 'profile', 'profile.json') }

  async load(userId: string): Promise<CandidateProfile> {
    try { return normalizeStoredProfile(JSON.parse(await readFile(this.path(userId), 'utf8'))) }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return defaultProfile(); throw error }
  }

  async save(userId: string, profile: CandidateProfile): Promise<void> {
    await this.exclusive(userId, async () => this.write(userId, profile))
  }

  async update<T>(userId: string, mutate: (profile: CandidateProfile) => T | Promise<T>): Promise<T> {
    return this.exclusive(userId, async () => {
      const profile = await this.load(userId)
      const result = await mutate(profile)
      await this.write(userId, profile)
      return result
    })
  }

  private async write(userId: string, profile: CandidateProfile): Promise<void> {
    profile.updated_at = new Date().toISOString()
    await atomicWriteJson(this.path(userId), profile)
  }

  private async exclusive<T>(userId: string, work: () => Promise<T>): Promise<T> {
    const previous = this.tails.get(userId) || Promise.resolve()
    let release!: () => void
    const current = new Promise<void>((resolve) => { release = resolve })
    const tail = previous.then(() => current)
    this.tails.set(userId, tail)
    await previous
    try { return await work() }
    finally { release(); if (this.tails.get(userId) === tail) this.tails.delete(userId) }
  }
}
