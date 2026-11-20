import { Fragment, useEffect, useState } from "react";
import { api, ExperimentData, fmt, shortTask } from "./api";
import { Gauge, Verdict } from "./charts";

function median(xs: number[]) {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

export function Experiment({ name }: { name: string }) {
  const [d, setD] = useState<ExperimentData | null>(null);
  useEffect(() => {
    const load = () => api.experiment(name).then(setD).catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [name]);
  if (!d) return <div className="empty-state">Загрузка</div>;
  const s = d.summary;
  const sys = s.system as Record<string, unknown>;
  const reqCap = s.budget.requests;
  const statuses = Object.entries(s.statuses).sort((a, b) => b[1] - a[1]);
  const maxStatus = Math.max(1, ...statuses.map(([, n]) => n));
  const tokens = d.rows.filter((r) => r.usage).map((r) => r.usage!.tokens);
  const secs = d.rows.filter((r) => r.solve_seconds).map((r) => r.solve_seconds!);

  const q = (label: string, text: string) => (
    <div className="q-block">
      <div className="label">{label}</div>
      {text ? <div className="text">{text}</div> : <div className="empty">не задано в конфиге эксперимента</div>}
    </div>
  );

  return (
    <div className="fade-in">
      <div className="crumbs">
        <a href="#/">Лаборатория</a> <span>/</span> <span>{s.name}</span>
      </div>
      <div className="page-head">
        <h1>{s.name}</h1>
        <div className="sub mono" style={{ fontSize: 13 }}>
          {s.model} · {String(sys.type)} · {s.tasks} · {s.attempts} попыт.
        </div>
      </div>

      <div className="card card-cols">
        <div>
          {q("Вопрос", s.question)}
          {q("Гипотеза", s.hypothesis)}
          {q("Правило решения", s.decision)}
        </div>
        <dl className="kv">
          <dt>Модель</dt><dd>{s.model}</dd>
          {Object.entries(s.hardware).map(([k, v]) => (
            <Fragment key={k}><dt>{k === "gpu" ? "GPU" : k === "quant" ? "Квант" : k === "server" ? "Сервер" : k}</dt><dd>{v}</dd></Fragment>
          ))}
          {Object.entries(sys).filter(([k]) => k !== "type").map(([k, v]) => (
            <Fragment key={k}><dt>{k}</dt><dd>{String(v)}</dd></Fragment>
          ))}
          {Object.entries(s.budget).map(([k, v]) => (
            <Fragment key={k}><dt>бюджет · {k}</dt><dd>{fmt.int(v as number)}</dd></Fragment>
          ))}
        </dl>
      </div>

      <div className="section stats">
        <div className="card stat">
          <div className="label">Решено</div>
          <div className="value">{s.resolved}/{s.runs}</div>
          <div className="note">{fmt.pct(s.resolved, s.runs)}</div>
        </div>
        <div className="card stat">
          <div className="label">Токенов на попытку</div>
          <div className="value">{fmt.k(median(tokens))}</div>
          <div className="note">медиана</div>
        </div>
        <div className="card stat">
          <div className="label">Время на попытку</div>
          <div className="value">{fmt.secs(median(secs))}</div>
          <div className="note">медиана, без судьи</div>
        </div>
        <div className="card stat">
          <div className="label">Причины остановки</div>
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 6 }}>
            {statuses.map(([k, n]) => (
              <div key={k} style={{ display: "grid", gridTemplateColumns: "1fr 48px 24px", gap: 8, alignItems: "center", fontSize: 12 }}>
                <span style={{ color: "var(--text-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{k}</span>
                <span className="gauge-track" style={{ width: 48 }}><span className="gauge-fill" style={{ width: `${(n / maxStatus) * 100}%` }} /></span>
                <span className="mono" style={{ color: "var(--text-3)", textAlign: "right" }}>{n}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="section card">
        <div className="section-head">
          <h2>Попытки</h2>
          <span className="meta">клик открывает повтор прогона</span>
        </div>
        <table className="t">
          <thead>
            <tr>
              <th>Задача</th><th>Вердикт</th><th>Остановка</th><th className="n">Шаги</th><th className="n">Время</th><th>Бюджет вызовов</th><th className="n">Токены</th>
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r) => (
              <tr key={`${r.task}-${r.attempt}`} className="click" onClick={() => (window.location.hash = `#/run/${encodeURIComponent(s.name)}/${encodeURIComponent(r.task)}/a${r.attempt}`)}>
                <td title={r.task}>
                  <div>{shortTask(r.task)}</div>
                  <div className="mono" style={{ color: "var(--text-3)", fontSize: 12 }}>{d.tasks[r.task]?.repo ?? r.task}</div>
                </td>
                <td><Verdict ok={r.resolved} /></td>
                <td className="mono" style={{ color: "var(--text-2)", fontSize: 12 }}>{r.exit_status.split(":")[0]}</td>
                <td className="n">{r.extra?.steps ?? "—"}</td>
                <td className="n">{r.solve_seconds ? fmt.secs(r.solve_seconds) : "—"}</td>
                <td><Gauge used={r.usage?.requests ?? 0} cap={reqCap} unit="выз." /></td>
                <td className="n">{r.usage ? fmt.k(r.usage.tokens) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
