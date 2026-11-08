import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgStudio, AgStudioProvider } from "ag-studio-react";
import { AgStudioAiModule, studioTheme, type AgReportState, type AgResponsiveLayoutBreakpoint } from "ag-studio";
import { AG_STUDIO_LOCALE_ES_ES } from "ag-studio-locale";
import { getContractors, getInvoices, type ContractorPayment, type OwnerHome, type OwnerInvoice, type StudioTurnCost } from "../../api";
import { inTour } from "../../demo";
import { collectionsAssistant, STUDIO_AI_TEXT, toolDisplay } from "./studioAssistant";

// EL TABLERO DEL DUEÑO CON AG STUDIO (FASE 16, solo en la rama `paypal-contest`: AG Studio es de pago y ContaBu no
// depende de él; Jose, 2-nov y 6-nov). Premio "Best Use of AG Grid" (pide AG Studio, 16.B §10).
// Lo que el dueño ya ve en su portal, en un tablero: sus facturas (Cobros), sus pagos a contratistas y los totales de los
// periodos que cerró su bookkeeper (el Resumen). NO hay ruta nueva: los datos vienen de las mismas rutas del portal, con
// sus mismos permisos. AG Studio solo suma y agrupa en el navegador lo que esas rutas ya entregan (el motor de AG Studio,
// no la IA). Las transacciones del libro nunca llegan aquí (regla del portal: totales, nunca transacciones).
// La llave de la licencia: `VITE_AG_STUDIO_LICENSE`, fuera de git. Sin llave corre con la marca de agua de prueba.

const LICENSE = (import.meta.env.VITE_AG_STUDIO_LICENSE as string | undefined) || undefined;

// PayPal: azul de marca y el amarillo del botón; el resto, la tipografía de ContaBu.
const PALETTE = ["#0070BA", "#003087", "#009CDE", "#FFC439", "#2E7D32", "#7B61FF", "#E0699B", "#8C9BAB", "#00A3A3", "#F28C28"];
const theme = studioTheme.withParams({
  accentColor: "#0070BA",
  fontFamily: "inherit",
  browserColorScheme: "light",
  ...Object.fromEntries(PALETTE.flatMap((c, i) => [[`chartPaletteFills${i + 1}Color`, c], [`chartPaletteStrokes${i + 1}Color`, c]])),
});

// En el celular (6-nov): un widget debajo del otro, a todo lo ancho, y sin el panel de filtros (se filtra tocando).
const RESPONSIVE: AgResponsiveLayoutBreakpoint[] = [{
  maxWidth: 640,
  layout: (cands, { columns }) => {
    let y = 0;
    return cands.map((c) => {
      const kpi = c.layout.ySpan <= 5;
      const l = { xTrack: 0, yTrack: y, xSpan: columns, ySpan: kpi ? 4 : c.layout.ySpan };
      y += l.ySpan;
      return l;
    });
  },
}];
const NARROW = typeof window !== "undefined" && window.innerWidth < 640;

const PAID = new Set(["PAID", "MARKED_AS_PAID", "PAID_EXTERNAL"]);
const REFUNDED = new Set(["REFUNDED", "PARTIALLY_REFUNDED", "MARKED_AS_REFUNDED", "REFUNDED_EXTERNAL"]);
const OPEN = new Set(["SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED", "SHARED"]);

/** El estado en inglés (los datos van en inglés, regla 5; el idioma es solo la pantalla). */
function statusOf(inv: OwnerInvoice): string {
  if (PAID.has(inv.status)) return "Paid";
  if (REFUNDED.has(inv.status)) return "Refunded";
  if (OPEN.has(inv.status)) return inv.overdue ? "Overdue" : "Open";
  if (inv.status === "DRAFT") return "Draft";
  return "Cancelled";
}

const month = (iso: string | null | undefined) => (iso ? iso.slice(0, 7) : null);
const num = (s: string | null | undefined) => (s ? Number(s) : 0);
// 8-nov (prueba de 10 preguntas): con los meses solo como texto "2026-08", el modelo buscaba "August" o "este año" y no
// encontraba nada (3 de 10 fallaron por esto). Cada fila lleva también el año como número y el mes con su nombre.
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const monthName = (ym: string | null) => (ym ? MONTHS[Number(ym.slice(5, 7)) - 1] : null);   // "August": el año va en `year`
const monthYear = (ym: string | null) => (ym ? `${MONTHS[Number(ym.slice(5, 7)) - 1]} ${ym.slice(0, 4)}` : null);
const yearOf = (ym: string | null) => (ym ? Number(ym.slice(0, 4)) : null);

export default function OwnerDashboard({ home }: { home: OwnerHome }) {
  const { t, i18n } = useTranslation();
  const es = (i18n.language || "en").startsWith("es");
  const [invoices, setInvoices] = useState<OwnerInvoice[] | null>(null);
  const [payments, setPayments] = useState<ContractorPayment[]>([]);
  const [error, setError] = useState(false);
  // El asistente (Agent Framework): solo el dueño en su libro; ni la vista previa del bookkeeper ni la visita (sin IA).
  const canAsk = !home.preview && !inTour();
  const [asking, setAsking] = useState(false);
  // Por cada respuesta (8-nov): se acumulan sus turnos hasta el final y se muestra el resumen, con la revisión de cifras.
  type Answer = { turns: number; ms: number; tokens: number; usd: number; model: string; verified: boolean | null };
  const running = useRef<Answer>({ turns: 0, ms: 0, tokens: 0, usd: 0, model: "", verified: null });
  const [last, setLast] = useState<Answer | null>(null);
  const onTurn = useCallback((c: StudioTurnCost) => {
    const r = running.current;
    r.turns += 1; r.ms += c.latency_ms; r.tokens += c.tokens; r.usd += c.cost_usd ?? 0; r.model = c.model ?? r.model;
    if (c.final) {
      setLast({ ...r, verified: c.verified ?? null });
      running.current = { turns: 0, ms: 0, tokens: 0, usd: 0, model: "", verified: null };
    }
  }, []);
  const tools = useMemo(() => toolDisplay(t), [t]);
  const homeRef = useRef(home);
  homeRef.current = home;
  const ai = useMemo(() => collectionsAssistant(es ? "es" : "en", onTurn, [
    { label: t("owner.dashboard.ai.s1"), prompt: t("owner.dashboard.ai.p1") },
    { label: t("owner.dashboard.ai.s2"), prompt: t("owner.dashboard.ai.p2") },
    { label: t("owner.dashboard.ai.s3"), prompt: t("owner.dashboard.ai.p3") },
  ], () => homeRef.current), [es, onTurn, t]);

  useEffect(() => {
    getInvoices().then((v) => setInvoices(v.invoices)).catch(() => setError(true));
    getContractors().then((v) => setPayments(v.enabled ? v.payments : [])).catch(() => setPayments([]));
  }, []);

  // Los datos: tres tablas de filas planas, todas con cifras que ya calculó el código (SQL).
  const data = useMemo(() => {
    if (!invoices) return null;
    const inv = invoices.filter((i) => i.status !== "DRAFT").map((i) => {
      const status = statusOf(i);
      const amount = num(i.amount);
      return {
        number: i.number ?? "—", customer: i.customer?.name ?? "—", concept: i.concept, status,
        amount,
        collected: status === "Paid" ? amount : 0,
        open: status === "Open" ? amount : 0,
        overdue: status === "Overdue" ? amount : 0,
        due_month: month(i.due_date), paid_month: month(i.paid_at),
        paid_month_name: monthName(month(i.paid_at)), year: yearOf(month(i.paid_at) ?? month(i.due_date)),
      };
    });
    const books = [...home.closed_months].sort((a, b) => a.month.localeCompare(b.month)).flatMap((m) => [
      ...m.in.map((c) => ({ month: m.month, month_name: monthName(m.month), year: yearOf(m.month), category: c.category, income: num(c.amount), costs: 0 })),
      ...m.out.map((c) => ({ month: m.month, month_name: monthName(m.month), year: yearOf(m.month), category: c.category, income: 0, costs: num(c.amount) })),
    ]);
    const pays = payments.filter((p) => p.status === "SUCCESS").map((p) => ({
      contractor: p.contractor ?? "—", month: month(p.date), month_name: monthName(month(p.date)), year: yearOf(month(p.date)), amount: num(p.amount), fee: num(p.fee),
    }));
    // Dólares con su signo, en el idioma de la pantalla.
    const cur = { format: "currencyFormat" as const,
                  formatOptions: { format: new Intl.NumberFormat(es ? "es-US" : "en-US", { style: "currency", currency: "USD" }) } };
    const txt = { format: "textFormat" as const };
    const yr = { format: "integerFormat" as const, formatOptions: { format: "0" } };
    return {
      // Para la IA (8-nov): qué es cada tabla y qué periodo cubre.
      description: `The dashboard of ${home.company.name}, a small business paid with PayPal. Today is ${new Date().toISOString().slice(0, 10)}. `
        + `"invoices": every invoice sent (paid, open, overdue). "books": income and costs per category for each month the bookkeeper closed`
        + `${home.closed_months.length ? ` (${monthYear(home.closed_months[home.closed_months.length - 1].month)} to ${monthYear(home.closed_months[0].month)})` : ""}. `
        + `"contractors": payments to contractors with PayPal Payouts. All amounts are USD.`,
      sources: [
        { id: "invoices", name: t("owner.dashboard.src.invoices"), data: inv, fields: [
          { id: "number", name: t("owner.dashboard.f.number"), description: "Invoice number", ...txt },
          { id: "customer", name: t("owner.dashboard.f.customer"), description: "Customer's name", ...txt },
          { id: "concept", name: t("owner.dashboard.f.concept"), ...txt },
          { id: "status", name: t("owner.dashboard.f.status"), description: "Paid, Open, Overdue, Refunded or Cancelled", ...txt },
          { id: "amount", name: t("owner.dashboard.f.amount"), description: "Invoice amount in USD", ...cur },
          { id: "collected", name: t("owner.dashboard.f.collected"), description: "USD collected (paid invoices only)", ...cur },
          { id: "open", name: t("owner.dashboard.f.open"), description: "USD still to collect, not yet due", ...cur },
          { id: "overdue", name: t("owner.dashboard.f.overdue"), description: "USD past due and unpaid", ...cur },
          { id: "due_month", name: t("owner.dashboard.f.dueMonth"), ...txt },
          { id: "paid_month", name: t("owner.dashboard.f.paidMonth"), description: "Month the invoice was paid, YYYY-MM", ...txt },
          { id: "paid_month_name", name: t("owner.dashboard.f.paidMonthName"), description: "Month name the invoice was paid, e.g. August (the year is in the year field)", ...txt },
          { id: "year", name: t("owner.dashboard.f.year"), description: "Year of the invoice (paid, or due if unpaid)", ...yr },
        ] },
        { id: "books", name: t("owner.dashboard.src.books"), data: books, fields: [
          { id: "month", name: t("owner.dashboard.f.month"), description: "Month, YYYY-MM", ...txt },
          { id: "month_name", name: t("owner.dashboard.f.monthName"), description: "Month name only, e.g. August (the year is in the year field)", ...txt },
          { id: "year", name: t("owner.dashboard.f.year"), description: "Year, e.g. 2026", ...yr },
          { id: "category", name: t("owner.dashboard.f.category"), description: "Accounting category", ...txt },
          { id: "income", name: t("owner.dashboard.f.income"), description: "Income in USD for a closed month", ...cur },
          { id: "costs", name: t("owner.dashboard.f.costs"), description: "Costs and expenses in USD for a closed month", ...cur },
        ] },
        { id: "contractors", name: t("owner.dashboard.src.contractors"), data: pays, fields: [
          { id: "contractor", name: t("owner.dashboard.f.contractor"), ...txt },
          { id: "month", name: t("owner.dashboard.f.month"), description: "Month, YYYY-MM", ...txt },
          { id: "month_name", name: t("owner.dashboard.f.monthName"), description: "Month name only, e.g. August (the year is in the year field)", ...txt },
          { id: "year", name: t("owner.dashboard.f.year"), description: "Year, e.g. 2026", ...yr },
          { id: "amount", name: t("owner.dashboard.f.paid"), ...cur },
          { id: "fee", name: t("owner.dashboard.f.fee"), ...cur },
        ] },
      ],
    };
  }, [invoices, payments, home, t, es]);

  // El tablero ya armado (el dueño no tiene que construir nada); en modo vista: filtra y cruza, no edita.
  const initialState = useMemo(() => {
    const title = (key: string) => ({ title: { enabled: true, text: t(`owner.dashboard.w.${key}`) } });
    const kpi = (key: string, field: string) => ({
      type: "value", dataMapping: { value: [{ id: field, aggregation: "sum" }] },
      format: { style: { label: { enabled: true, text: t(`owner.dashboard.w.${key}`) } } },
    });
    return {
      pages: [{
        id: "collections",
        layout: { minWidth: 320 },   // sin esto el lienzo mide 720 px y en el celular se sale de la pantalla
        widgets: {
          collected: kpi("collected", "invoices.collected"),
          open: kpi("open", "invoices.open"),
          overdue: kpi("overdue", "invoices.overdue"),
          contractors: kpi("contractorsPaid", "contractors.amount"),
          byCustomer: {
            type: "column-chart-grouped",
            dataMapping: { categoryKey: [{ id: "invoices.customer" }],
                           valueKey: [{ id: "invoices.collected", aggregation: "sum" }, { id: "invoices.open", aggregation: "sum" },
                                      { id: "invoices.overdue", aggregation: "sum" }], tooltipKey: [] },
            format: title("byCustomer"),
          },
          byStatus: {
            type: "donut-chart",
            dataMapping: { categoryKey: [{ id: "invoices.status" }], valueKey: [{ id: "invoices.amount", aggregation: "sum" }] },
            format: title("byStatus"),
          },
          months: {
            type: "column-chart-grouped",
            dataMapping: { categoryKey: [{ id: "books.month" }],
                           valueKey: [{ id: "books.income", aggregation: "sum" }, { id: "books.costs", aggregation: "sum" }], tooltipKey: [] },
            format: title("months"),
          },
          costs: {
            type: "donut-chart",
            dataMapping: { categoryKey: [{ id: "books.category" }], valueKey: [{ id: "books.costs", aggregation: "sum" }] },
            format: title("costs"),
          },
          list: {
            type: "grid",
            dataMapping: { cols: [{ id: "invoices.number" }, { id: "invoices.customer" }, { id: "invoices.concept" },
                                  { id: "invoices.status" }, { id: "invoices.due_month" }, { id: "invoices.amount", aggregation: "sum" }] },
            format: title("list"),
          },
        },
        widgetLayout: {
          collected: { xTrack: 0, yTrack: 0, xSpan: 6, ySpan: 5 },
          open: { xTrack: 6, yTrack: 0, xSpan: 6, ySpan: 5 },
          overdue: { xTrack: 12, yTrack: 0, xSpan: 6, ySpan: 5 },
          contractors: { xTrack: 18, yTrack: 0, xSpan: 6, ySpan: 5 },
          byCustomer: { xTrack: 0, yTrack: 5, xSpan: 14, ySpan: 12 },
          byStatus: { xTrack: 14, yTrack: 5, xSpan: 10, ySpan: 12 },
          months: { xTrack: 0, yTrack: 17, xSpan: 14, ySpan: 12 },
          costs: { xTrack: 14, yTrack: 17, xSpan: 10, ySpan: 12 },
          list: { xTrack: 0, yTrack: 29, xSpan: 24, ySpan: 14 },
        },
      }],
      selectedPageId: "collections",
      panels: { filters: { collapsed: true } },
    } as unknown as AgReportState;
  }, [t]);

  if (error) return <p className="text-sm text-rose-600">{t("owner.error")}</p>;
  if (!data) return <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <p className="flex-1 min-w-[16rem] text-sm text-slate-600">{t("owner.dashboard.intro")}</p>
        {canAsk && (
          <button onClick={() => setAsking((a) => !a)}
                  className={`shrink-0 rounded-lg px-3 py-1.5 text-sm font-semibold ${asking ? "border border-[#0070BA] text-[#0070BA] bg-white" : "text-[#003087] bg-[#FFC439] hover:bg-[#f2ba36]"}`}>
            {asking ? t("owner.dashboard.ai.close") : t("owner.dashboard.ai.open")}
          </button>
        )}
      </div>
      {asking && <p className="text-[11px] text-slate-500">{t("owner.dashboard.ai.rules")}</p>}
      {/* `key`: AG Studio fija el idioma al crearse; al cambiarlo se vuelve a crear. El chat de AG Studio solo existe en
          el modo de edición: con el asistente abierto se pasa a ese modo, pero solo con el panel del chat. */}
      <div className="rounded-xl border border-slate-200 bg-white overflow-hidden" style={{ height: "min(1100px, calc(100vh - 10rem))", minHeight: 560 }}>
        <AgStudioProvider licenseKey={LICENSE} modules={[AgStudioAiModule]}>
          <AgStudio key={es ? "es" : "en"} data={data as never} initialState={initialState} mode={asking ? "edit" : "view"} theme={theme}
                    localeText={es ? AG_STUDIO_LOCALE_ES_ES : undefined} ai={canAsk ? ai : undefined} aiText={STUDIO_AI_TEXT} aiToolDisplay={tools}
                    responsiveLayoutBreakpoints={RESPONSIVE}
                    panels={{ edit: { left: [], right: ["ai"] }, view: NARROW ? { left: [], right: [] } : { right: ["filters"] } }}
                    style={{ height: "100%", width: "100%" }} />
        </AgStudioProvider>
      </div>
      {last && (
        <p className={`text-[11px] ${last.verified === false ? "text-amber-800" : "text-emerald-800"}`}>
          {t("owner.dashboard.ai.last", { model: last.model.split("/").pop(), turns: last.turns, s: (last.ms / 1000).toFixed(1),
                                          tokens: last.tokens, cost: `$${last.usd.toFixed(4)}` })}
          {" · "}{last.verified === false ? t("owner.dashboard.ai.unverified") : t("owner.dashboard.ai.verified")}
        </p>
      )}
      <p className="text-[11px] text-slate-400">{t("owner.dashboard.note")}</p>
    </div>
  );
}
