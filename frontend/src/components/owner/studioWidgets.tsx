import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgLoadingOverlay, createWidgets } from "ag-studio-react";

// LOS WIDGETS PROPIOS DEL TABLERO (FASE 16, rama `paypal-contest`, 8-nov). El premio de AG Studio valora "custom widgets,
// theming, and layout tools, not just AG Grid or AG Charts on their own" y sugiere un widget de "quién debe qué".
// - `contabu-who-owes` — Quién me debe: cada cliente con lo que debe, los días de atraso y la parte vencida; tocar uno
//   filtra todo el tablero (filtro cruzado de AG Studio) y "Recordar" abre Cobros (el recordatorio lo manda el dueño).
// - `contabu-kpi` — un indicador con ícono y color según si la cifra es buena o mala, y la cifra que sube al cargar.
// Los datos los pide cada widget al motor de AG Studio (`widgetApi.getData`), sobre filas que ya calculó el código.

/* eslint-disable @typescript-eslint/no-explicit-any */
const OWNER_VIEW_EVENT = "contabu:owner-view";   // el mismo de OwnerApp (sin importarlo: evita un ciclo)

/** Las filas de una consulta, con cada valor por la clave del widget ("customer", "open"…). Las filas de AG Studio vienen
 *  con claves "tabla.campo.agregación" en el MISMO orden que los campos pedidos (✅ 8-nov, visto en la consola). */
function useRows(params: any): any[] | null {
  const { widgetApi, dataMapping } = params;
  const [rows, setRows] = useState<any[] | null>(null);
  const keys = Object.keys(dataMapping ?? {}).filter((k) => (dataMapping[k] ?? []).length);
  const fields = keys.flatMap((k) => dataMapping[k]) as any[];
  const sig = JSON.stringify(fields.map((f) => widgetApi.getFieldName(f)));
  useEffect(() => {
    let alive = true;
    if (!fields.length) return;
    widgetApi.getData({ fields }, { queryId: "rows" }).then((res: any) => {
      if (!alive || res.replaced) return;
      setRows((res.results?.rows ?? []).map((r: any) => {
        const vals = Object.values(r);
        return Object.fromEntries(keys.map((k, i) => [k, vals[i]]));
      }));
    }).catch(() => alive && setRows([]));
    return () => { alive = false; };
  }, [widgetApi, sig]);   // eslint-disable-line react-hooks/exhaustive-deps
  return rows;
}

const usd = (n: number, lang: string) =>
  new Intl.NumberFormat(lang.startsWith("es") ? "es-US" : "en-US", { style: "currency", currency: "USD" }).format(n || 0);

/** La cifra que sube de 0 a su valor (≈0.7 s) al cargar o al filtrar. */
function useCountUp(target: number) {
  const [v, setV] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / 700);
      const eased = 1 - Math.pow(1 - p, 3);
      setV(a + (target - a) * eased);
      if (p < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target]);
  return v;
}

// ── Quién me debe ────────────────────────────────────────────────────────────────────────────────────────────────────

function WhoOwes(params: any) {
  const { t, i18n } = useTranslation();
  const rows = useRows(params);
  if (!rows) return <AgLoadingOverlay />;
  const owing = rows.map((r) => ({ customer: r.customer as string, open: Number(r.open) || 0, overdue: Number(r.overdue) || 0,
                                   days: Number(r.days_overdue) || 0 }))
    .filter((r) => r.open + r.overdue > 0.004)
    .sort((a, b) => b.overdue - a.overdue || b.days - a.days || b.open - a.open);
  const total = owing.reduce((s, r) => s + r.open + r.overdue, 0);
  const click = (customer: string) => {
    const f = (params.dataMapping?.customer ?? [])[0];
    if (f) params.widgetApi.toggleCrossFilter({ type: "value", field: f, value: customer, group: 0 });
  };
  return (
    <div className="h-full flex flex-col p-3 font-sans">
      <div className="flex items-baseline gap-2">
        <h3 className="text-sm font-semibold text-slate-900">{t("owner.dashboard.w.whoOwes")}</h3>
        {owing.length > 0 && <span className="text-xs text-slate-500">{t("owner.dashboard.owes.total", { amount: usd(total, i18n.language) })}</span>}
      </div>
      {owing.length === 0 ? (
        <div className="flex-1 grid place-items-center text-center">
          <div>
            <div className="mx-auto mb-2 grid size-10 place-items-center rounded-full bg-emerald-100 text-emerald-700 text-xl">✓</div>
            <p className="text-sm font-semibold text-slate-800">{t("owner.dashboard.owes.none")}</p>
            <p className="text-xs text-slate-500">{t("owner.dashboard.owes.noneHint")}</p>
          </div>
        </div>
      ) : (
        <ul className="mt-2 flex-1 overflow-auto space-y-2 pr-1">
          {owing.map((r) => {
            const owed = r.open + r.overdue;
            const late = r.overdue > 0;
            return (
              <li key={r.customer} onClick={() => click(r.customer)}
                  className="group cursor-pointer rounded-lg border border-slate-200 bg-white p-2.5 hover:border-[#0070BA] hover:shadow-sm transition">
                <div className="flex items-center gap-2.5">
                  <span className={`grid size-8 shrink-0 place-items-center rounded-full text-xs font-bold ${late ? "bg-rose-100 text-rose-700" : "bg-sky-100 text-[#0070BA]"}`}>
                    {r.customer.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-2">
                      <span className="truncate text-sm font-medium text-slate-900">{r.customer}</span>
                      <span className="ms-auto text-sm font-semibold tabular-nums text-slate-900">{usd(owed, i18n.language)}</span>
                    </div>
                    <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full bg-rose-500" style={{ width: `${(r.overdue / owed) * 100}%`, float: "left" }} />
                      <div className="h-full bg-[#009CDE]" style={{ width: `${(r.open / owed) * 100}%`, float: "left" }} />
                    </div>
                    <div className="mt-1 flex items-center gap-2 text-[11px]">
                      {late
                        ? <span className="rounded-full bg-rose-50 px-1.5 py-0.5 font-medium text-rose-700">{t("owner.dashboard.owes.late", { count: r.days })}</span>
                        : <span className="rounded-full bg-sky-50 px-1.5 py-0.5 font-medium text-[#0070BA]">{t("owner.dashboard.owes.notDue")}</span>}
                      <button onClick={(e) => { e.stopPropagation(); window.dispatchEvent(new CustomEvent(OWNER_VIEW_EVENT, { detail: "invoices" })); }}
                              className="ms-auto rounded-md bg-[#FFC439] px-2 py-0.5 font-semibold text-[#003087] hover:bg-[#f2ba36]">
                        {t("owner.dashboard.owes.remind")}
                      </button>
                    </div>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ── Indicadores ─────────────────────────────────────────────────────────────────────────────────────────────────────

const ic = (d: string) => (
  <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
);
type Tone = "good" | "info" | "bad" | "neutral";
/** Cada indicador por su id en el tablero: texto, ícono y cuándo es bueno o malo. */
const KPI: Record<string, { label: string; icon: string; tone: (v: number) => Tone; hint?: (v: number) => string | null }> = {
  collected: { label: "collected", icon: "M20 6 9 17l-5-5", tone: () => "good" },
  open: { label: "open", icon: "M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0", tone: (v) => (v > 0 ? "info" : "neutral"),
          hint: (v) => (v > 0 ? null : "openNone") },
  overdue: { label: "overdue", icon: "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0", tone: (v) => (v > 0 ? "bad" : "good"),
             hint: (v) => (v > 0 ? "overdueSome" : "overdueNone") },
  contractors: { label: "contractorsPaid", icon: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9", tone: () => "neutral" },
};
const TONE: Record<Tone, string> = {
  good: "bg-emerald-50 text-emerald-700", info: "bg-sky-50 text-[#0070BA]", bad: "bg-rose-50 text-rose-700", neutral: "bg-slate-100 text-slate-600",
};

function Kpi(params: any) {
  const { t, i18n } = useTranslation();
  const rows = useRows(params);
  const value = rows ? Number(rows[0]?.value ?? 0) : 0;
  const shown = useCountUp(value);
  const cfg = KPI[params.widgetId] ?? KPI.collected;
  if (!rows) return <AgLoadingOverlay />;
  const tone = cfg.tone(value);
  const hint = cfg.hint?.(value);
  return (
    <div className="h-full flex items-center gap-3 px-3 font-sans">
      <span className={`grid size-9 shrink-0 place-items-center rounded-xl ${TONE[tone]}`}>{ic(cfg.icon)}</span>
      <div className="min-w-0">
        <div className={`text-xl font-semibold tabular-nums leading-tight ${tone === "bad" ? "text-rose-700" : "text-slate-900"}`}>{usd(shown, i18n.language)}</div>
        <div className="truncate text-xs text-slate-500">{t(`owner.dashboard.w.${cfg.label}`)}</div>
        {hint && <div className={`truncate text-[11px] ${tone === "bad" ? "text-rose-700" : "text-emerald-700"}`}>{t(`owner.dashboard.kpi.${hint}`)}</div>}
      </div>
    </div>
  );
}

// ── Registro ────────────────────────────────────────────────────────────────────────────────────────────────────────

const noEdit = (params: any) => params.createDefaults({ dataMappingItems: [] });

export function contabuWidgets(): any {
  // `as any`: el registro de tipos de AG Studio no conoce nuestros ids de widget (se validan en tiempo de ejecución).
  return createWidgets({
    additionalTypes: [
      {
        id: "contabu-who-owes", label: "Who owes me", comp: WhoOwes, form: noEdit, toolbar: [],
        dataMapping: {
          customer: { type: "field", supportedRoles: ["category"], requires: { cardinality: "many" }, required: true },
          open: { type: "field", supportedRoles: ["numeric"], requires: { per: "dataMapping.customer", cardinality: "one" } },
          overdue: { type: "field", supportedRoles: ["numeric"], requires: { per: "dataMapping.customer", cardinality: "one" } },
          days_overdue: { type: "field", supportedRoles: ["numeric"], requires: { per: "dataMapping.customer", cardinality: "one" } },
        },
        featureConfig: { crossFilter: { supportsHighlight: false } },
        defaultSize: { width: 400, height: 300 }, minSize: { width: 240, height: 160 },
        ai: { description: "Who owes the business money: each customer with the amount owed, how much is overdue and the days late. Computed by the code." },
      },
      {
        id: "contabu-kpi", label: "KPI", comp: Kpi, form: noEdit, toolbar: [],
        dataMapping: { value: { type: "field", supportedRoles: ["numeric"], requires: { cardinality: "one" }, required: true } },
        defaultSize: { width: 200, height: 100 }, minSize: { width: 140, height: 72 },
        ai: { description: "A single total (USD) with an icon and a color that says if it is good or bad." },
      },
    ],
  } as any);
}
