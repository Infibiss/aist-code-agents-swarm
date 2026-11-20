import { useEffect, useState } from "react";
import { FlaskConical, Network } from "lucide-react";
import { api, Summary } from "./api";
import { Lab } from "./Lab";
import { Experiment } from "./Experiment";
import { Run } from "./Run";

function useHash() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
}

function Mark() {
  // Three small nodes and one larger one: the swarm against the single model.
  return (
    <svg className="brand-mark" viewBox="0 0 20 20" aria-hidden>
      <circle cx="4" cy="5" r="2.2" fill="#a1a1a6" />
      <circle cx="4" cy="15" r="2.2" fill="#a1a1a6" />
      <circle cx="9" cy="10" r="2.2" fill="#a1a1a6" />
      <circle cx="15.5" cy="10" r="3.6" fill="#e8a33d" />
    </svg>
  );
}

export function App() {
  const route = useHash();
  const [exps, setExps] = useState<Summary[]>([]);
  useEffect(() => {
    const load = () => api.experiments().then(setExps).catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, []);

  const [page, a, b, c] = route;
  let body;
  if (page === "exp" && a) body = <Experiment name={a} />;
  else if (page === "run" && a && b && c) body = <Run exp={a} task={b} attempt={c} />;
  else body = <Lab exps={exps} />;

  const activeExp = page === "exp" || page === "run" ? a : undefined;
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <Mark />
          aist-swarm
        </div>
        <div className="side-label">Разделы</div>
        <nav className="nav">
          <a href="#/" className={!page ? "on" : ""}>
            <FlaskConical size={16} strokeWidth={1.5} />
            Лаборатория
            <span className="count">{exps.length}</span>
          </a>
          <a href={activeExp && page === "run" ? window.location.hash : "#/"} className={page === "run" ? "on" : ""}>
            <Network size={16} strokeWidth={1.5} />
            Рой
          </a>
        </nav>
        <div className="side-label">Эксперименты</div>
        <nav className="nav">
          {exps.map((e) => (
            <a key={e.name} href={`#/exp/${encodeURIComponent(e.name)}`} className={activeExp === e.name ? "on" : ""}>
              <span className="exp-name">{e.name}</span>
              <span className="count">
                {e.resolved}/{e.runs}
              </span>
            </a>
          ))}
        </nav>
      </aside>
      <main className="main" key={route.join("/")}>
        {body}
      </main>
    </div>
  );
}
