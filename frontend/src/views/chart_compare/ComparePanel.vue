<template>
  <section class="panel">
    <header class="panel-head">
      <h3>② 两期分布对比看板</h3>
      <p class="panel-desc">
        同一看板放连续两期图：标出各档行数与数值覆盖范围、哪些区间可直接比较，并核对两期总行数是否对得上。
      </p>
    </header>

    <form class="compare-form" @submit.prevent="emitCompare">
      <label class="form-field grow">
        <span>基准期快照（旧一期）</span>
        <select v-model="selection.base_snapshot_id" required>
          <option value="" disabled>请选择基准期</option>
          <option v-for="snapshot in snapshots" :key="snapshot.snapshot_id" :value="snapshot.snapshot_id">
            {{ snapshot.snapshot_id }} · {{ snapshot.chart_name }}
          </option>
        </select>
      </label>
      <label class="form-field grow">
        <span>对照期快照（新一期）</span>
        <select v-model="selection.target_snapshot_id" required>
          <option value="" disabled>请选择对照期</option>
          <option v-for="snapshot in snapshots" :key="snapshot.snapshot_id" :value="snapshot.snapshot_id">
            {{ snapshot.snapshot_id }} · {{ snapshot.chart_name }}
          </option>
        </select>
      </label>
      <label class="form-field">
        <span>对比口径</span>
        <select v-model="selection.mode">
          <option value="">自动识别</option>
          <option value="overflow">保留旧桶 · 20 以上并入溢出区</option>
          <option value="replay">重放两期 · 统一新桶</option>
        </select>
      </label>
      <button class="btn primary" type="submit">生成对比</button>
    </form>
    <p v-if="compareError" class="error-text">{{ compareError }}</p>

    <template v-if="report">
      <div class="mode-banner" :class="report.mode">
        <strong>{{ report.mode === 'overflow' ? '保留旧桶 + 溢出区' : '统一新桶重放' }}</strong>
        <span>{{ report.mode_label }}</span>
      </div>

      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">基准期总行数</span>
          <strong class="stat-value">{{ report.totals_match.base_total }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">对照期总行数</span>
          <strong class="stat-value">{{ report.totals_match.target_total }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">两期行数差</span>
          <strong class="stat-value" :class="{ 'warn-text': !report.totals_match.equal }">
            {{ signed(report.totals_match.difference) }}
          </strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">可直接比较区间</span>
          <strong class="stat-value">{{ report.summary.comparable_buckets }} / {{ report.summary.bucket_slots }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">无法直接比较区间</span>
          <strong class="stat-value" :class="{ 'warn-text': report.summary.non_comparable_buckets > 0 }">
            {{ report.summary.non_comparable_buckets }}
          </strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">对照期溢出区行数</span>
          <strong class="stat-value" :class="{ 'warn-text': report.summary.target_overflow_rows > 0 }">
            {{ report.summary.target_overflow_rows }}
          </strong>
        </article>
      </div>

      <table class="data-table compare-table">
        <thead>
          <tr>
            <th>区间</th>
            <th>基准期行数</th>
            <th>对照期行数</th>
            <th>行差</th>
            <th>基准期数值覆盖</th>
            <th>对照期数值覆盖</th>
            <th>可比性</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in report.rows" :key="row.key" :class="{ 'row-limited': row.comparability === 'limited', 'row-none': row.comparability === 'none' }">
            <td>
              <code>{{ row.label }}</code>
              <span v-if="row.open_ended" class="tag tag-open">开放</span>
            </td>
            <td>
              {{ dash(row.base_count) }}
              <div class="bar bar-base" :style="{ width: barWidth(row.base_count) }"></div>
            </td>
            <td>
              {{ dash(row.target_count) }}
              <div class="bar bar-target" :style="{ width: barWidth(row.target_count) }"></div>
            </td>
            <td :class="deltaClass(row.count_delta)">{{ row.count_delta === null ? '—' : signed(row.count_delta) }}</td>
            <td class="muted">{{ rangeText(row.base_value_range) }}</td>
            <td class="muted">{{ rangeText(row.target_value_range) }}</td>
            <td>
              <span :class="['tag', `tag-${row.comparability}`]">{{ comparabilityLabel(row.comparability) }}</span>
              <p class="cell-note">{{ row.note }}</p>
            </td>
          </tr>
        </tbody>
      </table>

      <div class="two-col">
        <article class="sub-panel">
          <h4>无法直接比较的区间</h4>
          <ul v-if="report.non_comparable_intervals.length" class="warn-list">
            <li v-for="item in report.non_comparable_intervals" :key="item.key">
              <code>{{ item.label }}</code>
              <span>基准 {{ dash(item.base_count) }} / 对照 {{ dash(item.target_count) }} — {{ item.note }}</span>
            </li>
          </ul>
          <p v-else class="ok-text">无：所有承载数据的有限桶区间两期边界一致，可直接比较。</p>
        </article>

        <article class="sub-panel">
          <h4>总行数核对（分桶行数 + 缺失行数 = 输入行数）</h4>
          <ul class="check-list">
            <li v-for="item in report.reconciliation" :key="item.snapshot_id">
              <span :class="item.balanced ? 'ok-text' : 'error-text'">{{ item.balanced ? '✓ 平衡' : '✗ 不平' }}</span>
              {{ item.snapshot_id }}：输入 {{ item.input_record_count }} = 分桶 {{ item.bucketed_rows }}
              + 缺失 {{ item.missing_rows }}
            </li>
          </ul>
          <p :class="report.totals_match.equal ? 'ok-text' : 'warn-text'">
            两期总行数是否对得上：{{ report.totals_match.equal ? '是' : '否' }}（{{ report.totals_match.note }}）
          </p>
        </article>
      </div>

      <article class="sub-panel">
        <h4>缺失值单独统计（不进入任何数值桶）</h4>
        <ul class="check-list">
          <li v-for="item in report.missing" :key="item.snapshot_id">
            {{ item.snapshot_id }}：{{ item.missing_count }} 条
            <template v-if="item.missing_coordinates.length">（坐标：{{ item.missing_coordinates.join('、') }}）</template>
          </li>
        </ul>
      </article>
    </template>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'

import type { ChartSnapshot, Comparability, ComparisonReport } from './types'

const props = defineProps<{
  snapshots: ChartSnapshot[]
}>()

const emit = defineEmits<{
  (event: 'compare', payload: { base_snapshot_id: string; target_snapshot_id: string; mode: string | null }): void
}>()

const selection = reactive({
  base_snapshot_id: '',
  target_snapshot_id: '',
  mode: '',
})
const report = ref<ComparisonReport | null>(null)
const compareError = ref('')

function emitCompare() {
  emit('compare', {
    base_snapshot_id: selection.base_snapshot_id,
    target_snapshot_id: selection.target_snapshot_id,
    mode: selection.mode || null,
  })
}

function setReport(payload: ComparisonReport) {
  report.value = payload
  compareError.value = ''
}

function setError(message: string) {
  compareError.value = message
}

function dash(value: number | null): string | number {
  return value === null ? '—' : value
}

function signed(value: number): string {
  return value > 0 ? `+${value}` : String(value)
}

function rangeText(range: { min: number; max: number } | null): string {
  if (!range) {
    return '空桶'
  }
  return `[${range.min}, ${range.max}]`
}

function barWidth(count: number | null): string {
  const max = Math.max(1, ...props.snapshots.flatMap((s) => s.buckets.map((b) => b.row_count)))
  return `${Math.round(((count ?? 0) / max) * 100)}%`
}

function deltaClass(delta: number | null): string {
  if (delta === null || delta === 0) {
    return 'muted'
  }
  return delta > 0 ? 'delta-up' : 'delta-down'
}

function comparabilityLabel(value: Comparability): string {
  if (value === 'comparable') {
    return '可直接比较'
  }
  if (value === 'limited') {
    return '有限可比·开放区间'
  }
  return '不可直接比较'
}

defineExpose({ setReport, setError })
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
  flex: 1 1 200px;
}
.form-field select {
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  color: #1f2937;
  min-width: 220px;
}
.error-text {
  color: #b42318;
}
.ok-text {
  color: #067647;
}
.muted {
  color: var(--muted);
}
.warn-text {
  color: #b54708;
}
.mode-banner {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 13px;
}
.mode-banner.overflow {
  background: #fff7ed;
  border: 1px solid #fdba74;
}
.mode-banner.replay {
  background: #f0fdf4;
  border: 1px solid #86efac;
}
.compare-table td {
  vertical-align: top;
}
.row-limited {
  background: #fffaf0;
}
.row-none {
  background: #fef2f2;
}
.tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}
.tag-comparable {
  background: #dcfce7;
  color: #166534;
}
.tag-limited {
  background: #ffedd5;
  color: #9a3412;
}
.tag-none {
  background: #fee2e2;
  color: #991b1b;
}
.tag-open {
  background: #e2e8f0;
  color: #475569;
  margin-left: 4px;
}
.cell-note {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--muted);
  max-width: 240px;
}
.bar {
  height: 6px;
  border-radius: 3px;
  margin-top: 4px;
  min-width: 2px;
}
.bar-base {
  background: #93c5fd;
}
.bar-target {
  background: #6ee7b7;
}
.delta-up {
  color: #b54708;
  font-weight: 600;
}
.delta-down {
  color: #b42318;
  font-weight: 600;
}
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 12px;
}
.sub-panel {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
  margin-top: 12px;
}
.sub-panel h4 {
  margin: 0 0 8px;
  font-size: 13px;
}
.warn-list,
.check-list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.warn-list code {
  margin-right: 6px;
}
@media (max-width: 1100px) {
  .two-col {
    grid-template-columns: 1fr;
  }
}
</style>
