import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { money, type OwnerMonth, type OwnerYear as Year } from "../../api";
import { DetailCard } from "../PageKit";

// EL AÑO DEL DUEÑO (FASE 16, 31-oct, pedido de Jose): los meses cerrados como UNA colección acumulada del año, no
// un reporte mes a mes, con gráficos para que el dueño lea mejor sus datos. Todo número viene del backend (SQL
// sobre las fotos de los meses cerrados: `month_close.owner_years`); aquí solo se dibuja.
// Diseño según la guía de gráficos (skill dataviz): una sola escala, líneas de 2px, barras ≤ 24px con la punta
// redondeada, rejilla de 1px, leyenda + etiquetas directas (la identidad nunca es solo el color), tooltip al pasar
// el mouse y una TABLA con los mismos datos. Paleta validada (verde / azul, pasa las pruebas de daltonismo); la app
// no tiene modo oscuro.

const IN = "#059669";   // entró — verde (el mismo del Resumen)
const OUT = "#2a78d6";  // salió — azul
const GRID = "#e2e8f0";

const compact = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  return [1, 2, 2.5, 5, 10].map((k) => k * p).find((x) => x >= v) ?? 10 * p;
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

function monthShort(lang: string, i: number) {
  return new Date(2026, i, 1).toLocaleDateString(lang, { month: "short" }).replace(".", "");
}

export default function OwnerYearView({ years, closed }: { years: Year[]; closed: OwnerMonth[] }) {
  const { t, i18n } = useTranslation();
  const lang = i18n.language || "en";
  const [pick, setPick] = useState(years[0]?.year);
  const [table, setTable] = useState(false);
  useEffect(() => { if (!years.some((y) => y.year === pick)) setPick(years[0]?.year); }, [years, pick]);
  const y = years.find((x) => x.year === pick) ?? years[0];
  if (!y) return null;
  const n = y.months.length;

  return (
    <div className="space-y-3">
      {/* Las cifras del año — sin utilidad ni diferencia: no es un P&L (Jose, 31-oct). */}
      <div data-tour="pp-year" className="bg-white rounded-xl border border-slate-200 px-3 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-2">
        {years.length > 1 ? (
          <select value={y.year} onChange={(e) => setPick(Number(e.target.value))} aria-label={t("owner.year.pick")}
                  className="text-sm font-semibold rounded-lg border border-slate-200 px-2 py-1">
            {years.map((x) => <option key={x.year} value={x.year}>{x.year}</option>)}
          </select>
        ) : (
          <span className="text-sm font-semibold text-slate-900">{y.year}</span>
        )}
        <Figure label={t("owner.summary.in")} value={money(Number(y.total_in))} swatch={IN} />
        <Figure label={t("owner.summary.out")} value={money(Number(y.total_out))} swatch={OUT} />
        <span className="text-xs text-slate-500">{t("owner.year.closedMonths", { count: n })} · {t("owner.summary.meta")}</span>
      </div>

      <DetailCard>
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <h3 className="text-sm font-semibold text-slate-900">{t("owner.year.cumulative")}</h3>
          <Legend />
        </div>
        <CumulativeChart months={y.months} lang={lang} />
      </DetailCard>

      <div data-tour="pp-categories" className="grid gap-3 lg:grid-cols-[3fr_2fr]">
        <DetailCard>
          <h3 className="text-sm font-semibold text-slate-900">{t("owner.year.whereOut")}</h3>
          <p className="text-xs text-slate-500 mb-2">{t("owner.year.whereOutSub", { year: y.year })}</p>
          <Bars rows={y.out_by_category} color={OUT} />
        </DetailCard>
        <DetailCard>
          <h3 className="text-sm font-semibold text-slate-900">{t("owner.year.whereIn")}</h3>
          <p className="text-xs text-slate-500 mb-2">{t("owner.year.whereInSub", { year: y.year })}</p>
          <Bars rows={y.in_by_category} color={IN} />
        </DetailCard>
      </div>

      {y.pending.count > 0 && (
        <p className="text-xs text-amber-800 bg-amber-50 rounded-lg px-2.5 py-1.5">
          {t("owner.summary.pending", { count: y.pending.count, in: money(Number(y.pending.money_in)), out: money(Number(y.pending.money_out)) })}
        </p>
      )}
      <p className="text-[11px] text-slate-400">{t("owner.summary.excludes")}</p>

      <div>
        <button onClick={() => setTable((v) => !v)} className="text-xs font-medium text-brand-green hover:underline">
          {table ? t("owner.year.hideTable") : t("owner.year.showTable")}
        </button>
        {table && <YearTable year={y} closed={closed.filter((m) => m.month.startsWith(`${y.year}-`))} lang={lang} />}
      </div>
    </div>
  );
}

function Figure({ label, value, swatch }: { label: string; value: string; swatch: string }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="inline-block w-3 h-0.5 rounded self-center" style={{ background: swatch }} />
      <span className="text-xs text-slate-500">{label}</span>
      <span className="text-lg font-semibold text-slate-900">{value}</span>
    </div>
  );
}

function Legend() {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-3 text-xs text-slate-600">
      {[[IN, t("owner.summary.in")], [OUT, t("owner.summary.out")]].map(([c, l]) => (
        <span key={l} className="flex items-center gap-1.5"><span className="inline-block w-4 h-0.5 rounded" style={{ background: c }} />{l}</span>
      ))}
    </div>
  );
}

/** El acumulado del año: dos líneas (entró / salió) sobre los 12 meses, hasta el último mes cerrado. El acumulado
 *  ya viene calculado por SQL (`cum_in` / `cum_out`). Pasar el mouse muestra el mes y el acumulado. */
function CumulativeChart({ months, lang }: { months: Year["months"]; lang: string }) {
  const { t } = useTranslation();
  const [ref, W] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const H = 220, L = 52, R = 84, T = 12, B = 24;
  const pw = Math.max(0, W - L - R), ph = H - T - B;
  const idx = (ym: string) => Number(ym.slice(5, 7)) - 1;
  const max = niceMax(Math.max(0, ...months.flatMap((m) => [Number(m.cum_in), Number(m.cum_out)])));
  // El eje empieza en el PRIMER mes cerrado (Jose, 1-nov: enero a junio vacíos no dicen nada) y llega a diciembre.
  const first = months.length ? Math.min(...months.map((m) => idx(m.month))) : 0;
  const span = Math.max(1, 11 - first);
  const x = (i: number) => L + (pw * (i - first)) / span;
  const yv = (v: number) => T + ph - (ph * Math.max(0, v)) / max;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((k) => k * max);
  const path = (key: "cum_in" | "cum_out") =>
    months.map((m, i) => `${i ? "L" : "M"}${x(idx(m.month)).toFixed(1)},${yv(Number(m[key])).toFixed(1)}`).join(" ");
  const last = months[months.length - 1];
  const hv = hover != null ? months[hover] : null;
  // Etiquetas al final de cada línea; si quedan encima una de otra, se separan lo justo (la leyenda manda igual).
  let yIn = last ? yv(Number(last.cum_in)) : 0, yOut = last ? yv(Number(last.cum_out)) : 0;
  if (Math.abs(yIn - yOut) < 26) { const mid = (yIn + yOut) / 2; const up = yIn <= yOut; yIn = mid + (up ? -13 : 13); yOut = mid + (up ? 13 : -13); }

  return (
    <div ref={ref} className="relative mt-2">
      {W > 0 && (
        <svg width={W} height={H} role="img" aria-label={t("owner.year.cumulative")}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={L} x2={L + pw} y1={yv(v)} y2={yv(v)} stroke={GRID} strokeWidth={1} />
              <text x={L - 6} y={yv(v) + 4} textAnchor="end" className="fill-slate-500 text-[11px] tabular-nums">{compact(v)}</text>
            </g>
          ))}
          {Array.from({ length: 12 - first }, (_, k) => first + k).map((i) => (
            <text key={i} x={x(i)} y={H - 6} textAnchor="middle" className="fill-slate-500 text-[11px]">{monthShort(lang, i)}</text>
          ))}
          {hv && <line x1={x(idx(hv.month))} x2={x(idx(hv.month))} y1={T} y2={T + ph} stroke="#94a3b8" strokeWidth={1} />}
          {(["cum_out", "cum_in"] as const).map((k) => (
            <path key={k} d={path(k)} fill="none" stroke={k === "cum_in" ? IN : OUT} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {months.map((m, i) => (["cum_out", "cum_in"] as const).map((k) => (
            <circle key={`${k}${i}`} cx={x(idx(m.month))} cy={yv(Number(m[k]))} r={hover === i || i === months.length - 1 ? 4.5 : 3}
                    fill={k === "cum_in" ? IN : OUT} stroke="#fff" strokeWidth={2} />
          )))}
          {last && (
            <>
              <text x={x(idx(last.month)) + 9} y={yIn + 4} className="fill-slate-700 text-[11px] font-semibold">{compact(Number(last.cum_in))}</text>
              <text x={x(idx(last.month)) + 9} y={yOut + 4} className="fill-slate-700 text-[11px] font-semibold">{compact(Number(last.cum_out))}</text>
            </>
          )}
          {/* Zonas de pasar el mouse: una franja por mes cerrado, más ancha que la marca. */}
          {months.map((m, i) => (
            <rect key={m.month} x={x(idx(m.month)) - pw / span / 2} y={T} width={pw / span} height={ph} fill="transparent"
                  tabIndex={0} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
          ))}
        </svg>
      )}
      {hv && (
        <div className="pointer-events-none absolute z-10 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs shadow-lg"
             style={{ left: Math.min(x(idx(hv.month)) + 10, W - 236), top: 8, width: 226 }}>
          <div className="text-slate-500 mb-1">{new Date(Number(hv.month.slice(0, 4)), idx(hv.month), 1).toLocaleDateString(lang, { month: "long", year: "numeric" })}</div>
          {([[IN, t("owner.summary.in"), hv.total_in, hv.cum_in], [OUT, t("owner.summary.out"), hv.total_out, hv.cum_out]] as const).map(([c, l, mo, cu]) => (
            <div key={l} className="flex items-center gap-1.5 py-0.5">
              <span className="inline-block w-3 h-0.5 rounded" style={{ background: c }} />
              <span className="font-semibold text-slate-900 tabular-nums">{money(Number(mo))}</span>
              <span className="text-slate-500">{l}</span>
              <span className="ms-auto whitespace-nowrap text-slate-400 tabular-nums" title={t("owner.year.soFar")}>Σ {compact(Number(cu))}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Barras horizontales del año por categoría (una sola serie = un solo color). Una categoría en negativo (p. ej.
 *  un reembolso de un subcontratista) crece hacia la izquierda de la línea de cero. */
function Bars({ rows, color }: { rows: Year["out_by_category"]; color: string }) {
  const { t, i18n } = useTranslation();
  const showTr = (i18n.language || "en").split("-")[0] !== "en";
  const [hover, setHover] = useState<string | null>(null);
  if (rows.length === 0) return <p className="text-sm text-slate-400">{t("owner.summary.nothing")}</p>;
  const vals = rows.map((r) => Number(r.amount));
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals);
  const span = hi - lo || 1;
  const zero = (-lo / span) * 100;
  return (
    <div className="space-y-1.5">
      {rows.map((r) => {
        const v = Number(r.amount);
        const w = (Math.abs(v) / span) * 100;
        const tr = showTr ? t(`cat.${r.category}`, { defaultValue: "" }) : "";
        return (
          <div key={r.category} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)_auto] items-center gap-2 text-xs"
               onMouseEnter={() => setHover(r.category)} onMouseLeave={() => setHover(null)}
               title={`${r.category} · ${money(v)} · ${t("owner.year.txCount", { count: r.count })}`}>
            {/* En español, el nombre en español primero y el término contable en inglés debajo (el dueño no es contador). */}
            <span className="min-w-0">
              <span className="block truncate text-slate-700">{tr || r.category}</span>
              {tr && <span className="block truncate text-[10px] text-slate-400">{r.category}</span>}
            </span>
            <span className="relative h-3.5">
              {lo < 0 && <span className="absolute inset-y-0 w-px bg-slate-300" style={{ left: `${zero}%` }} />}
              <span className={`absolute inset-y-0 transition-opacity ${v < 0 ? "rounded-s" : "rounded-e"} ${hover && hover !== r.category ? "opacity-60" : ""}`}
                    style={{ background: color, width: `${Math.max(w, 0.5)}%`, left: v < 0 ? `${zero - w}%` : `${zero}%` }} />
            </span>
            <span className="tabular-nums text-slate-700 text-end">{money(v)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** La misma información en tabla (accesibilidad, y "la colección de datos"): categoría × mes cerrado, y el año.
 *  Cada celda es la foto de ese mes; la columna del año viene de SQL. */
function YearTable({ year, closed, lang }: { year: Year; closed: OwnerMonth[]; lang: string }) {
  const { t } = useTranslation();
  const months = [...closed].sort((a, b) => a.month.localeCompare(b.month));
  const cell = (m: OwnerMonth, side: "in" | "out", cat: string) => m[side].find((r) => r.category === cat)?.amount;
  const block = (side: "in" | "out", rows: Year["in_by_category"], label: string, total: string) => (
    <>
      <tr className="bg-slate-50"><th colSpan={months.length + 2} className="px-2 py-1 text-start text-[11px] uppercase tracking-wide text-slate-500">{label}</th></tr>
      {rows.map((r) => (
        <tr key={side + r.category} className="border-t border-slate-100">
          <td className="px-2 py-1 text-slate-700 truncate max-w-[14rem]" title={r.category}>{t(`cat.${r.category}`, { defaultValue: r.category })}</td>
          {months.map((m) => { const v = cell(m, side, r.category); return <td key={m.month} className="px-2 py-1 text-end tabular-nums text-slate-600">{v ? money(Number(v)) : "—"}</td>; })}
          <td className="px-2 py-1 text-end tabular-nums font-semibold text-slate-900">{money(Number(r.amount))}</td>
        </tr>
      ))}
      <tr className="border-t border-slate-200">
        <td className="px-2 py-1 font-semibold text-slate-900">{t("owner.summary.total")}</td>
        {months.map((m) => <td key={m.month} className="px-2 py-1 text-end tabular-nums font-semibold text-slate-700">{money(Number(side === "in" ? m.total_in : m.total_out))}</td>)}
        <td className="px-2 py-1 text-end tabular-nums font-semibold text-slate-900">{money(Number(total))}</td>
      </tr>
    </>
  );
  return (
    <div className="mt-2 overflow-x-auto bg-white rounded-xl border border-slate-200">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-slate-500">
            <th className="px-2 py-1.5 text-start font-medium">{t("owner.year.category")}</th>
            {months.map((m) => <th key={m.month} className="px-2 py-1.5 text-end font-medium">{monthShort(lang, Number(m.month.slice(5, 7)) - 1)}</th>)}
            <th className="px-2 py-1.5 text-end font-medium">{year.year}</th>
          </tr>
        </thead>
        <tbody>
          {block("in", year.in_by_category, t("owner.summary.in"), year.total_in)}
          {block("out", year.out_by_category, t("owner.summary.out"), year.total_out)}
        </tbody>
      </table>
    </div>
  );
}
