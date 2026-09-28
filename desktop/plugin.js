// Quota Router — desktop half. Talks to the same plugin_api.py as the dashboard tab
// (/api/plugins/quota-router/*), which calls the one canonical engine.
import {
  host, useQuery, queryClient, Button, Badge, Input,
  ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS, PALETTE_AREA
} from '@hermes/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'quota-router'
const PATH = '/quota-router'
const KEY = [ID]
let REST = null // set in register(ctx)

const pctColor = p => p == null ? 'var(--ui-text-tertiary)'
  : p >= 70 ? 'var(--ui-danger, var(--ui-accent))' : p >= 50 ? 'var(--ui-warning, var(--ui-text-secondary))' : 'var(--ui-text-secondary)'

function useOverview() {
  return useQuery({ queryKey: [...KEY, 'overview'], queryFn: () => REST('/overview'), refetchInterval: 60000 })
}
function useDecisions() {
  return useQuery({ queryKey: [...KEY, 'decisions'], queryFn: () => REST('/decisions?n=40'), refetchInterval: 60000 })
}

async function act(path, body, okMsg) {
  try {
    const r = await REST(path, { method: 'POST', body: body || {}, timeoutMs: 180000 })
    const lines = r && r.lines
    host.notify({ kind: 'info', message: lines ? (lines.length ? lines.join('\n') : 'Router pass done — no changes.') : okMsg })
  } catch (e) {
    host.notify({ kind: 'error', message: String((e && e.message) || e) })
  }
  queryClient.invalidateQueries({ queryKey: KEY })
}

// Two-click confirmation (no blocking dialogs in the renderer).
function ConfirmButton({ label, confirmLabel, onConfirm, disabled, variant }) {
  const [armed, setArmed] = useState(false)
  return jsx(Button, {
    size: 'sm', variant: armed ? 'destructive' : (variant || 'outline'), disabled,
    onClick: () => { if (!armed) { setArmed(true); setTimeout(() => setArmed(false), 4000); return } setArmed(false); onConfirm() },
    children: armed ? (confirmLabel || 'Confirm?') : label
  })
}

const cell = { padding: '4px 8px', borderBottom: '1px solid var(--ui-stroke-secondary)', verticalAlign: 'middle' }
const mono = { fontFamily: 'ui-monospace, Menlo, monospace', fontSize: '0.75rem' }
const muted = { color: 'var(--ui-text-tertiary)', fontSize: '0.75rem' }
const card = { border: '1px solid var(--ui-stroke-secondary)', borderRadius: 8, padding: 12 }

function Gauge({ label, pct, sub, value }) {
  return jsxs('div', { style: { ...card, minWidth: 170 }, children: [
    jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem' }, children: [
      jsx('span', { children: label }),
      jsx('b', { style: { color: pctColor(pct) }, children: value != null ? value : (pct == null ? '?' : pct + '%') })
    ] }),
    pct != null ? jsx('div', { style: { height: 5, marginTop: 6, background: 'var(--ui-stroke-secondary)', borderRadius: 3, overflow: 'hidden' },
      children: jsx('div', { style: { width: Math.min(100, pct) + '%', height: '100%', background: pctColor(pct) } }) }) : null,
    sub ? jsx('div', { style: { ...muted, marginTop: 4 }, children: sub }) : null
  ] })
}

function Accounts({ q }) {
  const a = q.anthropic || {}, c = q['openai-codex'] || {}, n = q.nous || {}, cp = q.copilot || {}, ds = q.deepseek || {}, orr = q.openrouter || {}
  return jsxs('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8 }, children: [
    jsx(Gauge, { label: 'Anthropic (Max)', pct: a.ok ? a.weekly : null, sub: a.ok ? 'session ' + a.session + '%' : (a.error || 'unknown') }),
    jsx(Gauge, { label: 'Codex (ChatGPT)', pct: c.ok ? c.weekly : null, sub: c.ok ? 'session ' + c.session + '%' : (c.error || 'unknown') }),
    jsx(Gauge, { label: 'Nous credits', value: n.ok ? '$' + n.credits : '?' }),
    jsx(Gauge, { label: 'Copilot', value: cp.ok ? (cp.plan || 'ok') : '?', sub: 'flat fee' }),
    jsx(Gauge, { label: 'DeepSeek 💳', value: ds.ok ? '$' + ds.balance_usd : '?', sub: 'metered' }),
    jsx(Gauge, { label: 'OpenRouter 💳', value: orr.ok ? '$' + orr.balance_usd : '?', sub: 'metered' })
  ] })
}

function RouteRow({ r, kinds }) {
  const [rung, setRung] = useState(String(r.rung))
  const cur = r.ladder[r.rung] || []
  const status = r.never_touch ? 'never touch' : r.hold ? '🔒 held' : r.drift ? '⚠️ drift' : 'auto'
  return jsxs('tr', { children: [
    jsx('td', { style: { ...cell, ...mono }, children: r.scope }),
    jsx('td', { style: cell, children: r['class'] }),
    jsxs('td', { style: { ...cell, ...mono }, children: [cur.join('/'), r.rung ? jsx(Badge, { style: { marginLeft: 6 }, children: 'fallback ' + r.rung }) : null] }),
    jsx('td', { style: { ...cell, ...mono, ...muted }, children: r.live ? r.live.join('/') : '—' }),
    jsx('td', { style: cell, title: r.drift || (r.hold && r.hold.note) || '', children: status }),
    jsx('td', { style: cell, children: jsxs('div', { style: { display: 'flex', gap: 4, alignItems: 'center' }, children: [
      jsx('select', {
        value: rung, onChange: e => setRung(e.target.value), disabled: r.never_touch,
        style: { background: 'transparent', color: 'inherit', border: '1px solid var(--ui-stroke-secondary)', borderRadius: 4, fontSize: '0.72rem', maxWidth: 220 },
        children: r.ladder.map((l, i) => jsx('option', { value: String(i), children: i + '. ' + l[0] + '/' + l[1] + (kinds[l[0]] === 'metered' ? ' 💳' : '') }, i))
      }),
      jsx(ConfirmButton, { label: 'Force', confirmLabel: 'Move + hold?', disabled: r.never_touch,
        onConfirm: () => act('/force', { scope: r.scope, rung: +rung }, r.scope + ' forced to rung ' + rung + ' and held') }),
      r.hold
        ? jsx(Button, { size: 'sm', variant: 'outline', onClick: () => act('/release', { scope: r.scope }, r.scope + ' released'), children: 'Release' })
        : jsx(Button, { size: 'sm', variant: 'outline', disabled: r.never_touch, onClick: () => act('/hold', { scope: r.scope, note: 'held from desktop' }, r.scope + ' held'), children: 'Hold' })
    ] }) })
  ] })
}

const THR = ['hot_weekly', 'return_weekly', 'room_weekly', 'hot_session', 'return_session', 'room_session', 'min_balance_usd']
function Thresholds({ t }) {
  const [v, setV] = useState(null)
  const cur = v || t
  return jsxs('div', { style: card, children: [
    jsx('div', { style: { fontWeight: 500, marginBottom: 4 }, children: 'Thresholds' }),
    jsx('div', { style: { ...muted, marginBottom: 8 }, children: 'Shed at hot · come back below return · receive load only below room.' }),
    jsx('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 8, marginBottom: 8 },
      children: THR.map(k => jsxs('label', { style: { display: 'flex', flexDirection: 'column', gap: 2, ...muted }, children: [
        k.replace('_', ' '),
        jsx(Input, { type: 'number', value: cur[k], onChange: e => setV({ ...cur, [k]: e.target.value }) })
      ] }, k)) }),
    jsx(Button, { size: 'sm', disabled: !v, onClick: () => { act('/thresholds', Object.fromEntries(THR.map(k => [k, Number(cur[k])])), 'Thresholds saved'); setV(null) }, children: 'Save thresholds' })
  ] })
}

function Decisions() {
  const { data } = useDecisions()
  const items = (data && data.items) || []
  return jsxs('div', { style: card, children: [
    jsx('div', { style: { fontWeight: 500, marginBottom: 6 }, children: 'Recent decisions' }),
    items.length === 0 ? jsx('div', { style: muted, children: 'none yet' }) :
    jsx('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }, children: jsx('tbody', { children: items.map((d, i) => jsxs('tr', { children: [
      jsx('td', { style: { ...cell, ...mono, ...muted }, children: (d.ts || '').replace('T', ' ').slice(0, 16) }),
      jsx('td', { style: cell, children: d.event ? d.event : (d.mode === 'dry_run' ? 'would' : d.applied ? 'switched' : 'FAILED') }),
      jsx('td', { style: { ...cell, ...mono }, children: d.scope || (d.values ? JSON.stringify(d.values) : d.mode || '') }),
      jsx('td', { style: { ...cell, ...mono }, children: d.to ? ((d.from || [])[1] || '') + ' → ' + d.to.join('/') + (d.metered ? ' 💳' : '') : '' }),
      jsx('td', { style: { ...cell, ...muted }, children: (d.reason || '') + (d.source ? ' · ' + d.source : '') })
    ] }, i)) }) })
  ] })
}

function QuotaPage() {
  const { data, isLoading, error } = useOverview()
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(false)
  if (isLoading) return jsx('div', { style: { padding: 16, ...muted }, children: 'Loading quota router…' })
  if (error || !data) return jsx('div', { style: { padding: 16 }, children:
    'Quota router backend unreachable (' + String((error && error.message) || 'no data') + '). The plugin must be enabled in config.yaml and the backend restarted.' })
  const enforce = data.mode === 'enforce'
  const routes = data.routes.filter(r => !filter || r.scope.includes(filter) || r['class'].includes(filter))
  const off = data.routes.filter(r => r.rung || r.hold || r.drift).length
  const run = async () => { setBusy(true); await act('/run', {}); setBusy(false) }
  return jsxs('div', { style: { padding: 16, display: 'flex', flexDirection: 'column', gap: 12, overflow: 'auto', height: '100%' }, children: [
    jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 8 }, children: [
      jsxs('div', { children: [
        jsx('div', { style: { fontSize: '1.1rem', fontWeight: 600 }, children: 'Quota Router' }),
        jsx('div', { style: muted, children: data.routes.length + ' routes · ' + off + ' off-default/held/drift · last probe ' + ((data.quota || {}).ts || '?') })
      ] }),
      jsxs('div', { style: { display: 'flex', gap: 6, alignItems: 'center' }, children: [
        jsx(Badge, { children: enforce ? 'ENFORCE' : 'DRY-RUN' }),
        jsx(ConfirmButton, { label: enforce ? 'Switch to dry-run' : 'Switch to enforce', confirmLabel: 'Confirm switch?',
          onConfirm: () => act('/mode', { mode: enforce ? 'dry_run' : 'enforce' }, 'Mode changed') }),
        jsx(Button, { size: 'sm', disabled: busy, onClick: run, children: busy ? 'Running…' : 'Run now' })
      ] })
    ] }),
    jsx(Accounts, { q: data.quota || {} }),
    jsxs('div', { style: card, children: [
      jsxs('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }, children: [
        jsx('div', { style: { fontWeight: 500 }, children: 'Routes' }),
        jsx(Input, { placeholder: 'filter (bot, class…)', value: filter, onChange: e => setFilter(e.target.value), style: { maxWidth: 220 } })
      ] }),
      jsx('div', { style: { overflowX: 'auto' }, children: jsxs('table', { style: { width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }, children: [
        jsx('thead', { children: jsx('tr', { children: ['Scope', 'Class', 'Router target', 'Live setting', 'Status', 'Actions'].map(h =>
          jsx('th', { style: { ...cell, textAlign: 'left', fontWeight: 500, color: 'var(--ui-text-tertiary)' }, children: h }, h)) }) }),
        jsx('tbody', { children: routes.map(r => jsx(RouteRow, { r, kinds: data.kinds || {} }, r.scope)) })
      ] }) })
    ] }),
    jsx(Thresholds, { t: data.thresholds || {} }),
    jsx(Decisions, {})
  ] })
}

function StatusChip() {
  const { data } = useOverview()
  if (!data) return null
  const q = data.quota || {}, a = q.anthropic || {}, c = q['openai-codex'] || {}
  const off = data.routes.filter(r => r.rung).length
  return jsx('button', {
    type: 'button', onClick: () => host.navigate(PATH),
    title: 'Quota router · ' + data.mode + ' · ' + off + ' routes on fallback',
    className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
    children: '⚡ A ' + (a.ok ? a.weekly + '%' : '?') + ' · C ' + (c.ok ? c.weekly + '%' : '?') + (off ? ' · ' + off + '↓' : '') + (data.mode === 'enforce' ? '' : ' · dry')
  })
}

export default {
  id: ID,
  name: 'Quota Router',
  register(ctx) {
    REST = ctx.rest
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: PATH }, render: () => jsx(QuotaPage, {}) },
      { id: 'nav', area: SIDEBAR_NAV_AREA, data: { path: PATH, label: 'Quota Router', codicon: 'dashboard' } },
      { id: 'chip', area: STATUSBAR_AREAS.right, order: 125, render: () => jsx(StatusChip, {}) },
      { id: 'open', area: PALETTE_AREA, data: { id: 'quota-router.open', label: 'Quota Router: open', keywords: ['quota', 'model', 'router'], run: () => host.navigate(PATH) } },
      { id: 'run', area: PALETTE_AREA, data: { id: 'quota-router.run', label: 'Quota Router: run now', keywords: ['quota', 'router'], run: () => act('/run', {}) } }
    ])
  }
}
