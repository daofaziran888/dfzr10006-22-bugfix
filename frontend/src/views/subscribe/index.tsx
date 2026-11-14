import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { fetchJson, request } from '@/api/client'
import {
  ACTIONS,
  actionAvailable,
  resultTone,
  type SubscribeRow,
} from './types'

const ENDPOINT = '/api/subscribe'
const columns = [
  "任务编号",
  "数据源名称",
  "推送目标",
  "推送格式",
  "推送频率",
  "过滤条件",
  "最近推送",
  "任务状态"
]
const statuses = ["", "草稿", "推送中", "已暂停", "已完成"]
const results = ["", "未启动", "推送中", "已停止", "可重试", "已送达", "已完成", "空队列"]

type Stats = { 总数: number; 推送中: number; 已暂停: number; 可重试: number }

export default function SubscribeView() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [rows, setRows] = useState<SubscribeRow[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<Stats>({ 总数: 0, 推送中: 0, 已暂停: 0, 可重试: 0 })
  const [listError, setListError] = useState('')
  const [actionError, setActionError] = useState('')
  const [actionNotice, setActionNotice] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)

  // 过滤条件放在 URL 上：刷新、返回列表时历史过滤条件仍然保留。
  const filters = useMemo(() => ({
    任务编号: searchParams.get('任务编号') ?? '',
    数据源名称: searchParams.get('数据源名称') ?? '',
    推送目标: searchParams.get('推送目标') ?? '',
    status: searchParams.get('status') ?? '',
    result: searchParams.get('result') ?? '',
  }), [searchParams])

  const queryString = useMemo(() => {
    const params = new URLSearchParams()
    Object.entries(filters).forEach(([key, value]) => {
      if (value) params.set(key, value)
    })
    return params.toString()
  }, [filters])

  const setFilter = useCallback((key: string, value: string) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    })
  }, [setSearchParams])

  const reload = useCallback(async () => {
    setLoading(true)
    setListError('')
    try {
      const [payload, nextStats] = await Promise.all([
        fetchJson<{ items: SubscribeRow[]; total: number }>(`${ENDPOINT}?${queryString}`),
        fetchJson<Stats>(`${ENDPOINT}/stats`),
      ])
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      setStats(nextStats)
    } catch (error) {
      setRows([])
      setTotal(0)
      setListError(error instanceof Error ? error.message : '数据订阅推送列表读取失败')
    } finally {
      setLoading(false)
    }
  }, [queryString])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: SubscribeRow) {
    if (busyId !== null) return
    setBusyId(row.id)
    setActionError('')
    setActionNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail ?? '数据订阅推送动作未生效，请稍后重试')
      }
      // 无论成功还是被状态机拒绝，都以服务端回传的任务明细刷新本行，避免停留上一轮结果。
      if (payload?.entry) {
        const entry = payload.entry as SubscribeRow
        setRows((prev) => prev.map((item) => (item.id === entry.id ? entry : item)))
      }
      if (payload?.ok === false) {
        setActionError(`${row.任务编号}：${payload.message}`)
      } else {
        setActionNotice(`${row.任务编号}：${payload?.message ?? '操作已完成'}`)
      }
      await reload()
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '数据订阅推送操作失败')
      await reload()
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="page" data-module="subscribe">
      <header className="page-head">
        <div>
          <h2>数据订阅推送管理</h2>
          <p className="page-desc">维护数据订阅的推送目标、格式选择与频率控制；状态只能按草稿、推送中、已暂停、已完成推进。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setActionError('订阅任务登记入口尚未接入审批流')}>
            登记订阅任务
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export?${queryString}`, '_blank')}>
            导出数据订阅推送清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {([
          ['订阅任务总数', stats.总数],
          ['推送中任务', stats.推送中],
          ['已暂停任务', stats.已暂停],
          ['可重试任务', stats.可重试],
        ] as const).map(([label, value]) => (
          <article key={label} className="stat-card">
            <span className="stat-label">{label}</span>
            <strong className="stat-value">{value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {columns.slice(0, 3).map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field as '任务编号' | '数据源名称' | '推送目标']}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilter(field, event.target.value)}
            />
          </label>
        ))}
        <label className="filter-item">
          <span>任务状态</span>
          <select value={filters.status} onChange={(event) => setFilter('status', event.target.value)}>
            {statuses.map((item) => <option key={item || 'all'} value={item}>{item || '全部状态'}</option>)}
          </select>
        </label>
        <label className="filter-item">
          <span>推送结果</span>
          <select value={filters.result} onChange={(event) => setFilter('result', event.target.value)}>
            {results.map((item) => <option key={item || 'all'} value={item}>{item || '全部结果'}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setSearchParams(new URLSearchParams())}>重置条件</button>
      </form>

      {(actionError || actionNotice) && (
        <div className={`notice ${actionError ? 'danger' : 'muted'}`}>
          {actionError || actionNotice}
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>推送结果</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const retryable = row.status === '推送中' && row.可重试
            return (
              <tr key={String(row.id)} data-status={row.推送结果}>
                {columns.map((column) => {
                  const value = (row as unknown as Record<string, string | number | null>)[column]
                  return (
                    <td key={column}>
                      {column === '任务编号'
                        ? <Link to={`/subscribe/${row.id}`}>{row.任务编号}</Link>
                        : value ?? '—'}
                    </td>
                  )
                })}
                <td>
                  <div className="result-cell">
                    <span className={`badge ${resultTone(row.推送结果)}`}>{row.推送结果 ?? '—'}</span>
                    <small>
                      {(row.已完成目标 ?? 0)}/{row.目标总数 ?? 0} 个目标已送达
                    </small>
                    {row.最近失败 && <small>原因：{row.最近失败.原因}</small>}
                    {row.恢复位置 && <small>恢复位置：{row.恢复位置}</small>}
                  </div>
                </td>
                <td className="row-actions">
                  {ACTIONS.map((action) => (
                    <button
                      key={action}
                      className="link"
                      type="button"
                      data-action={action}
                      data-highlight={action === '重试推送' && retryable ? 'true' : undefined}
                      disabled={busyId !== null}
                      title={actionAvailable(action, row) ? action : `当前状态「${row.status}」，执行会被拒绝并说明原因`}
                      onClick={() => void runAction(action, row)}
                    >
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 2}>
                {loading
                  ? '正在读取数据订阅推送数据…'
                  : listError
                    ? `${listError}，可重试`
                    : '当前过滤条件下没有数据订阅推送数据'}
                {!loading && (
                  <button className="btn ghost" type="button" style={{ marginLeft: 8 }} onClick={() => void reload()}>
                    重新加载
                  </button>
                )}
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条数据订阅推送记录</span>
        {listError && <span className="error-text">{listError}</span>}
      </footer>
    </section>
  )
}
