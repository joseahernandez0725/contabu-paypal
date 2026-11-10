import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { money, type OwnerMonth, type OwnerYear as Year } from "../../api";
import { DetailCard } from "../PageKit";
import { CategoryBars, IN, MonthsChart, OUT } from "./yearCharts";

// EL AÑO DEL DUEÑO (FASE 16, 31-oct, pedido de Jose): los meses cerrados como UNA colección acumulada del año, no
// un reporte mes a mes, con gráficos para que el dueño lea mejor sus datos. Todo número viene del backend (SQL
// sobre las fotos de los meses cerrados: `month_close.owner_years`); aquí solo se dibuja.
// Diseño según la guía de gráficos (skill dataviz): una sola escala, líneas de 2px, barras ≤ 24px con la punta
// redondeada, rejilla de 1px, leyenda + etiquetas directas (la identidad nunca es solo el color), tooltip al pasar
// el mouse y una TABLA con los mismos datos. Paleta validada (verde / azul, pasa las pruebas de daltonismo); la app
// no tiene modo oscuro.

// 10-nov: las gráficas pasaron a AG Charts (`yearCharts.tsx`), con los colores de Análisis; aquí quedan las cifras y la tabla.

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
      {/* 10-nov: la parada 1 de la visita (la portada) ilumina las cifras del año Y su gráfica, no solo la línea de totales. */}
      <div data-tour="pp-year" className="space-y-3">
      <div className="bg-white rounded-xl border border-slate-200 px-3 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-2">
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
        <p className="text-xs text-slate-500">{t("owner.year.sigma")}</p>
        <MonthsChart months={y.months} lang={lang} />
      </DetailCard>
      </div>

      <div data-tour="pp-categories" className="grid gap-3 lg:grid-cols-[3fr_2fr]">
        <DetailCard>
          <h3 className="text-sm font-semibold text-slate-900">{t("owner.year.whereOut")}</h3>
          <p className="text-xs text-slate-500 mb-2">{t("owner.year.whereOutSub", { year: y.year })}</p>
          <CategoryBars rows={y.out_by_category} color={OUT} label={t("owner.summary.out")} />
        </DetailCard>
        <DetailCard>
          <h3 className="text-sm font-semibold text-slate-900">{t("owner.year.whereIn")}</h3>
          <p className="text-xs text-slate-500 mb-2">{t("owner.year.whereInSub", { year: y.year })}</p>
          <CategoryBars rows={y.in_by_category} color={IN} label={t("owner.summary.in")} />
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
      <span className="inline-block w-3 h-1 rounded-full self-center" style={{ background: swatch }} />
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
        <span key={l} className="flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-full" style={{ background: c }} />{l}</span>
      ))}
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
