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
      h(Thresholds, { t: data.thresholds, busy, act }),
      h(Decisions, { items: dec }));
  }

  window.__HERMES_PLUGINS__.register("quota-router", QuotaRouterPage);
})();
