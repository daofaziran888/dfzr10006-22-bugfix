/** 订阅任务共用的字段与状态口径：列表页和最近推送详情页读同一份后端派生结果。 */
export type PushTarget = {
  id: string
  名称: string
  状态: string
  尝试次数: number
  失败原因?: string | null
  恢复位置?: string | null
}

export type PushRecord = {
  时间: string
  目标: string
  结果: string
  轮次: number
}

export type PushFailure = {
  时间: string
  目标: string
  原因: string
  恢复位置: string
  轮次: number
}

export type SubscribeRow = {
  id: number
  status: string
  任务编号: string
  数据源名称: string
  推送目标: string
  推送格式?: string
  推送频率?: string
  过滤条件?: string
  最近推送?: string
  最近推送记录?: PushRecord | null
  最近失败?: PushFailure | null
  推送结果?: string
  推送队列?: PushTarget[]
  推送记录?: PushRecord[]
  目标总数?: number
  已完成目标?: number
  可重试?: boolean
  恢复位置?: string | null
}

export const ACTIONS = ['启动推送', '暂停推送', '重试推送', '归档任务'] as const

/** 动作是否与当前状态匹配；最终仍以后端状态机为准。 */
export function actionAvailable(action: string, row: SubscribeRow): boolean {
  switch (action) {
    case '启动推送':
      return row.status === '草稿'
    case '暂停推送':
      return row.status === '推送中'
    case '重试推送':
      return row.status === '推送中' && Boolean(row.可重试)
    case '归档任务':
      return row.status === '已暂停'
    default:
      return false
  }
}

export function resultTone(result?: string): string {
  switch (result) {
    case '可重试':
    case '空队列':
      return 'danger'
    case '已送达':
    case '已完成':
      return 'success'
    case '推送中':
      return 'active'
    default:
      return 'muted'
  }
}

export function targetTone(status?: string): string {
  switch (status) {
    case '成功':
      return 'success'
    case '失败':
    case '中断':
      return 'danger'
    case '推送中':
      return 'active'
    case '待投递':
      return 'warn'
    default:
      return 'muted'
  }
}
