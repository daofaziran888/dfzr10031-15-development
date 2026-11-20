<template>
  <section class="panel">
    <header class="panel-head">
      <h3>① 图表数据快照</h3>
      <p class="panel-desc">从数据来源读取坐标字段与数值列，按连续半开区间分桶；每桶保留行数与数值覆盖范围，缺失值单独统计。</p>
    </header>

    <form class="snapshot-form" @submit.prevent="submitCreate">
      <label class="form-field grow">
        <span>数据来源</span>
        <select v-model="form.source_name" required>
          <option value="" disabled>请选择数据来源</option>
          <option v-for="source in sources" :key="source.name" :value="source.name">
            {{ source.label }}（{{ source.record_count }} 行）
          </option>
        </select>
      </label>
      <label class="form-field grow">
        <span>图表名称</span>
        <input v-model="form.chart_name" placeholder="例如：第一期分布图" required />
      </label>
      <label class="form-field grow-2">
        <span>分桶边界（逗号分隔，左闭右开）</span>
        <input v-model="form.edges_text" placeholder="0, 10, 20" required />
      </label>
      <label class="form-field check">
        <input v-model="form.include_overflow" type="checkbox" />
        <span>启用溢出桶（边界外并入开放区间）</span>
      </label>
      <button class="btn primary" type="submit">生成快照</button>
    </form>
    <p v-if="formError" class="error-text">{{ formError }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>快照编号</th>
          <th>图表名称 / 来源</th>
          <th>分桶口径</th>
          <th>分布（区间：行数）</th>
          <th>缺失</th>
          <th>输入 / 总行数</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="snapshot in snapshots" :key="snapshot.snapshot_id">
          <td>{{ snapshot.snapshot_id }}</td>
          <td>
            <strong>{{ snapshot.chart_name }}</strong>
            <br /><small class="muted">{{ snapshot.source_name }} · {{ snapshot.coordinate_field }} / {{ snapshot.value_field }}</small>
          </td>
          <td><code>{{ edgesText(snapshot.spec.edges) }}</code></td>
          <td class="dist-cell">
            <span
              v-for="bucket in snapshot.buckets"
              :key="bucket.key"
              :class="['dist-chip', `chip-${bucket.kind}`]"
              :title="rangeTitle(bucket)"
            >
              {{ bucket.label }}：{{ bucket.row_count }}
            </span>
          </td>
          <td>
            <span :class="{ 'warn-text': snapshot.missing.count > 0 }">{{ snapshot.missing.count }}</span>
          </td>
          <td>{{ snapshot.input_record_count }} / {{ snapshot.total_rows }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="emitRecompute(snapshot)">复算核对</button>
            <button class="link" type="button" @click="emitReplay(snapshot)">新口径重放</button>
          </td>
        </tr>
        <tr v-if="!snapshots.length">
          <td class="empty-state" colspan="7">暂无快照</td>
        </tr>
      </tbody>
    </table>

    <div v-if="recomputeResult" class="result-box">
      <strong>复算核对 · {{ recomputeResult.snapshot_id }}</strong>
      <p :class="recomputeResult.ok ? 'ok-text' : 'error-text'">
        {{ recomputeResult.ok ? '原快照可由输入记录与原分桶口径逐字段复现' : '复算不一致' }}
      </p>
      <ul class="check-list">
        <li>输入记录指纹一致：{{ yesNo(recomputeResult.verification.input_fingerprint_match) }}</li>
        <li>聚合结果指纹一致：{{ yesNo(recomputeResult.verification.result_fingerprint_match) }}</li>
        <li v-if="recomputeResult.verification.problems.length">
          问题：{{ recomputeResult.verification.problems.join('；') }}
        </li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'

import type { ChartSnapshot, DataSource, RecomputeResult } from './types'

defineProps<{
  sources: DataSource[]
  snapshots: ChartSnapshot[]
}>()

const emit = defineEmits<{
  (event: 'create', payload: { source_name: string; edges: number[]; chart_name: string; include_overflow: boolean }): void
  (event: 'recompute', snapshot: ChartSnapshot): void
  (event: 'replay', snapshot: ChartSnapshot): void
}>()

const form = reactive({
  source_name: '',
  chart_name: '',
  edges_text: '0, 10, 20',
  include_overflow: true,
})
const formError = ref('')
const recomputeResult = ref<RecomputeResult | null>(null)

function edgesText(edges: number[]): string {
  return edges.join(', ')
}

function rangeTitle(bucket: ChartSnapshot['buckets'][number]): string {
  if (!bucket.row_count || bucket.value_min === null || bucket.value_max === null) {
    return '空桶'
  }
  return `数值覆盖范围：[${bucket.value_min}, ${bucket.value_max}]`
}

function yesNo(value: boolean): string {
  return value ? '是' : '否'
}

function submitCreate() {
  formError.value = ''
  const edges = form.edges_text
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .map(Number)
  if (edges.some(Number.isNaN) || edges.length < 2) {
    formError.value = '边界至少需要两个数字，且必须可解析'
    return
  }
  if (edges.some((edge, index) => index > 0 && edge <= edges[index - 1])) {
    formError.value = '边界必须严格递增，保证区间连续且不重叠'
    return
  }
  emit('create', {
    source_name: form.source_name,
    edges,
    chart_name: form.chart_name,
    include_overflow: form.include_overflow,
  })
  form.chart_name = ''
}

function emitRecompute(snapshot: ChartSnapshot) {
  emit('recompute', snapshot)
}

function emitReplay(snapshot: ChartSnapshot) {
  emit('replay', snapshot)
}

defineExpose({
  setRecomputeResult(result: RecomputeResult) {
    recomputeResult.value = result
  },
})
</script>

<style scoped>
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
.snapshot-form {
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
.warn-text {
  color: #b54708;
  font-weight: 600;
}
.error-text {
  color: #b42318;
}
.dist-cell {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  max-width: 360px;
}
.dist-chip {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 12px;
  border: 1px solid var(--border);
  background: #f8fafc;
}
.chip-underflow,
.chip-overflow {
  background: #fff7ed;
  border-color: #fdba74;
}
.result-box {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  background: #f8fafc;
  font-size: 13px;
}
.check-list {
  margin: 6px 0 0;
  padding-left: 18px;
}
.ok-text {
  color: #067647;
}
</style>
