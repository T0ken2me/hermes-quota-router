(function () {
  "use strict";
  const SDK = window.__HERMES_PLUGIN_SDK__;
  const { React } = SDK;
  const { useState, useEffect, useCallback } = SDK.hooks;
  const { Card, CardHeader, CardTitle, CardContent, Badge, Button, Input } = SDK.components;
  const h = React.createElement;
  const API = "/api/plugins/quota-router";

  const post = (path, body) =>
    SDK.fetchJSON(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

  function pctClass(p) {
    if (p == null) return "qr-muted";
    if (p >= 70) return "qr-hot";
    if (p >= 50) return "qr-warm";
    return "qr-ok";
  }

  function Gauge({ label, pct, sub }) {
    return h("div", { className: "qr-gauge" },
      h("div", { className: "qr-gauge-head" }, h("span", null, label), h("b", { className: pctClass(pct) }, pct == null ? "?" : pct + "%")),
      h("div", { className: "qr-bar" }, h("div", { className: "qr-fill " + pctClass(pct), style: { width: Math.min(100, pct || 0) + "%" } })),
      sub ? h("div", { className: "qr-sub" }, sub) : null);
  }

  function Balance({ label, value, ok, sub }) {
    return h("div", { className: "qr-gauge" },
      h("div", { className: "qr-gauge-head" }, h("span", null, label), h("b", { className: ok ? "qr-ok" : "qr-muted" }, value)),
      sub ? h("div", { className: "qr-sub" }, sub) : null);
  }

  function Quotas({ q }) {
    const a = q.anthropic || {}, c = q["openai-codex"] || {}, n = q.nous || {}, cp = q.copilot || {},
      ds = q.deepseek || {}, orr = q.openrouter || {};
    return h(Card, null,
      h(CardHeader, null, h(CardTitle, null, "Accounts"), h("div", { className: "qr-sub" }, "last probe " + (q.ts || "?"))),
      h(CardContent, { className: "qr-grid" },
        h(Gauge, { label: "Anthropic (Max)", pct: a.ok ? a.weekly : null, sub: a.ok ? "session " + a.session + "%" : (a.error || "unknown") }),
        h(Gauge, { label: "Codex (ChatGPT)", pct: c.ok ? c.weekly : null, sub: c.ok ? "session " + c.session + "%" : (c.error || "unknown") }),
        h(Balance, { label: "Nous credits", value: n.ok ? "$" + n.credits : (n.error || "?"), ok: n.ok }),
        h(Balance, { label: "Copilot", value: cp.ok ? (cp.plan || "ok") : "?", ok: cp.ok, sub: "flat fee" }),
        h(Balance, { label: "DeepSeek 💳", value: ds.ok ? "$" + ds.balance_usd : "?", ok: ds.ok, sub: "metered" }),
        h(Balance, { label: "OpenRouter 💳", value: orr.ok ? "$" + orr.balance_usd : "?", ok: orr.ok, sub: "metered" })));
  }

  function RouteRow({ r, kinds, busy, act }) {
    const [rung, setRung] = useState(String(r.rung));
    const cur = r.ladder[r.rung] || [];
    const live = r.live ? r.live.join("/") : "—";
    return h("tr", { className: r.drift ? "qr-row-drift" : "" },
      h("td", { className: "qr-mono" }, r.scope),
      h("td", null, r["class"]),
      h("td", { className: "qr-mono" }, cur.join("/"), r.rung ? h(Badge, { className: "qr-badge" }, "fallback " + r.rung) : null),
      h("td", { className: "qr-mono qr-sub" }, live),
      h("td", null,
        r.never_touch ? h(Badge, null, "never touch") :
        r.hold ? h(Badge, { className: "qr-held", title: (r.hold.note || "") + " — " + (r.hold.by || "") }, "🔒 held") :
        r.drift ? h(Badge, { className: "qr-drift", title: r.drift }, "⚠️ drift") : h("span", { className: "qr-sub" }, "auto")),
      h("td", { className: "qr-actions" },
        h("select", { className: "qr-select", value: rung, onChange: (e) => setRung(e.target.value), disabled: busy },
          r.ladder.map((l, i) => h("option", { key: i, value: String(i) }, i + ". " + l[0] + "/" + l[1] + (kinds[l[0]] === "metered" ? " 💳" : "")))),
        h(Button, { size: "sm", disabled: busy || r.never_touch,
          onClick: () => { const m = r.ladder[+rung]; if (window.confirm("Move " + r.scope + " to " + m.join("/") + " now and hold it?")) act("/force", { scope: r.scope, rung: +rung }); } }, "Force"),
        r.hold
          ? h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: () => act("/release", { scope: r.scope }) }, "Release")
          : h(Button, { size: "sm", variant: "outline", disabled: busy || r.never_touch, onClick: () => act("/hold", { scope: r.scope, note: "held from dashboard" }) }, "Hold")));
  }

  function Thresholds({ t, busy, act }) {
    const [v, setV] = useState(t);
    useEffect(() => setV(t), [JSON.stringify(t)]);
    const keys = ["hot_weekly", "return_weekly", "room_weekly", "hot_session", "return_session", "room_session", "min_balance_usd"];
    return h(Card, null,
      h(CardHeader, null, h(CardTitle, null, "Thresholds"),
        h("div", { className: "qr-sub" }, "Shed at hot · come back below return · receive load only below room. Must satisfy room ≤ return < hot.")),
      h(CardContent, null,
        h("div", { className: "qr-thr" }, keys.map((k) => h("label", { key: k, className: "qr-thr-item" },
          h("span", null, k.replace("_", " ")),
          h(Input, { type: "number", value: v[k], onChange: (e) => setV({ ...v, [k]: e.target.value }), disabled: busy })))),
        h(Button, { size: "sm", disabled: busy, onClick: () => act("/thresholds", Object.fromEntries(keys.map((k) => [k, Number(v[k])]))) }, "Save thresholds")));
  }

  function Decisions({ items }) {
    return h(Card, null,
      h(CardHeader, null, h(CardTitle, null, "Recent decisions")),
      h(CardContent, null,
        items.length === 0 ? h("div", { className: "qr-sub" }, "none yet") :
        h("table", { className: "qr-table" }, h("tbody", null, items.map((d, i) => h("tr", { key: i },
          h("td", { className: "qr-sub qr-mono" }, (d.ts || "").replace("T", " ").slice(0, 16)),
          h("td", null, d.event ? d.event : (d.mode === "dry_run" ? "would" : d.applied ? "switched" : "FAILED")),
          h("td", { className: "qr-mono" }, d.scope || (d.values ? JSON.stringify(d.values) : d.mode || "")),
          h("td", { className: "qr-mono" }, d.to ? (d.from || [])[1] + " → " + d.to.join("/") + (d.metered ? " 💳" : "") : ""),
          h("td", { className: "qr-sub" }, (d.reason || "") + (d.source ? " · " + d.source : ""))))))));
  }

  function SpendPanel() {
    const [range, setRange] = useState("7d");
    const [summary, setSummary] = useState(null);
    const [timeseries, setTimeseries] = useState(null);
    const [profiles, setProfiles] = useState(null);
    const [models, setModels] = useState(null);
    const [sessions, setSessions] = useState(null);
    const [err, setErr] = useState("");
    const [loading, setLoading] = useState(false);

    const loadSpend = useCallback((r) => {
      setLoading(true); setErr("");
      Promise.all([
        SDK.fetchJSON(API + "/spend/summary?range=" + r).then(setSummary),
        SDK.fetchJSON(API + "/spend/timeseries?range=" + r).then(setTimeseries),
        SDK.fetchJSON(API + "/spend/profiles?range=" + r).then(setProfiles),
        SDK.fetchJSON(API + "/spend/models?range=" + r).then(setModels),
        SDK.fetchJSON(API + "/spend/sessions?range=" + r + "&limit=20").then(setSessions),
      ]).catch((e) => setErr("Spend load failed: " + e.message))
        .finally(() => setLoading(false));
    }, []);

    useEffect(() => { loadSpend(range); }, [range, loadSpend]);

    const fmt$ = (v) => v == null ? "?" : "$" + (v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2));
    const fmtK = (v) => v == null ? "?" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "K" : String(v);

    function BarChart({ points }) {
      if (!points || !points.length) return h("div", { className: "qr-sub" }, "No data");
      const maxCost = Math.max(...points.map((p) => p.estimated_cost_usd || 0), 0.0001);
      return h("div", { className: "qr-chart" },
        points.map((p) => {
          const pct = Math.max(2, Math.round(((p.estimated_cost_usd || 0) / maxCost) * 54));
          const lbl = (p.date || "").slice(5);
          return h("div", { key: p.date, className: "qr-chart-col", title: p.date + " $" + (p.estimated_cost_usd || 0).toFixed(4) },
            h("div", { className: "qr-chart-bar", style: { height: pct + "px" } }),
            h("div", { className: "qr-chart-lbl" }, lbl));
        }));
    }

    const provNote = "estimated; by last activity";

    return h(Card, null,
      h(CardHeader, null,
        h("div", { className: "qr-spend-header" },
          h(CardTitle, null, "Spend"),
          h("div", { className: "qr-spend-range" },
            ["today", "7d", "30d"].map((r) =>
              h("button", { key: r, className: r === range ? "qr-active" : "", onClick: () => setRange(r) }, r)))),
        summary && summary.coverage && summary.coverage.profiles_partial > 0
          ? h("div", { className: "qr-sub" }, "Partial — " + summary.coverage.profiles_partial + " profile(s) unavailable")
          : null),
      h(CardContent, null,
        err ? h("div", { className: "qr-sub" }, err) : null,
        loading && !summary ? h("div", { className: "qr-sub" }, "Loading…") : null,
        summary ? h("div", null,
          h("div", { className: "qr-spend-totals" },
            h("div", { className: "qr-spend-card" },
              h("div", { className: "qr-spend-card-label" }, "Estimated cost"),
              h("div", { className: "qr-spend-card-val" }, fmt$(summary.estimated_cost_usd)),
              h("div", { className: "qr-spend-note" }, provNote)),
            h("div", { className: "qr-spend-card" },
              h("div", { className: "qr-spend-card-label" }, "Total tokens"),
              h("div", { className: "qr-spend-card-val" }, fmtK(summary.total_tokens))),
            h("div", { className: "qr-spend-card" },
              h("div", { className: "qr-spend-card-label" }, "Input tokens"),
              h("div", { className: "qr-spend-card-val" }, fmtK(summary.input_tokens))),
            h("div", { className: "qr-spend-card" },
              h("div", { className: "qr-spend-card-label" }, "Output tokens"),
              h("div", { className: "qr-spend-card-val" }, fmtK(summary.output_tokens))),
            summary.coverage && summary.coverage.pricing_unknown_rows > 0
              ? h("div", { className: "qr-spend-card" },
                  h("div", { className: "qr-spend-card-label" }, "Pricing unknown"),
                  h("div", { className: "qr-spend-card-val qr-warm" }, summary.coverage.pricing_unknown_rows + " rows"),
                  h("div", { className: "qr-spend-note" }, "cost omitted from total"))
              : null)) : null,
        timeseries && timeseries.points && timeseries.points.length
          ? h("div", null,
              h("div", { className: "qr-sub" }, "Daily cost (" + range + ")"),
              h(BarChart, { points: timeseries.points }))
          : null,
        profiles && profiles.profiles && profiles.profiles.length
          ? h("div", { style: { marginTop: ".75rem" } },
              h("div", { className: "qr-sub", style: { marginBottom: ".35rem" } }, "Per profile"),
              h("div", { className: "qr-scroll" },
                h("table", { className: "qr-table" },
                  h("thead", null, h("tr", null,
                    ["Profile", "Cost (est.)", "Input", "Output", "Total tokens", "Status"].map(
                      (x) => h("th", { key: x }, x)))),
                  h("tbody", null, profiles.profiles.map((p) =>
                    h("tr", { key: p.profile },
                      h("td", { className: "qr-mono" }, p.profile),
                      h("td", null, fmt$(p.estimated_cost_usd)),
                      h("td", null, fmtK(p.input_tokens)),
                      h("td", null, fmtK(p.output_tokens)),
                      h("td", null, fmtK(p.total_tokens)),
                      h("td", { className: "qr-sub" }, p.source_status)))))))
          : null,
        models && models.models && models.models.length
          ? h("div", { style: { marginTop: ".75rem" } },
              h("div", { className: "qr-sub", style: { marginBottom: ".35rem" } }, "Per model"),
              h("div", { className: "qr-scroll" },
                h("table", { className: "qr-table" },
                  h("thead", null, h("tr", null,
                    ["Model", "Cost (est.)", "Input", "Output", "Sessions"].map(
                      (x) => h("th", { key: x }, x)))),
                  h("tbody", null, models.models.map((m) =>
                    h("tr", { key: m.model },
                      h("td", { className: "qr-mono" }, m.model),
                      h("td", null, fmt$(m.estimated_cost_usd)),
                      h("td", null, fmtK(m.input_tokens)),
                      h("td", null, fmtK(m.output_tokens)),
                      h("td", null, m.sessions)))))))
          : null,
        sessions && sessions.sessions && sessions.sessions.length
          ? h("div", { style: { marginTop: ".75rem" } },
              h("div", { className: "qr-sub", style: { marginBottom: ".35rem" } }, "Top sessions (no titles shown)"),
              h("div", { className: "qr-scroll" },
                h("table", { className: "qr-table" },
                  h("thead", null, h("tr", null,
                    ["Profile", "Session", "Model(s)", "Date", "Input", "Output", "Cost (est.)"].map(
                      (x) => h("th", { key: x }, x)))),
                  h("tbody", null, sessions.sessions.map((s, i) =>
                    h("tr", { key: i },
                      h("td", { className: "qr-mono" }, s.profile),
                      h("td", { className: "qr-mono qr-sub" }, s.session_id_short),
                      h("td", { className: "qr-mono" }, (s.models || []).join(", ")),
                      h("td", { className: "qr-sub" }, s.date || ""),
                      h("td", null, fmtK(s.input_tokens)),
                      h("td", null, fmtK(s.output_tokens)),
                      h("td", null, fmt$(s.estimated_cost_usd))))))))
          : null));
  }

  // ── Account manager components ───────────────────────────────────────────

  const PROVIDERS = ["openai-codex","anthropic","nous","openai","google","xai","deepseek","mistral"];
  const TASK_CLASSES = ["coding","reasoning","review","vision","background"];
  const SAFE_RE = /^[A-Za-z0-9][A-Za-z0-9._:@/\-]{0,127}$/;

  function statusClass(s) {
    if (s === "validated") return "qr-status-ready";
    if (s === "failed") return "qr-status-failed";
    if (s === "unsupported") return "qr-status-unsupported";
    return "qr-status-unvalidated";
  }

  function AccountUsageInline({ account_ref }) {
    const [usage, setUsage] = useState(null);
    const [err, setErr] = useState("");
    const load = useCallback(() => {
      SDK.fetchJSON(API + "/accounts/" + encodeURIComponent(account_ref) + "/usage")
        .then(setUsage).catch((e) => setErr(e.message));
    }, [account_ref]);
    useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);
    if (err) return h("div", { className: "qr-sub" }, "Usage unavailable");
    if (!usage) return h("div", { className: "qr-sub" }, "Loading…");
    const freshClass = usage.freshness === "fresh" ? "qr-ok" : usage.freshness === "stale" ? "qr-warm" : "qr-muted";
    return h("div", null,
      h("div", { className: "qr-sub" },
        h("b", { className: freshClass }, usage.freshness),
        usage.fetched_at ? " · " + usage.fetched_at.slice(0, 16).replace("T", " ") + " UTC" : ""),
      usage.windows && usage.windows.length
        ? h("div", { className: "qr-grid", style: { marginTop: ".35rem" } },
            usage.windows.map((w) => h(Gauge, { key: w.name, label: w.name, pct: w.used_percent,
              sub: "resets " + (w.reset_at || "").slice(0, 16).replace("T", " ") })))
        : h("div", { className: "qr-sub" }, "No quota data"),
      usage.credits
        ? h(Balance, { label: "Credits remaining", value: usage.credits.remaining.toFixed(2) + " " + (usage.credits.unit || ""), ok: usage.credits.remaining > 0 })
        : null);
  }

  function AccountCard({ acc, busy, onValidate, onToggle, onPriorityChange }) {
    const [validating, setValidating] = useState(false);
    const [valResult, setValResult] = useState(null);
    const doValidate = () => {
      setValidating(true); setValResult(null);
      post("/accounts/" + encodeURIComponent(acc.account_ref) + "/validate", {})
        .then((r) => setValResult(r))
        .catch((e) => setValResult({ status: "failed", reason: e.message }))
        .finally(() => setValidating(false));
    };
    return h("div", { className: "qr-acct-card" },
      h("div", { className: "qr-acct-header" },
        h("span", { className: "qr-acct-label" }, acc.label),
        h("span", { className: "qr-provider-badge" }, acc.provider),
        h("span", { className: "qr-kind-badge" }, acc.kind),
        h("span", { className: statusClass(acc.status) }, acc.status || "unvalidated"),
        acc.never_touch ? h(Badge, null, "never touch") : null),
      h("div", { style: { display: "flex", alignItems: "center", gap: ".5rem" } },
        h("label", { style: { fontSize: ".78rem" } },
          h("input", { type: "checkbox", checked: !!acc.enabled, disabled: acc.never_touch || busy,
            onChange: (e) => onToggle(acc.account_ref, e.target.checked) }),
          " enabled"),
        h("label", { style: { fontSize: ".78rem" } }, "Priority ",
          h("select", { className: "qr-select", value: String(acc.priority || 0), disabled: acc.never_touch || busy,
            onChange: (e) => onPriorityChange(acc.account_ref, +e.target.value) },
            Array.from({ length: 10 }, (_, i) => h("option", { key: i, value: String(i) }, i))))),
      h(AccountUsageInline, { account_ref: acc.account_ref }),
      h("div", { style: { display: "flex", gap: ".5rem", alignItems: "center", flexWrap: "wrap" } },
        h(Button, { size: "sm", disabled: busy || validating, onClick: doValidate }, validating ? "Validating…" : "Validate"),
        acc.last_validated_at ? h("span", { className: "qr-sub" }, "Last: " + acc.last_validated_at.slice(0, 16).replace("T", " ")) : null),
      valResult ? h("div", { className: "qr-dryrun-result" },
        h("b", { className: statusClass(valResult.status) }, valResult.status), " — ", valResult.reason || "",
        valResult.plan_label ? h("span", { className: "qr-sub" }, " · " + valResult.plan_label) : null) : null,
      acc.notes ? h("div", { className: "qr-sub" }, acc.notes) : null);
  }

  function AddAccountForm({ busy, onAdd, onCancel }) {
    const empty = { label: "", provider: "openai-codex", kind: "subscription", account_ref: "", pool_ref: "", credential_ref: "", priority: 0, notes: "", enabled: true };
    const [form, setForm] = useState(empty);
    const [err, setErr] = useState("");
    const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
    const validate = () => {
      if (!form.label.trim() || form.label.length > 120) return "Label required, max 120 chars";
      for (const f of ["account_ref", "pool_ref", "credential_ref"]) {
        if (!SAFE_RE.test(form[f])) return f + " must be a safe reference handle";
      }
      if (form.priority < 0 || form.priority > 9) return "Priority 0–9";
      if (form.notes.length > 500) return "Notes max 500 chars";
      return "";
    };
    const submit = () => {
      const e = validate();
      if (e) { setErr(e); return; }
      setErr("");
      post("/accounts", { ...form, priority: +form.priority })
        .then(() => { setForm(empty); onAdd(); })
        .catch((e) => setErr(e.message));
    };
    const field = (id, label, type, value, onChange, placeholder, extra) =>
      h("div", { className: "qr-form-row" },
        h("label", { htmlFor: id }, label),
        h("input", { id, type: type || "text", value, onChange, placeholder, disabled: busy, ...(extra || {}) }));
    return h("div", { className: "qr-add-form" },
      h("div", { style: { fontWeight: 600, fontSize: ".9rem" } }, "Add account"),
      h("div", { className: "qr-sub" }, "Verified included allowances only · purchased credits and auto-recharge disabled"),
      field("f-label", "Label *", "text", form.label, (e) => set("label", e.target.value), "e.g. Personal Claude Pro", { maxLength: 120 }),
      h("div", { className: "qr-form-row" },
        h("label", { htmlFor: "f-provider" }, "Provider *"),
        h("select", { id: "f-provider", value: form.provider, disabled: busy, onChange: (e) => set("provider", e.target.value) },
          PROVIDERS.map((p) => h("option", { key: p, value: p }, p)))),
      h("div", { className: "qr-form-row" },
        h("label", { htmlFor: "f-kind" }, "Kind *"),
        h("select", { id: "f-kind", value: form.kind, disabled: busy, onChange: (e) => set("kind", e.target.value) },
          ["subscription", "credits"].map((k) => h("option", { key: k, value: k }, k)))),
      field("f-aref", "Account ref *", "text", form.account_ref, (e) => set("account_ref", e.target.value), "e.g. claude-pro-personal"),
      field("f-pool", "Pool ref *", "text", form.pool_ref, (e) => set("pool_ref", e.target.value), "e.g. anthropic-personal"),
      field("f-cred", "Credential handle *", "text", form.credential_ref, (e) => set("credential_ref", e.target.value), "Opaque handle from Hermes auth store · no tokens"),
      h("div", { className: "qr-form-row" },
        h("label", { htmlFor: "f-pri" }, "Priority (0 = highest)"),
        h("input", { id: "f-pri", type: "number", min: 0, max: 9, value: form.priority, disabled: busy, onChange: (e) => set("priority", +e.target.value) })),
      h("div", { className: "qr-form-row" },
        h("label", { htmlFor: "f-notes" }, "Notes"),
        h("textarea", { id: "f-notes", rows: 2, maxLength: 500, value: form.notes, disabled: busy, onChange: (e) => set("notes", e.target.value) })),
      h("div", { className: "qr-form-row" },
        h("label", null,
          h("input", { type: "checkbox", checked: form.enabled, disabled: busy, onChange: (e) => set("enabled", e.target.checked) }),
          " Enabled")),
      err ? h("div", { className: "qr-sub", style: { color: "#dc2626" } }, err) : null,
      h("div", { className: "qr-form-actions" },
        h(Button, { size: "sm", disabled: busy, onClick: submit }, "Add account"),
        h(Button, { size: "sm", variant: "outline", disabled: busy, onClick: onCancel }, "Cancel")));
  }

  function AccountsPanel() {
    const [accounts, setAccounts] = useState(null);
    const [showAdd, setShowAdd] = useState(false);
    const [busy, setBusy] = useState(false);
    const [msg, setMsg] = useState("");
    const [dryProfile, setDryProfile] = useState("");
    const [dryClass, setDryClass] = useState("coding");
    const [dryResult, setDryResult] = useState(null);

    const load = useCallback(() =>
      SDK.fetchJSON(API + "/accounts").then((r) => setAccounts(r.accounts || [])).catch((e) => setMsg(e.message)), []);

    useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);

    const act = (path, body, after) => {
      setBusy(true); setMsg("");
      post(path, body).then(() => { if (after) after(); load(); })
        .catch((e) => setMsg(e.message)).finally(() => setBusy(false));
    };

    const dryrun = () => {
      if (!dryProfile) { setMsg("Select a profile first"); return; }
      setBusy(true); setDryResult(null); setMsg("");
      post("/priorities/dryrun", { profile: dryProfile, task_class: dryClass })
        .then(setDryResult).catch((e) => setMsg(e.message)).finally(() => setBusy(false));
    };

    const sorted = accounts ? [...accounts].sort((a, b) => (a.priority || 0) - (b.priority || 0) || a.account_ref.localeCompare(b.account_ref)) : [];

    return h(Card, null,
      h(CardHeader, null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: ".5rem" } },
          h(CardTitle, null, "Accounts"),
          h(Button, { size: "sm", onClick: () => setShowAdd((v) => !v), disabled: busy }, showAdd ? "Cancel" : "Add account")),
        h("div", { className: "qr-sub" }, "Verified included allowances only · purchased credits and auto-recharge disabled")),
      h(CardContent, null,
        msg ? h("div", { className: "qr-sub", style: { color: "#dc2626", marginBottom: ".5rem" } }, msg) : null,
        showAdd ? h(AddAccountForm, { busy, onAdd: () => { setShowAdd(false); load(); }, onCancel: () => setShowAdd(false) }) : null,
        h("div", { style: { marginTop: ".75rem", marginBottom: ".5rem" } },
          h("div", { style: { fontWeight: 600, fontSize: ".82rem", marginBottom: ".35rem" } }, "Selection preview"),
          h("div", { style: { display: "flex", gap: ".5rem", alignItems: "center", flexWrap: "wrap" } },
            h("input", { className: "qr-filter", placeholder: "Profile name", value: dryProfile, onChange: (e) => setDryProfile(e.target.value), disabled: busy }),
            h("select", { className: "qr-select", value: dryClass, disabled: busy, onChange: (e) => setDryClass(e.target.value) },
              TASK_CLASSES.map((c) => h("option", { key: c, value: c }, c))),
            h(Button, { size: "sm", disabled: busy || !dryProfile, onClick: dryrun }, "Preview")),
          dryResult ? h("div", { className: "qr-dryrun-result" },
            h("div", null, h("b", null, dryResult.would_select || "none — would defer"), " — ", dryResult.reason),
            dryResult.eligible && dryResult.eligible.length
              ? h("div", { className: "qr-sub" }, "Eligible: " + dryResult.eligible.join(", ")) : null,
            dryResult.ineligible && dryResult.ineligible.length
              ? h("div", { className: "qr-sub" }, "Ineligible: " + dryResult.ineligible.map((x) => x.account_ref + " (" + x.reason + ")").join("; ")) : null)
            : null),
        !accounts ? h("div", { className: "qr-sub" }, "Loading…") :
        accounts.length === 0 ? h("div", { className: "qr-sub" }, "No accounts registered. Add one above.") :
        h("div", { className: "qr-acct-cards" },
          sorted.map((acc) => h(AccountCard, { key: acc.account_ref, acc, busy,
            onValidate: (ref) => act("/accounts/" + encodeURIComponent(ref) + "/validate", {}),
            onToggle: (ref, en) => act("/accounts/" + encodeURIComponent(ref) + "/enable", { enabled: en }),
            onPriorityChange: (ref, p) => act("/accounts/" + encodeURIComponent(ref) + "/priority", { priority: p }) })))));
  }

  // ── Switching history ─────────────────────────────────────────────────────

  const EVT_COLOURS = { proposed: "#6b7280", config_applied: "#2563eb", runtime_verified: "#16a34a", refused: "#dc2626", deferred: "#d97706", manual: "#3b82f6" };

  function SwitchingHistoryPanel() {
    const [range, setRange] = useState("7d");
    const [filterAcct, setFilterAcct] = useState("");
    const [filterProfile, setFilterProfile] = useState("");
    const [events, setEvents] = useState(null);
    const [accounts, setAccounts] = useState([]);
    const [err, setErr] = useState("");

    const load = useCallback(() => {
      const q = "?range=" + range + (filterAcct ? "&account_ref=" + encodeURIComponent(filterAcct) : "") + (filterProfile ? "&profile=" + encodeURIComponent(filterProfile) : "");
      Promise.all([
        SDK.fetchJSON(API + "/history" + q).then((r) => setEvents(r.events || [])),
        SDK.fetchJSON(API + "/accounts").then((r) => setAccounts(r.accounts || [])),
      ]).catch((e) => setErr(e.message));
    }, [range, filterAcct, filterProfile]);

    useEffect(() => { load(); }, [load]);

    function SwimlaneChart({ evts }) {
      if (!evts || !evts.length) return null;
      const providers = [...new Set(evts.map((e) => e.provider).filter(Boolean))];
      if (!providers.length) return null;
      const times = evts.map((e) => new Date(e.ts).getTime()).filter(Boolean);
      const t0 = Math.min(...times), t1 = Math.max(...times, t0 + 1);
      const W = 600, laneH = 22, pad = 4;
      const lanes = providers.map((prov, li) => {
        const y = li * (laneH + pad) + 2;
        const relevant = evts.filter((e) => e.provider === prov && e.ts);
        const rects = relevant.map((e, i) => {
          const x = Math.round(((new Date(e.ts).getTime() - t0) / (t1 - t0)) * (W - 60)) + 45;
          const col = EVT_COLOURS[e.event_type] || "#9ca3af";
          return h("rect", { key: i, x, y: y + 2, width: 8, height: laneH - 4, fill: col, rx: 2,
            title: (e.to_account || e.from_account || "") + " · " + (e.event_type || "") + " · " + (e.ts || "").slice(0, 16) });
        });
        return h("g", { key: prov },
          h("text", { x: 2, y: y + laneH / 2 + 4, fontSize: 9, fill: "var(--color-muted-foreground)" }, prov),
          h("line", { x1: 44, y1: y + laneH / 2, x2: W, y2: y + laneH / 2, stroke: "var(--color-border)", strokeWidth: 1 }),
          ...rects);
      });
      const svgH = providers.length * (laneH + pad) + 8;
      return h("div", { className: "qr-swimlane-chart" },
        h("svg", { width: W, height: svgH, style: { display: "block", maxWidth: "100%" } }, ...lanes));
    }

    return h(Card, null,
      h(CardHeader, null,
        h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center", gap: ".75rem", flexWrap: "wrap" } },
          h(CardTitle, null, "Switching history"),
          h("div", { className: "qr-spend-range" },
            ["24h","7d","30d"].map((r) => h("button", { key: r, className: r === range ? "qr-active" : "", onClick: () => setRange(r) }, r)))),
        h("div", { style: { display: "flex", gap: ".5rem", flexWrap: "wrap", marginTop: ".4rem" } },
          h("select", { className: "qr-select", value: filterAcct, onChange: (e) => setFilterAcct(e.target.value) },
            h("option", { value: "" }, "All accounts"),
            accounts.map((a) => h("option", { key: a.account_ref, value: a.account_ref }, a.label || a.account_ref))),
          h("input", { className: "qr-filter", placeholder: "Profile filter", value: filterProfile, onChange: (e) => setFilterProfile(e.target.value) }))),
      h(CardContent, null,
        err ? h("div", { className: "qr-sub" }, err) : null,
        !events ? h("div", { className: "qr-sub" }, "Loading…") :
        events.length === 0 ? h("div", { className: "qr-sub" }, "No switching events recorded for this period.") :
        h("div", null,
          h(SwimlaneChart, { evts: events }),
          h("div", { className: "qr-history-disclaimer" }, "Segments represent recorded switching events only. Gaps indicate periods with no recorded event — not continuous use."),
          h("div", { className: "qr-history-list", style: { marginTop: ".75rem" } },
            events.map((e, i) => h("div", { key: i, className: "qr-history-event qr-evt-" + (e.event_type || "proposed") },
              h("div", { style: { minWidth: "80px" } },
                h("div", { className: "qr-mono qr-sub" }, (e.ts || "").slice(0, 16).replace("T", " ")),
                h("span", { className: "qr-evt-pill qr-pill-" + (e.event_type || "proposed") }, e.event_type || "?")),
              h("div", { style: { flex: 1 } },
                h("div", { className: "qr-mono" }, [e.from_account, e.to_account].filter(Boolean).join(" → ")),
                h("div", { className: "qr-sub" }, [e.provider, e.model].filter(Boolean).join("/")),
                h("div", { className: "qr-sub" }, e.reason || ""),
                e.profile ? h("div", { className: "qr-sub" }, "profile: " + e.profile) : null,
                e.actor ? h("div", { className: "qr-sub" }, "actor: " + e.actor) : null)))))));
  }

  function QuotaRouterPage() {
    const [data, setData] = useState(null);
    const [dec, setDec] = useState([]);
    const [busy, setBusy] = useState(false);
    const [msg, setMsg] = useState("");
    const [filter, setFilter] = useState("");

    const load = useCallback(() => Promise.all([
      SDK.fetchJSON(API + "/overview").then(setData),
      SDK.fetchJSON(API + "/decisions?n=40").then((r) => setDec(r.items || [])),
    ]).catch((e) => setMsg("Load failed: " + e.message)), []);

    useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t); }, [load]);

    const act = (path, body) => {
      setBusy(true); setMsg("");
      return post(path, body)
        .then((r) => { if (r && r.lines) setMsg(r.lines.length ? r.lines.join("\n") : "Router pass done — no changes."); else setMsg("Done."); })
        .catch((e) => setMsg("Error: " + e.message))
        .then(load).finally(() => setBusy(false));
    };

    if (!data) return h("div", { className: "qr-sub" }, msg || "Loading…");
    const enforce = data.mode === "enforce";
    const routes = data.routes.filter((r) => !filter || r.scope.includes(filter) || r["class"].includes(filter));
    const off = data.routes.filter((r) => r.rung || r.hold || r.drift).length;

    return h("div", { className: "qr-root" },
      h("div", { className: "qr-top" },
        h("div", null, h("h2", { className: "qr-title" }, "Quota Router"),
          h("div", { className: "qr-sub" }, data.routes.length + " routes · " + off + " off-default/held/drift")),
        h("div", { className: "qr-top-actions" },
          h(Badge, { className: enforce ? "qr-enforce" : "qr-dry" }, enforce ? "ENFORCE" : "DRY-RUN"),
          h(Button, { size: "sm", variant: "outline", disabled: busy,
            onClick: () => { const m = enforce ? "dry_run" : "enforce"; if (window.confirm("Switch router to " + m + "?")) act("/mode", { mode: m }); } },
            enforce ? "Switch to dry-run" : "Switch to enforce"),
          h(Button, { size: "sm", disabled: busy, onClick: () => act("/run", {}) }, busy ? "Working…" : "Run now"))),
      msg ? h("pre", { className: "qr-msg" }, msg) : null,
      h(Quotas, { q: data.quota || {} }),
      h(Card, null,
        h(CardHeader, null, h(CardTitle, null, "Routes"),
          h(Input, { placeholder: "filter (bot, class…)", value: filter, onChange: (e) => setFilter(e.target.value), className: "qr-filter" })),
        h(CardContent, null,
          h("div", { className: "qr-scroll" },
            h("table", { className: "qr-table" },
              h("thead", null, h("tr", null, ["Scope", "Class", "Router target", "Live setting", "Status", "Actions"].map((x) => h("th", { key: x }, x)))),
              h("tbody", null, routes.map((r) => h(RouteRow, { key: r.scope, r, kinds: data.kinds, busy, act }))))))),
      h(SpendPanel, null),
      h(AccountsPanel, null),
      h(SwitchingHistoryPanel, null),
      h(Thresholds, { t: data.thresholds, busy, act }),
      h(Decisions, { items: dec }));
  }

  window.__HERMES_PLUGINS__.register("quota-router", QuotaRouterPage);
})();
