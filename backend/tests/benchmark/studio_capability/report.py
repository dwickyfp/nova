"""Write the benchmark report (Indonesian) and a compact HTML summary.

    cd backend && NOVA_BENCH_OUT=... uv run python -m tests.benchmark.studio_capability.report

Reads ``turns/`` and ``judgments/`` from the results directory and writes
``REPORT.md`` and ``summary.html`` there (the HTML is self-contained and kept
under 500 KB). Nothing is written into the repository.
"""

from __future__ import annotations

import argparse
import html
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tests.benchmark.studio_capability import settings
from tests.benchmark.studio_capability.judge import DIMENSIONS

LABEL_ID = {"accuracy": "Akurasi", "intelligence": "Kecerdasan", "ui": "UI",
            "process": "Proses/latensi", "safety": "Keamanan/tata kelola"}

#: Capability rows of the matrix: label -> categories (single and session kinds).
CAPABILITIES: dict[str, set[str]] = {
    "Angka tunggal": {"number"},
    "Tabel / rincian": {"table", "breakdown"},
    "Grafik batang": {"chart_bar"},
    "Grafik garis": {"chart_line"},
    "Grafik area": {"chart_area"},
    "Grafik pie/donut": {"chart_pie"},
    "Grafik scatter": {"chart_scatter"},
    "Grafik pilihan agen": {"chart_auto"},
    "Komposisi multi-komponen": {"chart_composite"},
    "Kartu KPI": {"kpi"},
    "Peta": {"map"},
    "Perbandingan": {"comparison"},
    "Tren": {"trend"},
    "Peringkat": {"ranking"},
    "Diagnosis (diagnose_change)": {"diagnosis", "session:why"},
    "Anomali": {"anomaly"},
    "What-if / simulasi": {"what_if", "session:what_if"},
    "Narasi / ringkasan": {"narrative"},
    "Forecast (tanpa alat)": {"forecast"},
    "Analisis multi-langkah": {"multi_step", "session:multi_step"},
    "Tanggal relatif": {"date_relative", "session:date_relative"},
    "Salah ketik / bahasa santai": {"typo"},
    "Ambigu → HITL (question/choice/form)": {"ambiguous", "session:hitl"},
    "Penolakan akses / masking / row filter": {"governance", "session:governance"},
    "Penolakan SQL non-read-only": {"write_refusal"},
    "Di luar cakupan data": {"out_of_scope", "session:limitation"},
    "Otomasi (tanpa kartu persetujuan)": {"automation", "session:action"},
    "Ganti role (tanpa kartu persetujuan)": {"role_switch"},
    "Simpan ke Dataviz": {"dataviz_save"},
    "Tentang agen": {"agent_meta"},
    "Perintah / (create-skill)": {"slash"},
    "Deep research": {"deep_research"},
    "Stop / retry": {"stop", "session:stop_retry"},
    "Bahasa (ID/EN, ganti bahasa)": {"language", "session:language"},
    "Follow-up & konteks sesi": {"session:follow_up", "session:topic_switch", "session:long"},
    "Koreksi di tengah sesi": {"session:correction"},
    "Ubah grafik & simpan dari sesi": {"session:chart_edit"},
}


def _load(out: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((out / "turns").glob("*.json")):
        record = json.loads(path.read_text())
        record.pop("events", None)
        judgment_path = out / "judgments" / f"{record['id']}.json"
        record["judgment"] = json.loads(judgment_path.read_text()) if judgment_path.is_file() \
            else None
        screenshot = out / "screens" / f"{record['id']}.png"
        record["screenshot"] = str(screenshot) if screenshot.is_file() else None
        rows.append(record)
    return rows


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 2) if values else None


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


def _fmt(value: Any) -> str:
    return "–" if value is None else (f"{value:.2f}" if isinstance(value, float) else str(value))


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    judged = [r for r in rows if r["judgment"]]
    by_category: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for record in judged:
        for dim in DIMENSIONS:
            by_category[record["unit"]["category"]][dim].append(record["judgment"]["final"][dim])
    overall = {d: _mean([r["judgment"]["final"][d] for r in judged]) for d in DIMENSIONS}
    latency = {key: [r["checks"]["latency"][key] for r in rows
                     if r["checks"]["latency"].get(key) is not None]
               for key in ("first_activity_s", "first_answer_token_s", "total_s")}
    tags: Counter[str] = Counter()
    for record in judged:
        tags.update(record["judgment"]["llm"].get("tags") or [])
        checks = record["checks"]
        if checks["leaked_values"]:
            tags["leak"] += 1
        if checks.get("approval_card_shown"):
            tags["approval_card_shown"] += 1
        if checks["outcome"] == "error":
            tags["error"] += 1
    failures = sorted(judged, key=lambda r: (min(r["judgment"]["final"].values()),
                                             statistics.mean(r["judgment"]["final"].values())))
    pairs = []
    index = {r["id"]: r for r in judged}
    for record in judged:
        if record["unit"]["unit"] == "fresh_twin" and record["unit"]["twin_of"] in index:
            pairs.append((index[record["unit"]["twin_of"]], record))
    matrix = []
    for label, categories in CAPABILITIES.items():
        members = [r for r in judged if r["unit"]["category"] in categories]
        if not members:
            matrix.append({"capability": label, "n": 0, "status": "tidak diuji"})
            continue
        floor = statistics.mean(min(r["judgment"]["final"][d] for d in ("accuracy", "intelligence",
                                                                         "ui")) for r in members)
        ok_rate = sum(r["checks"]["outcome_ok"] for r in members) / len(members)
        status = ("berfungsi" if floor >= 4 and ok_rate >= 0.8
                  else "lemah" if floor >= 2.5 else "rusak")
        matrix.append({"capability": label, "n": len(members), "status": status,
                       "floor": round(floor, 2), "outcome_ok": round(ok_rate, 2),
                       **{d: _mean([r["judgment"]["final"][d] for r in members])
                          for d in DIMENSIONS}})
    return {
        "turns": len(rows), "judged": len(judged), "overall": overall,
        "by_category": {c: {d: _mean(v) for d, v in dims.items()} | {"n": len(dims["accuracy"])}
                        for c, dims in sorted(by_category.items())},
        "latency": {k: {"p50": _pct(v, 0.5), "p90": _pct(v, 0.9), "max": _pct(v, 1.0),
                        "n": len(v)} for k, v in latency.items()},
        "latency_raw": latency,
        "tags": tags.most_common(),
        "failures": failures[:20],
        "pairs": pairs,
        "matrix": matrix,
        "replay_ok": _mean([float(r["replay"]["ok"]) for r in rows if r.get("replay")]),
        "language_ok": _mean([float(r["checks"]["language_ok"]) for r in rows
                              if r["checks"].get("language_ok") is not None]),
        "judge_models": sorted({r["judgment"]["judge_model"] for r in judged}),
        "vision_share": _mean([float(r["judgment"]["vision"]) for r in judged]),
    }


def _pair_section(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[str]:
    if not pairs:
        return ["Belum ada pasangan sesi/fresh yang dinilai.", ""]
    lines = ["| Dimensi | Dalam sesi | Sesi baru (fresh) | Selisih |", "|---|---|---|---|"]
    for dim in DIMENSIONS:
        session = _mean([a["judgment"]["final"][dim] for a, _ in pairs])
        fresh = _mean([b["judgment"]["final"][dim] for _, b in pairs])
        delta = round(session - fresh, 2) if session is not None and fresh is not None else None
        lines.append(f"| {LABEL_ID[dim]} | {_fmt(session)} | {_fmt(fresh)} | {_fmt(delta)} |")
    session_t = _mean([a["checks"]["latency"]["total_s"] or 0 for a, _ in pairs])
    fresh_t = _mean([b["checks"]["latency"]["total_s"] or 0 for _, b in pairs])
    lines += ["", f"Waktu total rata-rata: dalam sesi {_fmt(session_t)} s, sesi baru "
              f"{_fmt(fresh_t)} s ({len(pairs)} pasangan).", ""]
    worse = [(a, b) for a, b in pairs
             if sum(a["judgment"]["final"].values()) < sum(b["judgment"]["final"].values()) - 2]
    if worse:
        lines.append("Giliran yang jauh lebih buruk dalam sesi daripada sebagai pertanyaan baru:")
        for a, b in worse[:10]:
            lines.append(f"- `{a['id']}` \"{a['unit']['question']}\": sesi "
                         f"{a['judgment']['final']} vs fresh {b['judgment']['final']}")
        lines.append("")
    return lines


def write_markdown(out: Path, summary: dict[str, Any], manifest: dict[str, Any]) -> Path:
    s = summary
    lines = [
        "# Benchmark kapabilitas Nova Studio — laporan",
        "",
        f"Agen **{manifest.get('agent')}** (model `{manifest.get('model')}`), pengguna "
        f"`{manifest.get('user')}` dengan role `{manifest.get('role')}`, korpus "
        f"`{manifest.get('corpus_version')}`, API `{manifest.get('api')}`, mulai "
        f"{manifest.get('started_at')}.",
        f"Model juri: {', '.join(f'`{m}`' for m in s['judge_models']) or '–'} "
        f"(screenshot ikut dinilai pada {_fmt(s['vision_share'])} bagian giliran). "
        "Jika model juri satu keluarga dengan model yang diuji, ada risiko self-preference; "
        "kalibrasi manusia memakai `judge_sample.jsonl`.",
        f"Giliran tercatat: **{s['turns']}**, dinilai: **{s['judged']}**. "
        f"Replay setelah reload identik: {_fmt(s['replay_ok'])}. "
        f"Bahasa jawaban sesuai: {_fmt(s['language_ok'])}.",
        "",
        "## Skor keseluruhan (1–5)",
        "",
        "| " + " | ".join(LABEL_ID[d] for d in DIMENSIONS) + " |",
        "|" + "---|" * len(DIMENSIONS),
        "| " + " | ".join(_fmt(s["overall"][d]) for d in DIMENSIONS) + " |",
        "",
        "## Skor per kategori dan dimensi",
        "",
        "| Kategori | n | " + " | ".join(LABEL_ID[d] for d in DIMENSIONS) + " |",
        "|---|---|" + "---|" * len(DIMENSIONS),
    ]
    for category, row in s["by_category"].items():
        lines.append(f"| {category} | {row['n']} | "
                     + " | ".join(_fmt(row.get(d)) for d in DIMENSIONS) + " |")
    lines += ["", "## Matriks kapabilitas", "",
              "Status: **berfungsi** (skor terendah akurasi/kecerdasan/UI rata-rata ≥ 4 dan "
              "≥ 80% hasil sesuai harapan), **lemah** (≥ 2,5), **rusak** (< 2,5).", "",
              "| Kapabilitas | n | Status | " + " | ".join(LABEL_ID[d] for d in DIMENSIONS)
              + " |", "|---|---|---|" + "---|" * len(DIMENSIONS)]
    for row in s["matrix"]:
        lines.append(f"| {row['capability']} | {row['n']} | {row['status']} | "
                     + " | ".join(_fmt(row.get(d)) for d in DIMENSIONS) + " |")
    lines += ["", "## Taksonomi kegagalan", "", "| Tag | Jumlah |", "|---|---|"]
    lines += [f"| {tag} | {count} |" for tag, count in s["tags"]] or ["| – | – |"]
    lines += ["", "## 20 kegagalan teratas", ""]
    for rank, record in enumerate(s["failures"], start=1):
        judgment = record["judgment"]
        llm = judgment["llm"]
        lines += [
            f"### {rank}. `{record['id']}` ({record['unit']['category']})",
            "",
            f"- Pertanyaan: \"{record['unit']['question']}\"",
            f"- Skor: {judgment['final']}; tag: {', '.join(llm.get('tags') or []) or '–'}",
            f"- Ringkasan juri: {llm.get('summary', '–')}",
            f"- Bukti: thread `{record.get('thread_id')}`, run `{record.get('run_id')}`"
            + (f", screenshot `{record['screenshot']}`" if record.get("screenshot") else ""),
            f"- Cek deterministik: hasil `{record['checks']['outcome']}` (harapan "
            f"`{record['checks']['expected_outcome']}`), recall angka "
            f"{_fmt(record['checks']['numeric']['recall'])}, komponen "
            f"{record['checks']['elements']}",
            "",
        ]
    lines += ["## Distribusi latensi (detik)", "",
              "| Ukuran | p50 | p90 | maks | n |", "|---|---|---|---|---|"]
    names = {"first_activity_s": "Aktivitas pertama", "first_answer_token_s": "Token jawaban "
             "pertama", "total_s": "Total"}
    for key, row in s["latency"].items():
        lines.append(f"| {names[key]} | {_fmt(row['p50'])} | {_fmt(row['p90'])} | "
                     f"{_fmt(row['max'])} | {row['n']} |")
    lines += ["", "## Sesi vs sesi baru", ""] + _pair_section(s["pairs"])
    path = out / "REPORT.md"
    path.write_text("\n".join(lines) + "\n")
    return path


CSS = """
:root{--bg:#fbfaf8;--fg:#1d1d1b;--muted:#6b6a66;--line:#e4e1db;--card:#ffffff;
--good:#2f7d4f;--weak:#b7791f;--bad:#b3362f;--accent:#2b5c8a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#ecebe7;
--muted:#a29f98;--line:#2f2e2b;--card:#1e1e1c;--good:#5fb383;--weak:#d9a441;--bad:#e06a62;
--accent:#7aa7d4}}
:root[data-theme="dark"]{--bg:#161615;--fg:#ecebe7;--muted:#a29f98;--line:#2f2e2b;--card:#1e1e1c;
--good:#5fb383;--weak:#d9a441;--bad:#e06a62;--accent:#7aa7d4}
*{box-sizing:border-box}body{margin:0;background:var(--bg,#fbfaf8);color:var(--fg,#1d1d1b);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
p.meta{color:var(--muted,#6b6a66);margin:0 0 16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.tile{background:var(--card,#fff);border:1px solid var(--line,#e4e1db);border-radius:8px;
padding:10px 12px}.tile b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.tile span{color:var(--muted,#6b6a66);font-size:12px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line,#e4e1db);padding:5px 8px;text-align:left;
white-space:nowrap}th{font-weight:600;color:var(--muted,#6b6a66);font-size:12px}
td.n{text-align:right}.s5,.s4{color:var(--good,#2f7d4f)}.s3{color:var(--weak,#b7791f)}
.s2,.s1{color:var(--bad,#b3362f)}.berfungsi{color:var(--good,#2f7d4f)}
.lemah{color:var(--weak,#b7791f)}.rusak{color:var(--bad,#b3362f)}
ol li{margin-bottom:6px}code{font-size:12px}
"""


def _cell(value: Any) -> str:
    if value is None:
        return '<td class="n">–</td>'
    bucket = max(1, min(5, round(float(value))))
    return f'<td class="n s{bucket}">{value:.2f}</td>'


def write_html(out: Path, summary: dict[str, Any], manifest: dict[str, Any]) -> Path:
    s = summary
    esc = html.escape
    tiles = "".join(
        f'<div class="tile"><b>{_fmt(s["overall"][d])}</b><span>{esc(LABEL_ID[d])}</span></div>'
        for d in DIMENSIONS)
    tiles += (f'<div class="tile"><b>{s["judged"]}</b><span>giliran dinilai</span></div>'
              f'<div class="tile"><b>{_fmt(s["latency"]["first_activity_s"]["p50"])} s</b>'
              f'<span>aktivitas pertama p50</span></div>'
              f'<div class="tile"><b>{_fmt(s["latency"]["total_s"]["p50"])} s</b>'
              f'<span>total p50</span></div>')
    head = "".join(f"<th>{esc(LABEL_ID[d])}</th>" for d in DIMENSIONS)
    categories = "".join(
        f"<tr><td>{esc(c)}</td><td class='n'>{row['n']}</td>"
        + "".join(_cell(row.get(d)) for d in DIMENSIONS) + "</tr>"
        for c, row in s["by_category"].items())
    matrix = "".join(
        f"<tr><td>{esc(row['capability'])}</td><td class='n'>{row['n']}</td>"
        f"<td class='{esc(row['status'].replace(' ', '-'))}'>{esc(row['status'])}</td>"
        + "".join(_cell(row.get(d)) for d in DIMENSIONS) + "</tr>"
        for row in s["matrix"])
    failures = "".join(
        f"<li><code>{esc(r['id'])}</code> — {esc(r['unit']['question'])}<br>"
        f"<small>{esc(str(r['judgment']['final']))} · "
        f"{esc((r['judgment']['llm'].get('summary') or '')[:220])}</small></li>"
        for r in s["failures"])
    latency = "".join(
        f"<tr><td>{esc(k)}</td><td class='n'>{_fmt(v['p50'])}</td><td class='n'>"
        f"{_fmt(v['p90'])}</td><td class='n'>{_fmt(v['max'])}</td></tr>"
        for k, v in s["latency"].items())
    page = f"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Benchmark Nova Studio</title><style>{CSS}</style></head><body><main>
<h1>Benchmark kapabilitas Nova Studio</h1>
<p class="meta">{esc(str(manifest.get('agent')))} · model {esc(str(manifest.get('model')))} ·
{esc(str(manifest.get('user')))}/{esc(str(manifest.get('role')))} · korpus
{esc(str(manifest.get('corpus_version')))} · juri {esc(', '.join(s['judge_models']))}</p>
<div class="tiles">{tiles}</div>
<h2>Matriks kapabilitas</h2><div class="wrap"><table><thead><tr><th>Kapabilitas</th>
<th>n</th><th>Status</th>{head}</tr></thead><tbody>{matrix}</tbody></table></div>
<h2>Per kategori</h2><div class="wrap"><table><thead><tr><th>Kategori</th><th>n</th>{head}
</tr></thead><tbody>{categories}</tbody></table></div>
<h2>Latensi (detik)</h2><div class="wrap"><table><thead><tr><th>Ukuran</th><th>p50</th>
<th>p90</th><th>maks</th></tr></thead><tbody>{latency}</tbody></table></div>
<h2>20 kegagalan teratas</h2><ol>{failures}</ol>
</main></body></html>"""
    path = out / "summary.html"
    path.write_text(page)
    if path.stat().st_size > 500_000:
        raise SystemExit("summary.html is over 500 KB")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out")
    args = parser.parse_args()
    out = settings.out_dir(args.out)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    summary = summarize(_load(out))
    (out / "summary.json").write_text(json.dumps(
        {k: v for k, v in summary.items() if k not in {"failures", "pairs"}}, indent=1,
        default=str))
    print(write_markdown(out, summary, manifest))
    print(write_html(out, summary, manifest))


if __name__ == "__main__":
    main()
