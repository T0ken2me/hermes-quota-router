#!/usr/bin/env python3
"""Render the Quota Router dashboard plugin with synthetic data -> docs/images/dashboard.png.

index.js and style.css are the repo's real files. Only the Hermes SDK and the
API responses are synthetic (SYNTHETIC fixtures below). The browser is blocked
from all network; React/ReactDOM UMD (pinned 18.3.1) are fetched once with urllib.
Exit code is nonzero if any check fails.
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_JS = ROOT / "dashboard" / "dist" / "index.js"
PLUGIN_CSS = ROOT / "dashboard" / "dist" / "style.css"
OUT = ROOT / "docs" / "images" / "dashboard.png"
CAPTION = "Synthetic demonstration — no live accounts or provider calls"
CDN = "https://cdn.jsdelivr.net/npm/{pkg}@18.3.1/umd/{pkg}.production.min.js"
UMD_URLS = [CDN.format(pkg="react"), CDN.format(pkg="react-dom")]

# SYNTHETIC fixture responses, keyed by API path (query string stripped).
# Shapes mirror what dashboard/dist/index.js reads.
FIXTURES = {
    "/overview": {
        "mode": "dry_run",
        "kinds": {"anthropic": "subscription", "openai-codex": "subscription", "nous": "credits",
                  "copilot": "flat", "deepseek": "metered", "openrouter": "metered"},
        "thresholds": {"hot_weekly": 85, "return_weekly": 60, "room_weekly": 50, "hot_session": 90,
                       "return_session": 70, "room_session": 50, "min_balance_usd": 2},
        "quota": {
            "ts": "2026-10-19 08:00 UTC",
            "anthropic": {"ok": True, "weekly": 42, "session": 18},
            "openai-codex": {"ok": True, "weekly": 63, "session": 30},
            "nous": {"ok": True, "credits": "12.50"},
            "copilot": {"ok": True, "plan": "Business (synthetic)"},
            "deepseek": {"ok": True, "balance_usd": "8.20"},
            "openrouter": {"ok": True, "balance_usd": "3.10"},
        },
        "routes": [
            {"scope": "writer.main", "class": "writer", "rung": 0, "live": ["anthropic", "claude-sonnet-4"],
             "ladder": [["anthropic", "claude-sonnet-4"], ["deepseek", "deepseek-chat"]],
             "hold": {"note": "synthetic hold for demo", "by": "fixture"}, "drift": None, "never_touch": False},
            {"scope": "coder.main", "class": "coder", "rung": 1, "live": ["anthropic", "claude-opus-4"],
             "ladder": [["openai-codex", "gpt-5-codex"], ["anthropic", "claude-opus-4"], ["openrouter", "qwen-coder"]],
             "hold": None, "drift": None, "never_touch": False},
        ],
    },
    "/decisions": {"items": [
        {"ts": "2026-10-19T07:40:00Z", "mode": "dry_run", "scope": "coder.main",
         "from": ["openai-codex", "gpt-5-codex"], "to": ["anthropic", "claude-opus-4"], "metered": False,
         "reason": "synthetic: earlier Codex weekly 90% reached hot threshold", "source": "fixture"},
        {"ts": "2026-10-19T06:10:00Z", "event": "hold", "scope": "writer.main",
         "reason": "synthetic hold for demo", "source": "fixture"},
        {"ts": "2026-10-19T05:00:00Z", "event": "thresholds", "values": {"hot_weekly": 85},
         "reason": "synthetic", "source": "fixture"},
    ]},
    "/spend/summary": {"coverage": {"profiles_partial": 1, "pricing_unknown_rows": 0}},
    "/spend/timeseries": {"points": [
        {"date": f"2026-10-{d:02d}", "estimated_cost_usd": c}
        for d, c in zip(range(13, 20), [0.52, 0.88, 0.41, 0.97, 0.64, 0.73, 0.71])]},
    "/spend/profiles": {"profiles": [
        {"profile": "developer", "estimated_cost_usd": 2.91, "input_tokens": 980000, "output_tokens": 210000,
         "total_tokens": 1190000, "source_status": "ok"},
        {"profile": "writer", "estimated_cost_usd": 1.95, "input_tokens": 420000, "output_tokens": 60000,
         "total_tokens": 480000, "source_status": "ok"},
        {"profile": "coder", "estimated_cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
         "total_tokens": 0, "source_status": "unavailable"},
    ]},
    "/spend/models": {"models": [
        {"model": "claude-sonnet-4", "estimated_cost_usd": 2.41, "input_tokens": 900000,
         "output_tokens": 180000, "sessions": 12},
        {"model": "gpt-5-codex", "estimated_cost_usd": 2.45, "input_tokens": 500000,
         "output_tokens": 90000, "sessions": 7},
    ]},
    "/spend/sessions": {"sessions": [
        {"profile": "developer", "session_id_short": "syn-7f3a", "models": ["claude-sonnet-4"],
         "date": "2026-10-18", "input_tokens": 210000, "output_tokens": 41000, "estimated_cost_usd": 0.61},
    ]},
}

# Derive displayed totals so the synthetic tables remain internally consistent.
for key in ("estimated_cost_usd", "input_tokens", "output_tokens", "total_tokens"):
    FIXTURES["/spend/summary"][key] = round(sum(p[key] for p in FIXTURES["/spend/profiles"]["profiles"]), 2)
assert round(sum(p["estimated_cost_usd"] for p in FIXTURES["/spend/models"]["models"]), 2) == FIXTURES["/spend/summary"]["estimated_cost_usd"]
assert round(sum(p["estimated_cost_usd"] for p in FIXTURES["/spend/timeseries"]["points"]), 2) == FIXTURES["/spend/summary"]["estimated_cost_usd"]

# Minimal SDK stand-in: hooks/components are thin HTML wrappers; fetchJSON serves FIXTURES and rejects non-GET.
SDK_JS = """
(function () {
  const React = window.React, FIX = __FIXTURES__;
  const h = React.createElement;
  const cls = (...c) => c.filter(Boolean).join(" ");
  const wrap = (tag, base) => function ({ className, children, ...rest }) {
    return h(tag, { ...rest, className: cls(base, className) }, children);
  };
  function Button({ variant, size, className, children, ...rest }) {
    return h("button", { type: "button", ...rest,
      className: cls("demo-btn", variant && "demo-btn-" + variant, size && "demo-btn-" + size, className) }, children);
  }
  const Input = ({ className, ...rest }) => h("input", { ...rest, className: cls("demo-input", className) });
  const components = {
    Card: wrap("section", "demo-card"), CardHeader: wrap("header", "demo-card-header"),
    CardTitle: wrap("h3", "demo-card-title"), CardContent: wrap("div", "demo-card-content"),
    Badge: wrap("span", "demo-badge"), Button, Input,
  };
  const fetchJSON = (url, opts = {}) => {
    const method = (opts.method || "GET").toUpperCase();
    if (method !== "GET") return Promise.reject(new Error("synthetic demo: " + method + " is disabled"));
    const key = url.split("?")[0].replace("/api/plugins/quota-router", "");
    if (!(key in FIX)) return Promise.reject(new Error("no synthetic fixture for " + key));
    return Promise.resolve(JSON.parse(JSON.stringify(FIX[key])));
  };
  window.__HERMES_PLUGINS__ = { register(name, comp) { window.__registered = { name, comp }; } };
  window.__HERMES_PLUGIN_SDK__ = {
    React, fetchJSON, components,
    hooks: { useState: React.useState, useEffect: React.useEffect, useCallback: React.useCallback },
  };
})();
""".replace("__FIXTURES__", json.dumps(FIXTURES))

BASE_CSS = """
body { margin: 0; padding: 24px; background: #f5f6f8; color: #111; font: 14px -apple-system, system-ui, sans-serif; }
.demo-caption { margin: 0 0 16px; padding: 8px 12px; background: #fff7d6; border: 1px solid #e6c84d;
  border-radius: 6px; font-weight: 600; }
.demo-card { background: #fff; border: 1px solid #dcdfe4; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.demo-card-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.demo-card-title { margin: 0 0 8px; font-size: 16px; }
.demo-badge { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #eceef2; font-size: 12px; }
.demo-btn { padding: 4px 10px; border: 1px solid #b8bec8; border-radius: 6px; background: #fff; cursor: pointer; }
.demo-btn:disabled { opacity: .5; cursor: default; }
.demo-input { padding: 4px 8px; border: 1px solid #b8bec8; border-radius: 6px; }
"""

PAGE_HTML = f"""<!doctype html><html><head><meta charset="utf-8"></head>
<body><p class="demo-caption">{CAPTION}</p><div id="root"></div></body></html>"""


def fetch_umd(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def main():
    umd = [fetch_umd(u) for u in UMD_URLS]
    plugin_js = PLUGIN_JS.read_text(encoding="utf-8")
    plugin_css = PLUGIN_CSS.read_text(encoding="utf-8")
    results, errors, browser = [], [], None

    def check(name, ok):
        results.append((name, bool(ok)))
        print(("PASS " if ok else "FAIL ") + name)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 1100})
            ctx.route("**/*", lambda route: route.abort())  # no network at all
            page = ctx.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.set_content(PAGE_HTML)
            page.add_style_tag(content=BASE_CSS)
            for script in umd:
                page.add_script_tag(content=script)
            page.add_script_tag(content=SDK_JS)
            page.add_script_tag(content=plugin_js)
            page.add_style_tag(content=plugin_css)
            page.evaluate("() => ReactDOM.createRoot(document.getElementById('root'))"
                          ".render(React.createElement(window.__registered.comp))")

            expect(page.get_by_role("heading", name="Quota Router", exact=True)).to_be_visible(timeout=10000)
            expect(page.get_by_text("Per profile")).to_be_visible()
            check("heading 'Quota Router'", page.get_by_role("heading", name="Quota Router", exact=True).is_visible())
            check("heading 'Routes'", page.get_by_role("heading", name="Routes", exact=True).is_visible())
            check("heading 'Spend'", page.get_by_role("heading", name="Spend", exact=True).is_visible())
            check("badge 'DRY-RUN'", page.get_by_text("DRY-RUN", exact=True).is_visible())

            rows = page.locator("table:has(th:text-is('Router target')) tbody tr")
            check("two route rows before filter", rows.count() == 2)
            page.get_by_placeholder("filter (bot, class…)").fill("coder")
            expect(rows).to_have_count(1)
            check("filter 'coder' leaves one route row", rows.count() == 1 and "coder.main" in rows.inner_text())
            page.get_by_placeholder("filter (bot, class…)").fill("")
            expect(rows).to_have_count(2)
            check("filter reset restores two route rows", rows.count() == 2)

            rejected = page.evaluate("() => window.__HERMES_PLUGIN_SDK__.fetchJSON("
                                     "'/api/plugins/quota-router/run', { method: 'POST' })"
                                     ".then(() => false, () => true)")
            check("non-GET fetchJSON is rejected (no mutation)", rejected)
            check("no page errors", not errors)

            OUT.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(OUT), full_page=True)
            check("screenshot written", OUT.is_file() and OUT.stat().st_size > 0)
    except Exception as exc:  # report and fail; the with-block closes the browser
        print(f"ERROR {type(exc).__name__}: {exc}")
        check("run completed", False)

    for err in errors:
        print("page error: " + err)
    failed = [name for name, ok in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if not failed:
        print(f"screenshot: {OUT}")
    print("data: SYNTHETIC fixtures only; no live accounts, provider calls, config, or database access")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
