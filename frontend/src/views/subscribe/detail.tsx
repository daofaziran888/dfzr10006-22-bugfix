import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { fetchJson, request } from '@/api/client'
import {
  ACTIONS,
  actionAvailable,
  resultTone,
  targetTone,
  type SubscribeRow,
} from './types'

const ENDPOINT = '/api/subscribe'

export default function SubscribeDetailView() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [row, setRow] = useState<SubscribeRow | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const payload = await fetchJson<SubscribeRow>(`${ENDPOINT}/${id}`)
      setRow(payload)
      setErrorMessage('')
    } catch (error) {
      setRow(null)
      setErrorMessage(error instanceof Error ? error.message : '推送详情读取失败')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string) {
    if (!row || busy) return
    setBusy(true)
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail ?? '订阅任务动作未生效，请稍后重试')
      }
      if (payload?.entry) {
        setRow(payload.entry as SubscribeRow)
      }
      if (payload?.ok === false) {
        setErrorMessage(payload.message)
      } else {
        setNotice(payload?.message ?? '操作已完成')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '订阅任务操作失败')
      await reload()
    } finally {
      setBusy(false)
    }
  }

  if (loading && !row) {
    return (
      <section className="page" data-module="subscribe-detail">
        <div className="empty-block">正在读取最近推送详情…</div>
      </section>
    )
  }

  if (!row) {
    return (
      <section className="page" data-module="subscribe-detail">
        <div className="breadcrumb"><Link to="/subscribe">返回数据订阅推送列表</Link></div>
        <div className="empty-block">
          {errorMessage || '订阅任务不存在或已归档'}
          <button className="btn ghost" type="button" style={{ marginLeft: 8 }} onClick={() => void reload()}>
            重试
          </button>
          <button className="btn" type="button" style={{ marginLeft: 8 }} onClick={() => navigate('/subscribe')}>
            返回列表
          </button>
        </div>
      </section>
    )
  }

  const queue = row.推送队列 ?? []
  const retryable = row.status === '推送中' && row.可重试

  return (
    <section className="page" data-module="subscribe-detail">
      <div className="breadcrumb"><Link to="/subscribe">返回数据订阅推送列表</Link></div>
      <header className="page-head">
        <div>
          <h2>{row.任务编号} · 最近推送详情</h2>
          <p className="page-desc">列表页与详情页共用同一份任务状态与推送结果，刷新后读取的是同一条任务记录。</p>
        </div>
        <div className="page-actions">
          {ACTIONS.map((action) => (
            <button
              key={action}
              className="btn"
              type="button"
              data-action={action}
              disabled={busy}
              title={actionAvailable(action, row) ? action : `当前状态「${row.status}」，执行会被拒绝并说明原因`}
              onClick={() => void runAction(action)}
            >
              {action}
            </button>
          ))}
        </div>
      </header>

      {(errorMessage || notice) && (
        <div className={`notice ${errorMessage ? 'danger' : 'muted'}`}>
          {errorMessage || notice}
        </div>
      )}

      <div className="panel">
        <h3>任务状态</h3>
        <div className="detail-grid">
          <div><dt>任务状态</dt><dd><span className="badge">{row.status}</span></dd></div>
          <div>
            <dt>推送结果</dt>
            <dd><span className={`badge ${resultTone(row.推送结果)}`}>{row.推送结果 ?? '—'}</span></dd>
          </div>
          <div><dt>目标进度</dt><dd>{row.已完成目标 ?? 0}/{row.目标总数 ?? 0} 个目标已送达</dd></div>
          <div><dt>最近推送</dt><dd>{row.最近推送 ?? '—'}</dd></div>
          <div><dt>数据源名称</dt><dd>{row.数据源名称}</dd></div>
          <div><dt>推送格式</dt><dd>{row.推送格式 ?? '—'}</dd></div>
          <div><dt>推送频率</dt><dd>{row.推送频率 ?? '—'}</dd></div>
          <div><dt>过滤条件</dt><dd>{row.过滤条件 ?? '—'}</dd></div>
          <div><dt>推送目标</dt><dd>{row.推送目标}</dd></div>
        </div>
        {row.恢复位置 && (
          <div className="notice danger">
            推送中断：{row.最近失败?.原因 ?? '推送目标失败'}
            <br />恢复位置：{row.恢复位置}
            {retryable && <>，点击「重试推送」从中断目标继续。</>}
          </div>
        )}
        {row.status === '已暂停' && (
          <div className="notice muted">任务已暂停，暂停后不会新增投递；确认无需再推送后可归档任务。</div>
        )}
      </div>

      <div className="panel">
        <h3>推送队列</h3>
        {queue.length === 0 ? (
          <div className="empty-block">
            推送队列为空，没有可投递的目标
            {retryable && (
              <button className="btn ghost" type="button" style={{ marginLeft: 8 }} onClick={() => void runAction('重试推送')}>
                重新加载队列
              </button>
            )}
          </div>
        ) : (
          <table className="target-list">
            <thead>
              <tr>
                <th>序号</th>
                <th>推送目标</th>
                <th>状态</th>
                <th>尝试次数</th>
                <th>失败原因</th>
                <th>恢复位置</th>
              </tr>
            </thead>
            <tbody>
              {queue.map((target, index) => (
                <tr key={target.id} data-target={target.名称}>
                  <td>{index + 1}</td>
                  <td>{target.名称}</td>
                  <td><span className={`badge ${targetTone(target.状态)}`}>{target.状态}</span></td>
                  <td>{target.尝试次数}</td>
                  <td>{target.失败原因 ?? '—'}</td>
                  <td>{target.恢复位置 ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h3>最近推送记录</h3>
        {row.最近推送记录 ? (
          <div className="detail-grid">
            <div><dt>时间</dt><dd>{row.最近推送记录.时间}</dd></div>
            <div><dt>目标</dt><dd>{row.最近推送记录.目标}</dd></div>
            <div><dt>结果</dt><dd>{row.最近推送记录.结果}</dd></div>
            <div><dt>轮次</dt><dd>第 {row.最近推送记录.轮次} 轮</dd></div>
          </div>
        ) : (
          <div className="empty-block">{row.status === '草稿' ? '任务尚未启动，暂无最近推送记录' : '暂无最近推送记录'}</div>
        )}
        {row.最近失败 && (
          <div className="notice danger">
            最近失败：{row.最近失败.时间} / {row.最近失败.目标} / {row.最近失败.原因}
            <br />恢复位置：{row.最近失败.恢复位置}
            <br />最近推送记录仍保留上一轮成功结果，失败不会覆盖它。
          </div>
        )}
      </div>
    </section>
  )
}
