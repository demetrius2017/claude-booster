---
description: "Frontend debug pipeline: Chrome DevTools diagnostics, HAR analysis, performance traces."
paths: ["**/*.tsx", "**/*.jsx", "**/*.vue", "**/*.css", "**/*.scss", "**/*.html", "**/components/**", "**/pages/**", "**/app/**"]
---

# Chrome Automation
`tabs_context_mcp` first. URLs always full: `http://host:port/path`, never bare `localhost`.

# Frontend Debug Pipeline

**When:** any frontend bug, visual issue, performance problem, a11y check.

**Which MCP when:**
- **Chrome DevTools** — diagnostics (console, network, performance, Lighthouse, memory). Primary tool.
- **Claude-in-Chrome** — visual checks, auth sessions, GIF recording, NL element search (`find`).
- **Playwright** — E2E tests, cross-browser checks, visual regression.

## Step 1: API check
```
curl API endpoint → confirm backend is OK
```

## Step 2: Diagnostics via Chrome DevTools (parallel)
```
list_network_requests     → what the browser sent/received
list_console_messages     → JS errors, warnings
take_screenshot           → current visual state
```

## Step 3: Deep dive (as needed)
```
get_network_request(id)           → request/response body of a specific call
get_console_message(id)           → stack trace with source-map
evaluate_script("document...")    → check DOM/state at runtime
lighthouse_audit                  → a11y + SEO + performance score
```

## Step 4: Performance (if slow)
```
performance_start_trace(reload: true) → record trace
performance_stop_trace                → stop
performance_analyze_insight("LCPBreakdown")  → Core Web Vitals analysis
performance_analyze_insight("RenderBlocking") → blocking resources
```

## Step 5: Memory (if leaks)
```
take_memory_snapshot → .heapsnapshot file
evaluate_script("performance.memory") → current consumption
```

## Step 6: Emulation (if responsive/mobile bug)
```
emulate(device: "iPhone 15", networkCondition: "Slow 3G", cpuThrottling: 4)
take_screenshot → verify on emulated device
```

## Step 0: HAR files and local artifacts (BEFORE browser)
```
Glob("**/reports/*.har", "**/*.har")  → find existing HAR files in project
Read(har_file)                         → JSON: parse entries[].timings, entries[].response.content.size
```
- HAR = full network snapshot (timings, sizes, headers, bodies). Read via `Read` — it's JSON.
- User complains "slow" / "takes forever" / "images lag" → **FIRST** look for HAR/network data, do NOT poke UI.
- HAR analysis: sort by `time` (total), `timings.wait` (TTFB), `response.content.size` (weight). Identify bottleneck.
- No HAR? → Chrome DevTools `list_network_requests` + `get_network_request(id)` for bodies and timings.

**[CRITICAL] Performance complaint = engineering diagnostics, NOT visual poking.**
Priority: HAR/network data → Chrome DevTools metrics → Lighthouse → only then screenshots.
Do NOT click UI like a user. Look under the hood: network waterfall, response sizes, cache headers, compression.

**[CRITICAL] Collect EVIDENCE before editing code:** screenshot + console errors + network status. Without root cause evidence — do not fix.
