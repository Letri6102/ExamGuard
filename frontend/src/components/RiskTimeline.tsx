import { fmtSec } from "../api";

type Pt = { t: number; risk: number; s: number };
type FlagSpan = { id: number; start_t: number; end_t: number; status: string };
type Ev = { t: number; kind: string };

/** Đường điểm rủi ro theo thời gian + vùng cờ + vạch sự kiện. Bấm vào đồ thị để chọn thời điểm. */
export default function RiskTimeline({ points, flags, events, threshold, onPick }: {
  points: Pt[]; flags: FlagSpan[]; events: Ev[]; threshold: number; onPick?: (t: number) => void;
}) {
  const W = 1000, H = 160, P = 24;
  const tMax = Math.max(60, ...points.map((p) => p.t), ...events.map((e) => e.t));
  const x = (t: number) => P + (t / tMax) * (W - 2 * P);
  const y = (r: number) => H - P - (r / 100) * (H - 2 * P);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.risk).toFixed(1)}`).join(" ");
  const ticks = Array.from({ length: 6 }, (_, i) => (tMax * i) / 5);
  const evColor = (k: string) => (k.startsWith("tab") || k.startsWith("window") ? "var(--warn)" : k.includes("blocked") ? "var(--bad)" : "var(--muted)");
  return (
    <svg className="timeline-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label="Điểm rủi ro theo thời gian"
      onClick={(e) => {
        if (!onPick) return;
        const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
        onPick(Math.max(0, ((e.clientX - r.left) / r.width * W - P) / (W - 2 * P) * tMax));
      }}>
      {flags.map((f) => (
        <rect key={f.id} x={x(f.start_t)} y={P} width={Math.max(3, x(f.end_t) - x(f.start_t))} height={H - 2 * P}
          fill={f.status === "dismissed" ? "var(--line)" : "var(--bad)"} opacity={0.18}><title>Cờ #{f.id} ({f.status})</title></rect>
      ))}
      <line x1={P} x2={W - P} y1={y(threshold)} y2={y(threshold)} stroke="var(--bad)" strokeDasharray="4 4" strokeWidth={1} />
      {ticks.map((t) => (
        <g key={t}><line x1={x(t)} x2={x(t)} y1={H - P} y2={H - P + 4} stroke="var(--muted)" />
          <text x={x(t)} y={H - 6} fontSize={11} textAnchor="middle" fill="var(--muted)">{fmtSec(t)}</text></g>
      ))}
      {events.filter((e) => e.kind !== "q_view").map((e, i) => (
        <line key={i} x1={x(e.t)} x2={x(e.t)} y1={P} y2={P + 8} stroke={evColor(e.kind)} strokeWidth={2}><title>{e.kind} @ {fmtSec(e.t)}</title></line>
      ))}
      <path d={line} fill="none" stroke="var(--brand)" strokeWidth={2} vectorEffect="non-scaling-stroke" />
      <text x={P} y={14} fontSize={11} fill="var(--muted)">Điểm rủi ro (0–100) · đường đứt: ngưỡng gắn cờ</text>
    </svg>
  );
}
