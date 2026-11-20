import { useEffect, useMemo, useRef, useState } from "react";
import { Bot, Box, ChevronLeft, ChevronRight, FileText, Gavel, Pause, Play, RotateCcw, SquareTerminal, TriangleAlert } from "lucide-react";
import { api, GraphNode, RunData, TimelineEvent, fmt, shortTask } from "./api";
import { Gauge, Verdict } from "./charts";

const W = 760, H = 360;
type P = { x: number; y: number };

function layout(nodes: GraphNode[]): Record<string, P> {
  const pos: Record<string, P> = {};
  const roles = nodes.filter((n) => n.kind === "role");
  const models = nodes.filter((n) => n.kind === "model");
  roles.forEach((n, i) => (pos[n.id] = { x: 310, y: H / 2 + (i - (roles.length - 1) / 2) * 104 }));
  models.forEach((n, i) => (pos[n.id] = { x: 560 + i * 24, y: 72 + i * 56 }));
  for (const n of nodes) {
    if (n.kind === "issue") pos[n.id] = { x: 84, y: H / 2 };
    if (n.kind === "sandbox") pos[n.id] = { x: 560, y: H - 72 };
    if (n.kind === "judge") pos[n.id] = { x: 690, y: H / 2 };
  }
  return pos;
}

function curve(a: P, b: P) {
  const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
  const dx = b.x - a.x, dy = b.y - a.y;
  const c = { x: mx - dy * 0.12, y: my + dx * 0.12 };
  return { c, d: `M ${a.x} ${a.y} Q ${c.x} ${c.y} ${b.x} ${b.y}` };
}

function along(a: P, b: P, t: number): P {
  const { c } = curve(a, b);
  const u = 1 - t;
  return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y };
}

type Particle = { id: number; from: string; to: string; born: number; color: string; r: number };
const TRAVEL_MS = 760;
const ease = (t: number) => 1 - Math.pow(1 - t, 3);

function nodeLabel(n: GraphNode) {
  if (n.kind === "issue") return "Задача";
  if (n.kind === "sandbox") return "Песочница";
  if (n.kind === "judge") return "Судья";
  if (n.kind === "model") return n.id.replace("model:", "");
  return n.id === "agent" ? "Агент" : n.id;
}

export function Run({ exp, task, attempt }: { exp: string; task: string; attempt: string }) {
  const [d, setD] = useState<RunData | null>(null);
  const [now, setNow] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [tab, setTab] = useState<"issue" | "patch" | "ws" | "verdict">("issue");
  const [pinned, setPinned] = useState<number | null>(null);
  const [particles, setParticles] = useState<Particle[]>([]);
  const [wall, setWall] = useState(0);
  const pid = useRef(0);
  const nowRef = useRef(0);

  useEffect(() => {
    api.run(exp, task, attempt).then((r) => {
      setD(r);
      const dur = r.timeline.duration || 1;
      setSpeed(dur > 300 ? 16 : dur > 60 ? 4 : 1);
      setPlaying(true);
    });
  }, [exp, task, attempt]);

  const events = d?.timeline.events ?? [];
  const duration = d?.timeline.duration || 1;
  const pos = useMemo(() => layout(d?.timeline.nodes ?? []), [d]);

  useEffect(() => {
    if (!playing || !d) return;
    let raf = 0;
    let last = performance.now();
    const tick = (ts: number) => {
      const dt = (ts - last) / 1000;
      last = ts;
      const prev = nowRef.current;
      const next = Math.min(duration, prev + dt * speed);
      const born: Particle[] = [];
      for (const e of events) {
        const end = e.t + e.dur;
        const spawn = (from: string, to: string, color: string, r: number) =>
          born.push({ id: pid.current++, from, to, born: ts, color, r });
        if (e.t > prev && e.t <= next) {
          if (e.kind === "llm") spawn(e.from, e.to, "var(--text-2)", 3 + Math.min(4, (e.tokens_in ?? 0) / 4000));
          if (e.kind === "exec") spawn(e.from, e.to, "var(--text)", 3);
          if (e.kind === "exit") spawn(e.from, e.to, e.resolved ? "var(--pos)" : "var(--neg)", 5);
        }
        if (end > prev && end <= next && e.dur > 0) {
          if (e.kind === "llm") spawn(e.to, e.from, "var(--cat-1)", 3 + Math.min(4, (e.tokens_out ?? 0) / 150));
          if (e.kind === "exec") spawn(e.to, e.from, e.returncode === 0 ? "var(--seq-5)" : "var(--accent)", 3);
        }
      }
      nowRef.current = next;
      setNow(next);
      setWall(ts);
      setParticles((ps) => [...ps.filter((p) => ts - p.born < TRAVEL_MS), ...born]);
      if (next >= duration) setPlaying(false);
      else raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, d]);

  const seek = (t: number) => {
    nowRef.current = t;
    setNow(t);
    setParticles([]);
    setPinned(null);
  };

  if (!d) return <div className="empty-state">Загрузка прогона</div>;
  const res = d.result;
  const currentIdx = pinned ?? Math.max(0, events.reduce((k, e, i) => (e.t <= now ? i : k), 0));
  const cur: TimelineEvent | undefined = events[currentIdx];
  const done = events.filter((e) => e.t <= now);
  const edgeLoad: Record<string, number> = {};
  for (const e of done) {
    const k = `${e.from}>${e.to}`;
    edgeLoad[k] = (edgeLoad[k] ?? 0) + (e.kind === "llm" ? (e.tokens_in ?? 0) + (e.tokens_out ?? 0) : 400);
  }
  const active = new Set<string>();
  for (const e of events) if (e.t <= now && now <= e.t + Math.max(e.dur, 0.6)) { active.add(e.from); active.add(e.to); }
  const tokensSoFar = done.filter((e) => e.kind === "llm").reduce((s, e) => s + (e.tokens_in ?? 0) + (e.tokens_out ?? 0), 0);
  const callsSoFar = done.filter((e) => e.kind === "llm").length;
  const finished = now >= duration;

  return (
    <div className="fade-in">
      <div className="crumbs">
        <a href="#/">Лаборатория</a> <span>/</span> <a href={`#/exp/${encodeURIComponent(exp)}`}>{exp}</a> <span>/</span> <span>{shortTask(task)} · {attempt}</span>
      </div>
      <div className="page-head" style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 24 }}>
        <div>
          <h1>{shortTask(task)}</h1>
          <div className="sub mono" style={{ fontSize: 13 }}>{task} · {res.model} · {res.exit_status?.split(":")[0]}</div>
        </div>
        <div style={{ display: "flex", gap: 24, alignItems: "center" }}>
          <Gauge used={res.usage?.requests ?? 0} cap={40} unit="выз." />
          <span className="mono" style={{ color: "var(--text-2)", fontSize: 13 }}>{fmt.k(res.usage?.tokens ?? 0)} ток. · {fmt.secs(res.solve_seconds ?? 0)}</span>
          <Verdict ok={res.resolved} />
        </div>
      </div>

      <div className="run-grid">
        <div className="card swarm">
          <div className="swarm-head">
            <span className="title">Схема прогона</span>
            <span className="mono" style={{ color: "var(--text-3)", fontSize: 12 }}>
              {callsSoFar} вызовов · {fmt.k(tokensSoFar)} токенов на {fmt.time(now)}
            </span>
          </div>
          <svg className="graph" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Схема ролей и сообщений прогона">
            {d.timeline.edges.map((e) => {
              const a = pos[e.from], b = pos[e.to];
              if (!a || !b) return null;
              const load = (edgeLoad[`${e.from}>${e.to}`] ?? 0) + (edgeLoad[`${e.to}>${e.from}`] ?? 0);
              const w = 1 + Math.min(7, Math.log10(1 + load) * 1.4);
              const hot = active.has(e.from) && active.has(e.to);
              return (
                <path key={`${e.from}-${e.to}`} d={curve(a, b).d} fill="none"
                  stroke={hot ? "rgba(237,237,237,0.32)" : "rgba(237,237,237,0.12)"} strokeWidth={w} strokeLinecap="round"
                  style={{ transition: "stroke 200ms var(--ease)" }} />
              );
            })}
            {particles.map((p) => {
              const a = pos[p.from], b = pos[p.to];
              if (!a || !b) return null;
              const t = ease(Math.min(1, (wall - p.born) / TRAVEL_MS));
              const q = along(a, b, t);
              return <circle key={p.id} cx={q.x} cy={q.y} r={p.r} fill={p.color} opacity={1 - Math.max(0, t - 0.85) * 4} />;
            })}
            {d.timeline.nodes.map((n) => {
              const p = pos[n.id];
              if (!p) return null;
              const on = active.has(n.id);
              if (n.kind === "role") {
                const share = Math.min(1, callsSoFar / 40);
                const R = 34, C = 2 * Math.PI * (R + 7);
                return (
                  <g key={n.id}>
                    <circle cx={p.x} cy={p.y} r={R + 7} fill="none" stroke="var(--raised)" strokeWidth={3} />
                    <circle cx={p.x} cy={p.y} r={R + 7} fill="none" stroke={share >= 1 ? "var(--accent)" : "var(--text-2)"} strokeWidth={3}
                      strokeDasharray={`${C * share} ${C}`} transform={`rotate(-90 ${p.x} ${p.y})`} strokeLinecap="round" />
                    <circle cx={p.x} cy={p.y} r={R} fill="var(--raised)" stroke={on ? "var(--accent)" : "var(--line-strong)"} strokeWidth={1.5}
                      style={{ transition: "stroke 160ms var(--ease)" }} />
                    <Bot x={p.x - 11} y={p.y - 15} width={22} height={22} strokeWidth={1.5} color="var(--text)" />
                    <text x={p.x} y={p.y + 21} textAnchor="middle" className="node-sub">{nodeLabel(n)}</text>
                    <text x={p.x} y={p.y + R + 28} textAnchor="middle" className="node-sub">бюджет {callsSoFar}/40 выз.</text>
                  </g>
                );
              }
              const label = nodeLabel(n);
              const w = Math.max(116, label.length * 7.4 + 48), h = 44;
              const Icon = n.kind === "issue" ? FileText : n.kind === "sandbox" ? SquareTerminal : n.kind === "judge" ? Gavel : Box;
              const judgeColor = n.kind === "judge" && finished ? (res.resolved ? "var(--pos)" : "var(--neg)") : undefined;
              return (
                <g key={n.id}>
                  <rect x={p.x - w / 2} y={p.y - h / 2} width={w} height={h} rx={10} fill="var(--card)"
                    stroke={judgeColor ?? (on ? "var(--accent)" : "var(--line-strong)")} strokeWidth={1.5} style={{ transition: "stroke 160ms var(--ease)" }} />
                  <Icon x={p.x - w / 2 + 14} y={p.y - 8} width={16} height={16} strokeWidth={1.5} color="var(--text-2)" />
                  <text x={p.x - w / 2 + 38} y={p.y + 5} className="node-label" style={{ fontSize: 13 }}>{label}</text>
                  {on && <circle cx={p.x + w / 2 - 12} cy={p.y - h / 2 + 12} r={3} fill="var(--accent)" />}
                </g>
              );
            })}
          </svg>
          <div className="controls">
            <button className="btn primary" onClick={() => { if (finished) seek(0); setPlaying(!playing || finished); }} aria-label={playing ? "Пауза" : "Пуск"}>
              {playing ? <Pause size={16} strokeWidth={1.5} /> : finished ? <RotateCcw size={16} strokeWidth={1.5} /> : <Play size={16} strokeWidth={1.5} />}
            </button>
            <span className="clock">{fmt.time(now)} / {fmt.time(duration)}</span>
            <div className="scrub" onClick={(ev) => {
              const box = ev.currentTarget.getBoundingClientRect();
              seek(((ev.clientX - box.left) / box.width) * duration);
            }}>
              <div style={{ position: "absolute", left: 0, right: 0, top: 13, height: 2, background: "var(--raised)", borderRadius: 1 }} />
              <div style={{ position: "absolute", left: 0, width: `${(now / duration) * 100}%`, top: 13, height: 2, background: "var(--text-2)", borderRadius: 1 }} />
              {events.filter((e) => e.kind !== "llm").map((e, i) => (
                <div key={i} style={{ position: "absolute", left: `${(e.t / duration) * 100}%`, top: 9, width: 1, height: 10,
                  background: e.kind === "format_error" ? "var(--accent)" : e.kind === "exit" ? "var(--text)" : "var(--text-3)" }} />
              ))}
              <div className="playhead" style={{ left: `${(now / duration) * 100}%` }} />
            </div>
            <div className="seg">
              {[1, 4, 16].map((s) => (
                <button key={s} className={speed === s ? "on" : ""} onClick={() => setSpeed(s)}>×{s}</button>
              ))}
            </div>
          </div>
          <div className="lanes">
            {[["модель", "llm"], ["команды", "exec"]].map(([name, kind]) => (
              <div className="lane" key={kind}>
                <span className="name">{name}</span>
                <div className="track">
                  {events.map((e, i) => e.kind === kind && (
                    <div key={i} className="blk" title={kind === "exec" ? e.command : `${e.tokens_in} → ${e.tokens_out} ток.`}
                      onClick={() => { seek(e.t); setPinned(i); setPlaying(false); }}
                      style={{ left: `${(e.t / duration) * 100}%`, width: `${Math.max(0.25, (e.dur / duration) * 100)}%`, cursor: "pointer",
                        background: i === currentIdx ? "var(--accent)" : kind === "llm" ? "var(--seq-3)" : e.returncode === 0 ? "var(--text-3)" : "#6b5a3c" }} />
                  ))}
                  <div className="playhead" style={{ left: `${(now / duration) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card detail">
          {cur ? <StepDetail e={cur} index={currentIdx} total={events.length} /> : <div className="empty-state">Нет событий</div>}
          <div className="steps-nav">
            <button className="btn" onClick={() => { const i = Math.max(0, currentIdx - 1); seek(events[i].t); setPinned(i); setPlaying(false); }}>
              <ChevronLeft size={16} strokeWidth={1.5} /> Назад
            </button>
            <button className="btn" onClick={() => { const i = Math.min(events.length - 1, currentIdx + 1); seek(events[i].t); setPinned(i); setPlaying(false); }}>
              Вперёд <ChevronRight size={16} strokeWidth={1.5} />
            </button>
          </div>
        </div>
      </div>

      <div className="section card">
        <div className="tabs" role="tablist">
          {([["issue", "Задача"], ["patch", "Отправленный патч"], ["ws", "Рабочая копия"], ["verdict", "Вердикт судьи"]] as const).map(([k, l]) => (
            <button key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{l}</button>
          ))}
        </div>
        {tab === "issue" && <div className="issue-text">{d.issue}</div>}
        {tab === "patch" && <Diff text={d.patch} empty="Агент ничего не отправил" />}
        {tab === "ws" && <Diff text={d.workspace_diff} empty="Рабочая копия не изменилась" />}
        {tab === "verdict" && (
          <div>
            <Verdict ok={res.resolved} />
            {res.verdict && !res.verdict.applied && <div className="sub">Патч не применился</div>}
            {res.verdict && res.verdict.f2p_failed.length > 0 && (
              <div className="block" style={{ marginTop: 16 }}>
                <div className="sub" style={{ marginBottom: 8 }}>Не прошли тесты, которые должны были начать проходить ({res.verdict.f2p_failed.length})</div>
                <pre className="code">{res.verdict.f2p_failed.join("\n")}</pre>
              </div>
            )}
            {res.verdict && res.verdict.p2p_failed.length > 0 && (
              <div className="block" style={{ marginTop: 16 }}>
                <div className="sub" style={{ marginBottom: 8 }}>Сломались тесты, которые проходили ({res.verdict.p2p_failed.length})</div>
                <pre className="code">{res.verdict.p2p_failed.join("\n")}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function StepDetail({ e, index, total }: { e: TimelineEvent; index: number; total: number }) {
  const head = {
    llm: { icon: <Bot size={16} strokeWidth={1.5} />, title: "Вызов модели" },
    exec: { icon: <SquareTerminal size={16} strokeWidth={1.5} />, title: "Команда в песочнице" },
    format_error: { icon: <TriangleAlert size={16} strokeWidth={1.5} />, title: "Ошибка формата ответа" },
    exit: { icon: <Gavel size={16} strokeWidth={1.5} />, title: "Остановка" },
    message: { icon: <Bot size={16} strokeWidth={1.5} />, title: "Сообщение" },
  }[e.kind];
  return (
    <div>
      <h3>{head.icon}{head.title}</h3>
      <div className="meta">шаг {index + 1} из {total} · {fmt.time(e.t)}{e.dur ? ` · ${e.dur.toFixed(1)} с` : ""}</div>
      {e.kind === "llm" && (
        <>
          <div className="block">
            <div className="label">Токены</div>
            <div className="mono" style={{ fontSize: 13 }}>{fmt.int(e.tokens_in ?? 0)} на входе → {fmt.int(e.tokens_out ?? 0)} на выходе</div>
          </div>
          <div className="block">
            <div className="label">Рассуждение</div>
            <div className="thought">{e.text || "—"}</div>
          </div>
        </>
      )}
      {e.kind === "exec" && (
        <>
          <div className="block">
            <div className="label">Команда</div>
            <pre className="code">{e.command}</pre>
          </div>
          <div className="block">
            <div className="label">Вывод · код {e.returncode ?? "—"}</div>
            <pre className="code">{e.output || "(пусто)"}</pre>
          </div>
        </>
      )}
      {e.kind === "format_error" && <div className="block thought">Модель ответила без блока команды, агент вернул ей подсказку по формату</div>}
      {e.kind === "exit" && <div className="block thought">{e.status}</div>}
    </div>
  );
}

function Diff({ text, empty }: { text: string; empty: string }) {
  if (!text.trim()) return <div className="empty-state">{empty}</div>;
  return (
    <div className="diff">
      {text.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : l.startsWith("@@") ? "hunk" : ""}>{l || " "}</div>
      ))}
    </div>
  );
}
