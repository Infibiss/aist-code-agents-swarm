import { useMemo, useState } from "react";
import { CircleCheck, CircleX } from "lucide-react";
import { ExperimentData, Summary, family, fmt, shortTask } from "./api";

export function Verdict({ ok }: { ok: boolean | undefined }) {
  return ok ? (
    <span className="verdict pass">
      <CircleCheck size={16} strokeWidth={1.5} /> решено
    </span>
  ) : (
    <span className="verdict fail">
      <CircleX size={16} strokeWidth={1.5} /> не решено
    </span>
  );
}

/** The signature: how much of the budget a run (or an average run) spent. */
export function Gauge({ used, cap, unit }: { used: number; cap?: number; unit: string }) {
  if (!cap) return <span className="gauge">{fmt.k(used)} {unit}</span>;
  const share = Math.min(1, used / cap);
  return (
    <span className={`gauge ${share >= 0.999 ? "full" : ""}`} title={`${fmt.int(used)} из ${fmt.int(cap)} ${unit}`}>
      <span className="gauge-track">
        <span className="gauge-fill" style={{ width: `${share * 100}%` }} />
      </span>
      {fmt.k(used)}/{fmt.k(cap)} {unit}
    </span>
  );
}

const FAMILY_COLOR: Record<string, string> = { Qwen: "var(--cat-1)", Gemma: "var(--cat-2)", Другие: "var(--cat-3)" };

/** Resolve rate against mean tokens per attempt, one dot per experiment (log x). */
export function Pareto({ exps, onPick }: { exps: Summary[]; onPick: (n: string) => void }) {
  const W = 720, H = 300, L = 48, R = 16, T = 16, B = 36;
  const pts = exps.filter((e) => e.runs > 0);
  const xs = pts.map((e) => Math.max(1, e.tokens_mean));
  const lo = Math.pow(10, Math.floor(Math.log10(Math.min(...xs, 1000))));
  const hi = Math.pow(10, Math.ceil(Math.log10(Math.max(...xs, 10000))));
  const x = (v: number) => L + ((Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (W - L - R);
  const y = (r: number) => T + (1 - r) * (H - T - B);
  const [hover, setHover] = useState<Summary | null>(null);
  const ticksX: number[] = [];
  for (let v = lo; v <= hi; v *= 10) ticksX.push(v);
  const fams = Array.from(new Set(pts.map((e) => family(e.model))));
  return (
    <div className="chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Доля решённых задач против среднего числа токенов на попытку">
        <g className="axis">
          {[0, 0.25, 0.5, 0.75, 1].map((r) => (
            <g key={r}>
              <line className="gridline" x1={L} x2={W - R} y1={y(r)} y2={y(r)} />
              <text x={L - 8} y={y(r) + 4} textAnchor="end">{Math.round(r * 100)}%</text>
            </g>
          ))}
          {ticksX.map((v) => (
            <text key={v} x={x(v)} y={H - 12} textAnchor="middle">{fmt.k(v)}</text>
          ))}
          <text x={W - R} y={H - 12 + 0} textAnchor="end" opacity={0}>.</text>
        </g>
        {pts.map((e) => {
          const r = e.resolved / e.runs;
          const cx = x(e.tokens_mean), cy = y(r);
          return (
            <g key={e.name} style={{ cursor: "pointer" }} onMouseEnter={() => setHover(e)} onMouseLeave={() => setHover(null)} onClick={() => onPick(e.name)}>
              <circle cx={cx} cy={cy} r={14} fill="transparent" />
              <circle cx={cx} cy={cy} r={5} fill={FAMILY_COLOR[family(e.model)]} stroke="var(--card)" strokeWidth={2} />
              {pts.length <= 6 && (
                <text x={cx + 10} y={cy - 8} className="node-sub" style={{ fill: "var(--text-2)" }}>{e.name}</text>
              )}
            </g>
          );
        })}
      </svg>
      <div className="legend">
        {fams.length > 1 && fams.map((f) => (
          <span key={f}><i style={{ background: FAMILY_COLOR[f] }} />{f}</span>
        ))}
        <span style={{ marginLeft: "auto", color: "var(--text-3)" }} className="mono">ось x: токены на попытку, лог. шкала</span>
      </div>
      {hover && (
        <div className="tip show" style={{ left: `${(x(hover.tokens_mean) / W) * 100}%`, top: `${(y(hover.resolved / hover.runs) / H) * 100}%`, transform: "translate(12px, -50%)" }}>
          <div style={{ fontWeight: 500, marginBottom: 4 }}>{hover.name}</div>
          <div><span className="k">модель </span><span className="v">{hover.model}</span></div>
          <div><span className="k">решено </span><span className="v">{hover.resolved}/{hover.runs} · {fmt.pct(hover.resolved, hover.runs)}</span></div>
          <div><span className="k">токенов в среднем </span><span className="v">{fmt.int(hover.tokens_mean)}</span></div>
          <div><span className="k">время в среднем </span><span className="v">{fmt.secs(hover.seconds_mean)}</span></div>
        </div>
      )}
    </div>
  );
}

const SEQ = ["var(--seq-1)", "var(--seq-2)", "var(--seq-3)", "var(--seq-4)", "var(--seq-5)"];

/** Tasks by experiments; a cell's shade is the share of attempts the judge accepted. */
export function Matrix({ data }: { data: ExperimentData[] }) {
  const tasks = useMemo(() => {
    const s = new Set<string>();
    data.forEach((d) => d.rows.forEach((r) => s.add(r.task)));
    return Array.from(s).sort();
  }, [data]);
  const [tip, setTip] = useState<{ x: number; y: number; text: string[] } | null>(null);
  if (!tasks.length) return <div className="empty-state">Пока нет прогонов</div>;
  return (
    <div className="matrix chart-wrap" onMouseLeave={() => setTip(null)}>
      <table>
        <thead>
          <tr>
            <th />
            {data.map((d) => (
              <th key={d.summary.name} className="col">{d.summary.name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t}>
              <td className="task" title={t}>{shortTask(t)}</td>
              {data.map((d) => {
                const rows = d.rows.filter((r) => r.task === t);
                if (!rows.length) return <td key={d.summary.name} className="cell none" />;
                const ok = rows.filter((r) => r.resolved).length;
                const share = ok / rows.length;
                const bg = ok === 0 ? undefined : SEQ[Math.min(4, Math.round(share * 4))];
                return (
                  <td
                    key={d.summary.name}
                    className={`cell ${ok === 0 ? "zero" : ""}`}
                    style={bg ? { background: bg } : undefined}
                    onMouseMove={(ev) => {
                      const box = (ev.currentTarget.closest(".matrix") as HTMLElement).getBoundingClientRect();
                      setTip({ x: ev.clientX - box.left, y: ev.clientY - box.top, text: [t, d.summary.name, `решено ${ok} из ${rows.length}`, rows.map((r) => r.exit_status.split(":")[0]).join(", ")] });
                    }}
                    onClick={() => (window.location.hash = `#/run/${encodeURIComponent(d.summary.name)}/${encodeURIComponent(t)}/a${rows[0].attempt}`)}
                  />
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="legend">
        <span><i style={{ background: "var(--raised)", borderRadius: 2 }} />0 решений</span>
        {SEQ.slice(1).map((c, i) => (
          <span key={c}><i style={{ background: c, borderRadius: 2 }} />{["25%", "50%", "75%", "100%"][i]}</span>
        ))}
        <span><i style={{ boxShadow: "inset 0 0 0 1px var(--line-strong)", borderRadius: 2 }} />не запускали</span>
      </div>
      {tip && (
        <div className="tip show" style={{ left: tip.x + 14, top: tip.y + 14 }}>
          <div style={{ fontWeight: 500 }}>{tip.text[0]}</div>
          <div className="k">{tip.text[1]}</div>
          <div className="v">{tip.text[2]}</div>
          <div className="k">{tip.text[3]}</div>
        </div>
      )}
    </div>
  );
}
