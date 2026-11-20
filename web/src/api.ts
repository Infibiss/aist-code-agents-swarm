export type Summary = {
  name: string;
  model: string;
  system: Record<string, unknown>;
  budget: { tokens?: number; requests?: number; seconds?: number };
  hardware: Record<string, string>;
  question: string;
  hypothesis: string;
  decision: string;
  tasks: string;
  attempts: number;
  runs: number;
  resolved: number;
  tokens_mean: number;
  seconds_mean: number;
  statuses: Record<string, number>;
  updated: number;
};

export type Row = {
  task: string;
  attempt: number;
  resolved: boolean;
  exit_status: string;
  solve_seconds?: number;
  usage?: { tokens: number; requests: number; prompt_tokens: number; completion_tokens: number };
  extra?: { steps?: number };
};

export type ExperimentData = {
  summary: Summary;
  config: Record<string, unknown>;
  rows: Row[];
  tasks: Record<string, { repo: string; created_at: string; patch_lines: number; files: number }>;
};

export type TimelineEvent = {
  kind: "llm" | "exec" | "format_error" | "exit" | "message";
  from: string;
  to: string;
  t: number;
  dur: number;
  tokens_in?: number;
  tokens_out?: number;
  text?: string;
  finish?: string;
  command?: string;
  returncode?: number;
  output?: string;
  status?: string;
  resolved?: boolean;
};

export type GraphNode = { id: string; kind: "issue" | "role" | "model" | "sandbox" | "judge" };

export type RunData = {
  result: Row & {
    experiment: string;
    model: string;
    verdict?: { resolved: boolean; applied: boolean; f2p_failed: string[]; p2p_failed: string[]; log_tail: string };
  };
  budget: { tokens?: number; requests?: number; seconds?: number };
  issue: string;
  timeline: { nodes: GraphNode[]; edges: { from: string; to: string }[]; events: TimelineEvent[]; duration: number };
  patch: string;
  workspace_diff: string;
};

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

export const api = {
  experiments: () => get<Summary[]>("/api/experiments"),
  experiment: (name: string) => get<ExperimentData>(`/api/experiments/${encodeURIComponent(name)}`),
  run: (name: string, task: string, attempt: string) =>
    get<RunData>(`/api/runs/${encodeURIComponent(name)}/${encodeURIComponent(task)}/${attempt}`),
};

export const fmt = {
  int: (n: number) => Math.round(n).toLocaleString("ru-RU"),
  k: (n: number) =>
    n >= 1e6 ? `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M` : n >= 1000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : `${Math.round(n)}`,
  pct: (a: number, b: number) => (b ? `${Math.round((100 * a) / b)}%` : "—"),
  time: (s: number) => {
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60);
    return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
  },
  secs: (s: number) => (s >= 60 ? `${(s / 60).toFixed(1)} мин` : `${s.toFixed(1)} с`),
};

export function shortTask(id: string) {
  const [owner, rest] = id.split("__");
  return rest ? rest : owner;
}

export function family(model: string): "Qwen" | "Gemma" | "Другие" {
  const m = model.toLowerCase();
  if (m.includes("qwen")) return "Qwen";
  if (m.includes("gemma")) return "Gemma";
  return "Другие";
}
