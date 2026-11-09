import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
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

// 8-nov (muchos clientes): a la vista, los 5 que más urge cobrar (lo vencido primero); el resto, en Cobros.
const OWES_SHOWN = 5;

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
    <div data-tour="pp-owes" className="h-full flex flex-col p-3 font-sans">
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
          {owing.slice(0, OWES_SHOWN).map((r) => {
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
          {owing.length > OWES_SHOWN && (
            <li>
              <button onClick={() => window.dispatchEvent(new CustomEvent(OWNER_VIEW_EVENT, { detail: "invoices" }))}
                      className="w-full rounded-lg border border-dashed border-slate-300 py-1.5 text-xs font-semibold text-[#0070BA] hover:bg-sky-50">
                {t("owner.dashboard.owes.all", { count: owing.length })}
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// ── Indicadores ─────────────────────────────────────────────────────────────────────────────────────────────────────

const ic = (d: string) => (
  <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={d} /></svg>
);
type Tone = "good" | "info" | "bad" | "warn" | "neutral";
/** Cada indicador por su id en el tablero: texto, ícono y cuándo es bueno o malo. */
const KPI: Record<string, { label: string; icon: string; tone: (v: number) => Tone; hint?: (v: number) => string | null }> = {
  collected: { label: "collected", icon: "M20 6 9 17l-5-5", tone: () => "good" },
  open: { label: "open", icon: "M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0", tone: (v) => (v > 0 ? "info" : "neutral"),
          hint: (v) => (v > 0 ? null : "openNone") },
  overdue: { label: "overdue", icon: "M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0", tone: (v) => (v > 0 ? "bad" : "good"),
             hint: (v) => (v > 0 ? "overdueSome" : "overdueNone") },
  // 8-nov (regla 4): lo Pendiente de clasificar de los meses cerrados se declara siempre, aunque sea cero.
  pending: { label: "pending", icon: "M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2",
             tone: (v) => (v > 0 ? "warn" : "good"), hint: (v) => (v > 0 ? "pendingSome" : "pendingNone") },
  contractors: { label: "contractorsPaid", icon: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M22 21v-2a4 4 0 0 0-3-3.9", tone: () => "neutral" },
};
const TONE: Record<Tone, string> = {
  good: "bg-emerald-50 text-emerald-700", warn: "bg-amber-50 text-amber-700", info: "bg-sky-50 text-[#0070BA]", bad: "bg-rose-50 text-rose-700", neutral: "bg-slate-100 text-slate-600",
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
        <div className="line-clamp-2 text-xs leading-tight text-slate-500">{t(`owner.dashboard.w.${cfg.label}`)}</div>
        {hint && <div className={`line-clamp-1 text-[11px] ${tone === "bad" ? "text-rose-700" : tone === "warn" ? "text-amber-700" : "text-emerald-700"}`}>{t(`owner.dashboard.kpi.${hint}`)}</div>}
      </div>
    </div>
  );
}

// ── Buscar cliente (8-nov, con muchos clientes) ──────────────────────────────────────────────────────────────────────
// Con más de 6 clientes, los botones no caben y el filtro de lista de AG Studio es una lista vertical (en la franja de arriba
// se ve una sola fila). Este widget es un buscador: se escribe, se elige un cliente de la lista (los que más facturaron
// primero) y todo el tablero se filtra con el filtro cruzado de AG Studio. La lista se dibuja fuera del widget (portal),
// para que no la corte el borde de la franja.

function CustomerSearch(params: any) {
  const { t, i18n } = useTranslation();
  const rows = useRows(params);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  const most = useRef(0);   // cuántos clientes hay en total (con el filtro puesto, la consulta trae solo uno)
  const [rect, setRect] = useState<DOMRect | null>(null);
  useEffect(() => {
    if (!open) return;
    const place = () => box.current && setRect(box.current.getBoundingClientRect());
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => { window.removeEventListener("scroll", place, true); window.removeEventListener("resize", place); };
  }, [open]);
  if (!rows) return <AgLoadingOverlay />;
  const all = rows.map((r) => ({ customer: String(r.customer), billed: Number(r.billed) || 0 }))
    .sort((a, b) => b.billed - a.billed);
  most.current = Math.max(most.current, all.length);
  // El cliente elegido se lee de AG Studio (no de un estado propio): si el widget se vuelve a crear (al abrir el chat, por
  // ejemplo) o si otro widget cambia el filtro, la etiqueta sigue diciendo la verdad.
  const custField = (params.dataMapping?.customer ?? [])[0];
  const nameOf = (f: any) => { try { return params.widgetApi.getFieldName(f); } catch { return null; } };
  const sel = (params.widgetApi.getCrossFilterSelections?.() ?? [])
    .find((x: any) => x.type === "value" && x.values?.length && (!custField || nameOf(x.field) === nameOf(custField)));
  const picked: string | null = sel ? String(sel.values[0]) : null;
  const norm = (x: string) => x.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const words = norm(q).split(/\s+/).filter(Boolean);   // cada palabra, en cualquier orden ("bright den")
  const hits = all.filter((r) => words.every((w) => norm(r.customer).includes(w))).slice(0, 50);
  const toggle = (customer: string) => {
    const f = (params.dataMapping?.customer ?? [])[0];
    if (f) params.widgetApi.toggleCrossFilter({ type: "value", field: f, value: customer, group: 0 });
  };
  const choose = (customer: string) => {
    if (picked && picked !== customer) toggle(picked);   // quita el anterior
    if (picked !== customer) toggle(customer);
    setQ(""); setOpen(false);
  };
  const clear = () => params.widgetApi.resetCrossFilter();
  return (
    <div className="h-full flex items-center gap-3 px-3 font-sans">
      <div ref={box} className="relative w-full max-w-md">
        <svg viewBox="0 0 24 24" className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
        <input value={q} onChange={(e) => { setQ(e.target.value); setOpen(true); }} onFocus={() => setOpen(true)}
               onBlur={() => setTimeout(() => setOpen(false), 150)}
               onKeyDown={(e) => { if (e.key === "Enter" && hits[0]) choose(hits[0].customer); if (e.key === "Escape") setOpen(false); }}
               placeholder={t("owner.dashboard.search.placeholder", { count: most.current })} aria-label={t("owner.dashboard.search.label")}
               className="h-9 w-full rounded-full border border-slate-300 bg-white pl-9 pr-3 text-sm text-slate-800 outline-none focus:border-[#0070BA] focus:ring-2 focus:ring-sky-100" />
      </div>
      {picked && (
        <span className="flex shrink-0 items-center gap-1 rounded-full bg-[#0070BA] py-1 pl-3 pr-1 text-sm font-medium text-white">
          {picked}
          <button onClick={clear} aria-label={t("owner.dashboard.search.clear")} className="grid size-6 place-items-center rounded-full hover:bg-white/20">×</button>
        </span>
      )}
      {open && rect && createPortal(
        <ul role="listbox" style={{ position: "fixed", left: rect.left, top: rect.bottom + 4, width: rect.width, zIndex: 70 }}
            className="max-h-64 overflow-auto rounded-xl border border-slate-200 bg-white py-1 text-sm shadow-lg font-sans">
          {hits.length === 0
            ? <li className="px-3 py-2 text-slate-500">{t("owner.dashboard.search.none")}</li>
            : hits.map((r) => (
              <li key={r.customer} role="option" aria-selected={r.customer === picked}
                  onMouseDown={(e) => { e.preventDefault(); choose(r.customer); }}
                  className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 hover:bg-sky-50 ${r.customer === picked ? "font-semibold text-[#0070BA]" : "text-slate-800"}`}>
                <span className="truncate">{r.customer}</span>
                <span className="ms-auto shrink-0 tabular-nums text-xs text-slate-500">{usd(r.billed, i18n.language)}</span>
              </li>
            ))}
        </ul>, document.body)}
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
        id: "contabu-who-owes", label: i18n.t("owner.dashboard.w.whoOwes"), comp: WhoOwes, form: noEdit, toolbar: [],
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
        id: "contabu-customer-search", label: i18n.t("owner.dashboard.search.label"), comp: CustomerSearch, form: noEdit, toolbar: [],
        dataMapping: {
          customer: { type: "field", supportedRoles: ["category"], requires: { cardinality: "many" }, required: true },
          billed: { type: "field", supportedRoles: ["numeric"], requires: { per: "dataMapping.customer", cardinality: "one" } },
        },
        featureConfig: { crossFilter: { supportsHighlight: false } },
        defaultSize: { width: 400, height: 60 }, minSize: { width: 200, height: 48 },
        ai: { description: "Search box to filter the whole dashboard by one customer." },
      },
      {
        id: "contabu-kpi", label: i18n.t("owner.dashboard.w.kpi"), comp: Kpi, form: noEdit, toolbar: [],
        dataMapping: { value: { type: "field", supportedRoles: ["numeric"], requires: { cardinality: "one" }, required: true } },
        defaultSize: { width: 200, height: 100 }, minSize: { width: 140, height: 72 },
        ai: { description: "A single total (USD) with an icon and a color that says if it is good or bad." },
      },
    ],
  } as any);
}
