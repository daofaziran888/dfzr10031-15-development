<template>
  <section class="page chart-compare-page">
    <header class="page-head">
      <div>
        <h2>图表分桶聚合与两期对比看板</h2>
        <p class="page-desc">
          从数据来源按区间聚合生成可重算的图表数据快照；连续两期同看板对比时，标清溢出分布、可比较区间与两期总行数核对。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新数据</button>
      </div>
    </header>

    <article class="decision-box">
      <h4>口径决策（旧桶 [0,10)、[10,20)；两期观测 5、19 与 5、20、35）</h4>
      <ul>
        <li>
          <strong>保留旧桶 + 溢出区</strong>：旧桶人数可按原快照复现（[0,10)=1、[10,20)=1），20、35 并入
          <code>[20, +∞)</code> 溢出区（行数 2，覆盖 [20,35]）；但溢出区无上限、无内部分辨率，不能与有限桶直接比较。
        </li>
        <li>
          <strong>重放两期·统一新桶</strong>：用 <code>[0,10)、[10,20)、[20,30)、[30,40)</code> 对两期输入各算一版，
          20 与 35 分别落桶、尾部有完整分辨率，全部有限桶可直接比较；旧桶人数仍可在重放结果上还原。
        </li>
        <li>看板默认两种口径都保留，由对比口径切换；任何快照都能用输入记录与原分桶口径复算。</li>
      </ul>
    </article>

    <SnapshotPanel
      ref="snapshotPanelRef"
      :sources="sources"
      :snapshots="snapshots"
      @create="onCreateSnapshot"
      @recompute="onRecompute"
      @replay="onOpenReplay"
    />

    <ComparePanel ref="comparePanelRef" :snapshots="snapshots" @compare="onCompare" />

    <Teleport to="body">
      <div v-if="replayTarget" class="modal-overlay" @click.self="replayTarget = null">
        <div class="modal-card">
          <header class="modal-head">
            <h3>新口径重放 · {{ replayTarget.snapshot_id }}</h3>
            <button class="btn ghost" type="button" @click="replayTarget = null">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="onSubmitReplay">
            <p class="muted small">
              对原输入记录按新边界重新分桶，生成新快照；原快照保持不变，输入记录指纹一致。
            </p>
            <label class="modal-field">
              <span>新分桶边界（逗号分隔）</span>
              <input v-model="replayEdges" required />
            </label>
            <p v-if="replayError" class="error-text">{{ replayError }}</p>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="replayTarget = null">取消</button>
              <button class="btn primary" type="submit">生成重放快照</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <footer class="page-foot">
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
      <span>快照含输入指纹与结果指纹，复算逐字段校验</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

import ComparePanel from './ComparePanel.vue'
import SnapshotPanel from './SnapshotPanel.vue'
import type { ChartSnapshot, ComparisonReport, DataSource, RecomputeResult } from './types'

const ENDPOINT = '/api/chart-bucketing'

const sources = ref<DataSource[]>([])
const snapshots = ref<ChartSnapshot[]>([])
const message = ref('')
const messageOk = ref(true)

const snapshotPanelRef = ref<InstanceType<typeof SnapshotPanel> | null>(null)
const comparePanelRef = ref<InstanceType<typeof ComparePanel> | null>(null)

const replayTarget = ref<ChartSnapshot | null>(null)
const replayEdges = ref('0, 10, 20, 30, 40')
const replayError = ref('')

function flash(text: string, ok = true) {
  message.value = text
  messageOk.value = ok
}

async function reload() {
  try {
    const [sourceResp, snapshotResp] = await Promise.all([
      request(`${ENDPOINT}/sources`),
      request(`${ENDPOINT}/snapshots`),
    ])
    const sourcePayload = await sourceResp.json()
    const snapshotPayload = await snapshotResp.json()
    sources.value = sourcePayload.items ?? []
    snapshots.value = snapshotPayload.items ?? []
  } catch (error) {
    flash(error instanceof Error ? error.message : '数据加载失败', false)
  }
}

async function onCreateSnapshot(payload: {
  source_name: string
  edges: number[]
  chart_name: string
  include_overflow: boolean
}) {
  try {
    const response = await request(`${ENDPOINT}/snapshots`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.detail || '快照生成失败')
    }
    flash('图表数据快照已生成')
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '快照生成失败', false)
  }
}

async function onRecompute(snapshot: ChartSnapshot) {
  try {
    const response = await request(`${ENDPOINT}/snapshots/${snapshot.snapshot_id}/recompute`, {
      method: 'POST',
    })
    const result = (await response.json()) as RecomputeResult
    snapshotPanelRef.value?.setRecomputeResult(result)
    flash(`复算核对完成：${result.ok ? '逐字段一致' : '存在差异'}`, result.ok)
  } catch (error) {
    flash(error instanceof Error ? error.message : '复算失败', false)
  }
}

function onOpenReplay(snapshot: ChartSnapshot) {
  replayTarget.value = snapshot
  replayError.value = ''
  replayEdges.value = snapshot.spec.edges.concat(snapshot.spec.edges[snapshot.spec.edges.length - 1] + 10).join(', ')
}

async function onSubmitReplay() {
  if (!replayTarget.value) {
    return
  }
  replayError.value = ''
  const edges = replayEdges.value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number)
  if (edges.some(Number.isNaN) || edges.length < 2) {
    replayError.value = '边界至少需要两个可解析数字'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/snapshots/${replayTarget.value.snapshot_id}/replay`, {
      method: 'POST',
      body: JSON.stringify({ edges }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.detail || '重放失败')
    }
    replayTarget.value = null
    flash('新口径重放快照已生成，旧快照保持不变')
    await reload()
  } catch (error) {
    replayError.value = error instanceof Error ? error.message : '重放失败'
  }
}

async function onCompare(payload: {
  base_snapshot_id: string
  target_snapshot_id: string
  mode: string | null
}) {
  try {
    const response = await request(`${ENDPOINT}/compare`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.detail || '对比失败')
    }
    const report = (await response.json()) as ComparisonReport
    comparePanelRef.value?.setReport(report)
    flash('两期对比已生成')
  } catch (error) {
    const text = error instanceof Error ? error.message : '对比失败'
    comparePanelRef.value?.setError(text)
    flash(text, false)
  }
}

onMounted(reload)
</script>

<style scoped>
.chart-compare-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px 16px;
}
.panel-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.panel-desc {
  margin: 0 0 12px;
  color: var(--muted);
  font-size: 12px;
}
.decision-box {
  background: #f0f6ff;
  border: 1px solid #b9d4ff;
  border-radius: 10px;
  padding: 12px 16px;
  font-size: 13px;
}
.decision-box h4 {
  margin: 0 0 8px;
}
.decision-box ul {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.snapshot-form,
.compare-form {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin-bottom: 12px;
}
.form-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
}
.form-field.grow {
  flex: 1 1 180px;
}
.form-field.grow-2 {
  flex: 2 1 260px;
}
.form-field input,
.form-field select {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  color: #1f2937;
}
.form-field.check {
  flex-direction: row;
  align-items: center;
  gap: 6px;
}
.muted {
  color: var(--muted);
}
.small {
  font-size: 12px;
}
.ok-text {
  color: #067647;
}
.warn-text {
  color: #b54708;
}
.error-text {
  color: #b42318;
}
</style>
