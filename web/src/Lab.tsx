import { useEffect, useState } from "react";
import { api, ExperimentData, Summary, fmt } from "./api";
import { Gauge, Matrix, Pareto } from "./charts";

export function Lab({ exps }: { exps: Summary[] }) {
  const [details, setDetails] = useState<ExperimentData[]>([]);
  const key = exps.map((e) => `${e.name}:${e.runs}`).join(",");
  useEffect(() => {
    Promise.all(exps.map((e) => api.experiment(e.name))).then(setDetails).catch(() => {});
  }, [key]);

  const runs = exps.reduce((s, e) => s + e.runs, 0);
  const resolved = exps.reduce((s, e) => s + e.resolved, 0);
  const models = new Set(exps.map((e) => e.model));
  const real = exps.filter((e) => e.model !== "fake");
  const best = real.length ? real.reduce((a, b) => (b.resolved / Math.max(1, b.runs) > a.resolved / Math.max(1, a.runs) ? b : a)) : undefined;

  return (
    <div className="fade-in">
      <div className="page-head">
        <h1>Лаборатория</h1>
        <div className="sub">
          Рой малых локальных моделей против одной большой на реальных багах Python. Каждая попытка идёт в чистой песочнице без сети,
          решение принимает судья по скрытым тестам, бюджет считает прокси.
        </div>
      </div>

      <div className="stats">
        <div className="card stat">
          <div className="label">Эксперименты</div>
          <div className="value">{exps.length}</div>
          <div className="note">{models.size} моделей</div>
        </div>
        <div className="card stat">
          <div className="label">Попытки</div>
          <div className="value">{fmt.int(runs)}</div>
          <div className="note">по всем экспериментам</div>
        </div>
        <div className="card stat">
          <div className="label">Решено судьёй</div>
          <div className="value">{fmt.int(resolved)}</div>
          <div className="note">{fmt.pct(resolved, runs)} попыток</div>
        </div>
        <div className="card stat">
          <div className="label">Лучший без оракула</div>
          <div className="value">{best ? fmt.pct(best.resolved, best.runs) : "—"}</div>
          <div className="note">{best ? best.name : "нет прогонов"}</div>
        </div>
      </div>

      <div className="section grid-main">
        <div className="card">
          <div className="section-head">
            <h2>Качество против бюджета</h2>
            <span className="meta">точка = эксперимент</span>
          </div>
          <Pareto exps={exps} onPick={(n) => (window.location.hash = `#/exp/${encodeURIComponent(n)}`)} />
        </div>
        <div className="card">
          <div className="section-head">
            <h2>Последние эксперименты</h2>
          </div>
          <div className="list">
            {exps.slice(0, 8).map((e) => (
              <a key={e.name} className="list-row" href={`#/exp/${encodeURIComponent(e.name)}`}>
                <span className="title">{e.name}</span>
                <span className="right">{e.resolved}/{e.runs}</span>
                <span className="meta">
                  <span>{e.model}</span>
                  <Gauge used={e.tokens_mean} cap={e.budget.tokens} unit="ток." />
                </span>
              </a>
            ))}
          </div>
        </div>
      </div>

      <div className="section card">
        <div className="section-head">
          <h2>Задачи × эксперименты</h2>
          <span className="meta">клик по ячейке открывает прогон</span>
        </div>
        <Matrix data={details} />
      </div>
    </div>
  );
}
