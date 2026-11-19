// Data contract v1 returned by GET /api/threads/{id}/analysis
// (same shape the dashboard's built-in engine produces). Times are epoch ms.
export type Tier = "high" | "medium" | "organic";
export type SignalKey = "temporal" | "linguistic" | "narrative" | "network";

export interface Span { s: number; e: number; type: "trope" | "calque" | "dialect" | "ngram"; note: string }
export interface Item {
  c: { id: string; user: string; ts: number | null; text: string; source_text?: string | null; platform?: string; thread_id?: string;
       simulated?: boolean; source?: string; url?: string; account?: Record<string, unknown> | null; infra?: Infra | null };
  t: number | null; score: number; tier: Tier; renormalised: boolean;
  temporal: number | null; linguistic: number | null; narrative: number | null; network: number | null;
  tReasons: string[]; lReasons: string[]; nReasons: string[]; gReasons: string[];
  tropes: { key: string; label: string; hits: number; semantic?: number | null }[];
  dups: { id: string; user: string; sim: number }[];
  spans: Span[]; dialect: { geg: number; std: number; profile: string };
}
export interface Infra { node: string; type: string; asn?: string; ip?: string; city?: string; cc?: string; lat?: number; lon?: number }
export interface Edge { a: string; b: string; type: "friends" | "content" | "infra" | "coactivity"; w: number }
export interface Analysis {
  contract: "1";
  items: Item[]; edges: Edge[];
  clusters: { id: string; members: string[]; size: number; avgScore: number; createdSpan: [number, number] | null; sharedFriends: number;
              medianFollowers: number | null; narratives: [string, number][]; edgeTypes: string[] }[];
  scoreByUser: Record<string, number>; users: Record<string, unknown>;
  infra: (Infra & { ips: string[]; users: string[]; comments: number; avgRisk: number; flagged: number; hostile: boolean;
                    first: number | null; last: number | null; vectors: [string, number][]; meta?: Record<string, string> | null })[];
  thread: { n: number; high: number; medium: number; organic: number; pct: number; level: 1 | 2 | 3 | 4; levelName: string;
            published: number | null; expected: number; largestCluster: number;
            peak: { start: number; end: number; count: number; coordinated: number } | null;
            narratives: [label: string, count: number, key: string, severity: "STRATEGIC" | "OPERATIONAL"][];
            signalAvg: Record<SignalKey, { flagged: number | null; organic: number | null }> };
  meta: { thread: { id: string; platform?: string; title?: string; url?: string; simulated?: number }; generated: number; embedder: "sbert" | "tfidf" };
}
export interface LiveMessage { type: "hello" | "analysis"; thread_id?: string; level?: number; n?: number;
  alert?: { thread_id: string; level: number; delivered: boolean; dry_run: boolean; exercise: boolean } | null; threads?: unknown[] }

// Minimal React hook (Vite / Next.js client component)
// import { useEffect, useState } from "react";
// export function useLiveThread(threadId: string, apiKey = "") {
//   const [data, setData] = useState<Analysis | null>(null);
//   useEffect(() => {
//     const load = () => fetch(`/api/threads/${encodeURIComponent(threadId)}/analysis`).then(r => r.json()).then(setData);
//     load();
//     const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live?key=${encodeURIComponent(apiKey)}`);
//     ws.onmessage = (ev) => { const m: LiveMessage = JSON.parse(ev.data); if (m.type === "analysis" && m.thread_id === threadId) load(); };
//     return () => ws.close();
//   }, [threadId, apiKey]);
//   return data;
// }
