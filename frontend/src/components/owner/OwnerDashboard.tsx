import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgStudio, AgStudioProvider } from "ag-studio-react";
import { AgStudioAiModule, studioTheme, type AgAiHarness, type AgReportState, type AgResponsiveLayoutBreakpoint } from "ag-studio";
import { AG_STUDIO_LOCALE_ES_ES } from "ag-studio-locale";
import { getContractors, getInvoices, type ContractorPayment, type OwnerHome, type OwnerInvoice, type StudioStep, type StudioTurnCost } from "../../api";
import { inTour } from "../../demo";
import { collectionsAssistant, modelLabel, setAssistantCategories, STUDIO_AI_TEXT, toolDisplay } from "./studioAssistant";
import { contabuWidgets } from "./studioWidgets";
import AiTrace, { type TraceStep } from "./AiTrace";
import OwnerChat from "./OwnerChat";

// EL TABLERO DEL DUEÑO CON AG STUDIO (FASE 16, solo en la rama `paypal-contest`: AG Studio es de pago y ContaBu no
// depende de él; Jose, 2-nov y 6-nov). Premio "Best Use of AG Grid" (pide AG Studio, 16.B §10).
// Lo que el dueño ya ve en su portal, en un tablero: sus facturas (Cobros), sus pagos a contratistas y los totales de los
// periodos que cerró su bookkeeper (el Resumen). NO hay ruta nueva: los datos vienen de las mismas rutas del portal, con
// sus mismos permisos. AG Studio solo suma y agrupa en el navegador lo que esas rutas ya entregan (el motor de AG Studio,
// no la IA). Las transacciones del libro nunca llegan aquí (regla del portal: totales, nunca transacciones).
// La llave de la licencia: `VITE_AG_STUDIO_LICENSE`, fuera de git. Sin llave corre con la marca de agua de prueba.

const LICENSE = (import.meta.env.VITE_AG_STUDIO_LICENSE as string | undefined) || undefined;

// PayPal: azul de marca y el amarillo del botón; el resto, la tipografía de ContaBu.
// 9-nov (revisión externa): el tercer color es el de "Vencido" en "Facturas por cliente": rojo, como en todo el portal.
// 🔄 10-nov (Jose, opción A): los colores del Resumen. AG Studio usa UNA paleta para todo el tablero, por posición de la
// serie: 1 = verde (ingresos, cobrado), 2 = ladrillo (costos, vencido), 3 = azul de PayPal (por cobrar, aún no vence).
// Validada con `validate_palette.js` (las tres, todos los pares): PASS, con el par verde/ladrillo en la franja 6–8 de
// daltonismo, legal porque cada serie tiene leyenda con nombre y tooltip.
const PALETTE = ["#059669", "#D9534F", "#0070BA", "#FFC439", "#2E7D32", "#7B61FF", "#E0699B", "#003087", "#00A3A3", "#F28C28"];
// Opción de Jose (9-nov): un marco verde de ContaBu (#15803d) con 70 % de transparencia, para distinguir el tablero.
const STUDIO_FRAME: string | false = "1px solid rgba(21, 128, 61, 0.3)";
const theme = studioTheme.withParams({
  accentColor: "#0070BA",
  fontFamily: "inherit",
  browserColorScheme: "light",
  // 8-nov (rediseño): que se vea como el portal: fondo slate-50, tarjetas blancas redondeadas, títulos pequeños.
  backgroundColor: "#FFFFFF",   // 9-nov: las tarjetas, blancas como las del portal (el fondo slate-50 es el de la página)
  // 9-nov (revisión externa: "el tablero se ve ajeno, con marco gris"): sin el marco de AG Studio (#E8EBED con borde) ni su
  // lienzo gris (#EFF1F3); las tarjetas quedan directo sobre el fondo del portal.
  // 🔄 9-nov (Jose): el fondo del tablero, del mismo blanco de las tarjetas, para que no se vea como un fondo aparte.
  studioWrapperBackgroundColor: "#FFFFFF",
  studioWrapperBorder: STUDIO_FRAME,
  studioWrapperBorderRadius: 12,
  studioWrapperSpacing: 0,
  studioCanvasBackgroundColor: "#FFFFFF",
  studioCanvasBorder: false,
  studioCanvasShadow: false,
  studioWidgetTitleFontSize: 15,
  studioWidgetTitleFontWeight: 600,
  studioWidgetTitleTextColor: "#0F172A",
  studioWidgetBorderRadius: 12,
  studioWidgetBorder: "1px solid #E2E8F0",
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
const TOP_CUSTOMERS = 10;    // "Facturas por cliente": los 10 que más facturaron, el resto en "Otros"
const BUTTONS_UP_TO = 6;     // hasta 6 clientes, botones; con más, nuestro buscador (`contabu-customer-search`)
const MOBILE_TRACKS = 3 + 5 * 4 + 2 * 13;   // en el celular, uno debajo del otro (sin la tabla, que se suma aparte)

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
// "jul 2026" para el eje de meses (8-nov: como fecha, AG Studio lo dibujaba como una línea de tiempo continua).
const monthLabelOf = (ym: string) => new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1, 1)
  .toLocaleDateString(document.documentElement.lang?.startsWith("es") ? "es-US" : "en-US", { month: "short", year: "numeric" });
const monthStart = (ym: string) => new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)) - 1, 1);   // 8-nov: el mes como fecha

// 8-nov (revisión como cliente): AG Studio no tiene opción para estas tres cosas, así que van en CSS sobre sus clases.
// - Un saludo en el chat vacío (antes: un panel en blanco). - Sin el selector de conversaciones viejas (sobraba; "Nuevo"
//   se queda). - El encabezado del panel no lo pliega (plegado, el chat desaparecía sin una forma clara de abrirlo).
const STUDIO_CSS = `
.contabu-studio .ag-studio-button-filter-widget .ag-studio-filter-widget-title { display: none; }
/* 10-nov (Jose): sin efecto al pasar el mouse por las tarjetas (se probó borde, sombra y tinte; ninguno se quedó). El foco
   de AG Studio (al tocar una tarjeta), verde y curvo, en vez del recuadro azul de esquinas rectas. */
.contabu-studio .ag-studio-layout-widget { border-radius: 12px; }
.contabu-studio .ag-studio-layout-widget:focus-within, .contabu-studio .ag-studio-layout-widget:focus {
  box-shadow: inset 0 0 0 1px rgba(21, 128, 61, .55) !important; outline: none; }
/* El chat propio (OwnerChat) flota abajo a la derecha, encima de los gráficos (8-nov). */
.contabu-chat { right: 20px; bottom: 88px; width: min(380px, calc(100vw - 24px)); height: min(560px, calc(100vh - 150px)); }
@media (max-width: 640px) { .contabu-chat { right: 12px; bottom: 84px; height: min(520px, calc(100vh - 140px)); } }
/* "Lo que hizo la IA" flota siempre, fuera de la página (8-nov: en pantallas angostas iba en la página, encima del tablero,
   y al aparecer o al cerrar el chat empujaba todo: el "salto" que vio Jose). Ancho: a la izquierda del chat; angosto: encima. */
.contabu-trace { position: fixed; z-index: 60; overflow: auto; border-radius: 12px; background: #FFFFFF;
  box-shadow: 0 16px 48px rgba(15, 23, 42, .18); }
@media (min-width: 900px) {
  .contabu-trace { right: 412px; bottom: 88px; width: 320px; max-height: min(560px, calc(100vh - 150px)); }
}
@media (max-width: 899px) {
  .contabu-trace { right: 20px; width: min(380px, calc(100vw - 24px)); max-height: 22vh;
    bottom: calc(88px + min(560px, calc(100vh - 150px)) + 8px); }
}
@media (max-width: 640px) {
  .contabu-trace { right: 12px; bottom: calc(84px + min(520px, calc(100vh - 140px)) + 8px); }
}
`;

/** `visible` (10-nov, Jose): el asistente atiende en la pantalla donde esté el dueño y conserva la conversación al cambiar
 *  de pantalla. Corre sobre el Agent Framework de AG Studio, que necesita el tablero: fuera de Cobros › Análisis el tablero
 *  sigue montado pero oculto, y solo se ven el botón, el chat y "Lo que hizo la IA". */
export default function OwnerDashboard({ home, openAsk = 0, visible = true }: { home: OwnerHome; openAsk?: number; visible?: boolean }) {
  const { t, i18n } = useTranslation();
  const es = (i18n.language || "en").startsWith("es");
  const [invoices, setInvoices] = useState<OwnerInvoice[] | null>(null);
  const [payments, setPayments] = useState<ContractorPayment[]>([]);
  const [error, setError] = useState(false);
  // El asistente (Agent Framework): solo el dueño en su libro; ni la vista previa del bookkeeper ni la visita (sin IA).
  const canAsk = !home.preview && !inTour();
  // 8-nov (Jose): en la visita el botón también se ve, para que la parada del asistente lo señale; ahí no hay IA, así que al
  // tocarlo solo dice dónde se prueba.
  const showFab = canAsk || inTour();
  const [tourNote, setTourNote] = useState(false);
  const [asking, setAsking] = useState(false);
  // 10-nov: el botón del asistente vive en todo el portal (OwnerApp); la primera vez monta este tablero con el chat abierto.
  useEffect(() => { if (openAsk) setAsking(true); }, [openAsk]);
  // Por cada respuesta (8-nov): se acumulan sus turnos hasta el final y se muestra el resumen, con la revisión de cifras.
  type Answer = { turns: number; ms: number; tokens: number; usd: number; model: string; verified: boolean | null; steps: StudioStep[] };
  const running = useRef<Answer>({ turns: 0, ms: 0, tokens: 0, usd: 0, model: "", verified: null, steps: [] });
  const [last, setLast] = useState<Answer | null>(null);
  const onTurn = useCallback((c: StudioTurnCost) => {
    const r = running.current;
    r.turns += (c.steps ?? []).filter((x) => x.kind === "model").length || 1;
    r.ms += c.latency_ms; r.tokens += c.tokens; r.usd += c.cost_usd ?? 0; r.model = c.model ?? r.model;
    r.steps = [...r.steps, ...(c.steps ?? [])];
    if (c.final) {
      setLast({ ...r, verified: c.verified ?? null });
      running.current = { turns: 0, ms: 0, tokens: 0, usd: 0, model: "", verified: null, steps: [] };
    }
  }, []);
  const tools = useMemo(() => toolDisplay(t), [t]);
  const toolName = (n: string) => (t(`owner.dashboard.ai.tool.${n === "customer_totals" ? "customerTotals" : n === "contractor_totals" ? "contractorTotals"
    : n === "month_totals" ? "monthTotals" : n === "category_totals" ? "categoryTotals" : n === "reminder_candidates" ? "reminderCandidates"
    : n === "open_collections" ? "openCollections" : `studio.${n}`}`, { name: n, defaultValue: n }));
  const [harness, setHarness] = useState<AgAiHarness | null>(null);
  // AG Studio solo arma el harness cuando abre su propio panel (modo edición). El tablero se queda en modo vista, así que
  // lo armamos nosotros con la misma función en cuanto su API está lista (`onApiReady`).
  // Las categorías del negocio, para que en español el asistente las nombre como la pantalla (10-nov).
  setAssistantCategories([...new Set(home.closed_months.flatMap((m) => [...m.in, ...m.out].map((c) => c.category)))]);
  const ai = useMemo(() => collectionsAssistant(es ? "es" : "en", onTurn), [es, onTurn]);

  useEffect(() => {
    getInvoices().then((v) => setInvoices(v.invoices)).catch(() => setError(true));
    getContractors().then((v) => setPayments(v.enabled ? v.payments : [])).catch(() => setPayments([]));
  }, []);

  // Los datos: tres tablas de filas planas, todas con cifras que ya calculó el código (SQL).
  const data = useMemo(() => {
    if (!invoices) return null;
    const inv0 = invoices.filter((i) => i.status !== "DRAFT").map((i) => {
      const status = statusOf(i);
      const amount = num(i.amount);
      return {
        number: i.number ?? "—", customer: i.customer?.name ?? "—", concept: i.concept, status,
        invoice_id: String(i.id),   // 10-nov: oculto, ligado a la columna Estado para el botón "Recordar" de la tabla
        // 8-nov (revisión como cliente): la tabla mostraba "Overdue" y "2026-11" en la pantalla en español.
        status_label: t(`owner.dashboard.status.${status}`, { defaultValue: status }),
        due_label: i.due_date ? monthLabelOf(i.due_date.slice(0, 7)) : "—",
        amount,
        // 9-nov (deuda #45): una abierta con pagos parciales debe solo lo que falta; lo pagado ya cuenta como cobrado.
        collected: status === "Paid" ? amount : num(i.paid_so_far ?? "0"),
        open: status === "Open" ? amount - num(i.paid_so_far ?? "0") : 0,
        overdue: status === "Overdue" ? amount - num(i.paid_so_far ?? "0") : 0,
        // Días de atraso (8-nov): los calcula el código, para "Quién me debe".
        // Igual que `reminder_candidates` (fecha de hoy contra la de vencimiento, sin horas): las dos cifras coinciden.
        days_overdue: status === "Overdue" ? Math.max(0, Math.round((Date.parse(new Date().toISOString().slice(0, 10)) - Date.parse(i.due_date)) / 86_400_000)) : 0,
        due_month: month(i.due_date), paid_month: month(i.paid_at),
        paid_month_name: monthName(month(i.paid_at)), year: yearOf(month(i.paid_at) ?? month(i.due_date)),
      };
    });
    // 8-nov (muchos clientes): "Facturas por cliente" muestra los 10 que más facturaron y junta el resto en "Otros"; el
    // código decide quiénes son (suma de lo facturado), no el gráfico.
    const billed = new Map<string, number>();
    for (const r of inv0) billed.set(r.customer, (billed.get(r.customer) ?? 0) + r.amount);
    const top = new Set([...billed.entries()].sort((a, b) => b[1] - a[1]).slice(0, TOP_CUSTOMERS).map(([c]) => c));
    const others = t("owner.dashboard.others");
    const inv = inv0.map((r) => ({ ...r, customer_group: top.has(r.customer) ? r.customer : others }));
    const closed = [...home.closed_months].sort((a, b) => a.month.localeCompare(b.month));
    // 8-nov (revisión como cliente): los nombres largos se cortaban en el eje; en el gráfico va la parte antes del guion.
    const catLabel = (c: string) => {
      const full = t(`cat.${c}`, { defaultValue: c });
      return full.length > 24 ? full.split(/ [—–-] /)[0] : full;
    };
    // 8-nov (revisión como cliente): "Returns and allowances" salía en "Mayores costos" (con $0), porque la tabla de libros
    // también tiene las filas de ingresos. Los costos van en su propia tabla, solo con las filas de costos.
    const spend = closed.flatMap((m) => m.out.map((c) => ({ month: m.month, category: c.category, category_label: catLabel(c.category), costs: num(c.amount) })));
    // Lo Pendiente de clasificar de cada mes cerrado (regla 4: se declara, nunca se omite).
    const periods = closed.map((m) => ({ month: m.month, month_label: monthLabelOf(m.month),
      pending_count: m.pending?.count ?? 0, pending_usd: num(m.pending?.money_in) + num(m.pending?.money_out) }));
    const books = closed.flatMap((m) => [
      ...m.in.map((c) => ({ month: m.month, month_label: monthLabelOf(m.month), month_start: monthStart(m.month), month_name: monthName(m.month), year: yearOf(m.month), category: c.category, income: num(c.amount), costs: 0 })),
      ...m.out.map((c) => ({ month: m.month, month_label: monthLabelOf(m.month), month_start: monthStart(m.month), month_name: monthName(m.month), year: yearOf(m.month), category: c.category, income: 0, costs: num(c.amount) })),
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
        + `"contractors": payments to contractors with PayPal Payouts. "costs": costs and expenses by category for each closed month. `
        + `"periods": per closed month, the transactions still pending to classify (not in income or costs). All amounts are USD.`,
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
          { id: "days_overdue", name: t("owner.dashboard.f.daysOverdue"), description: "Days past due for unpaid invoices (0 if not overdue)", ...yr },
          { id: "due_month", name: t("owner.dashboard.f.dueMonth"), ...txt },
          { id: "paid_month", name: t("owner.dashboard.f.paidMonth"), description: "Month the invoice was paid, YYYY-MM", ...txt },
          { id: "paid_month_name", name: t("owner.dashboard.f.paidMonthName"), description: "Month name the invoice was paid, e.g. August (the year is in the year field)", ...txt },
          { id: "year", name: t("owner.dashboard.f.year"), description: "Year of the invoice (paid, or due if unpaid)", ...yr },
          { id: "customer_group", name: t("owner.dashboard.f.customer"), description: "Customer name for the 10 that billed the most; the rest grouped as Others", ...txt },
          // `context.remind` (10-nov): la tabla dibuja en esta columna el botón "Recordar" de las facturas sin pagar.
          { id: "status_label", name: t("owner.dashboard.f.status"), description: "Status as shown on screen (filter with status)", ...txt,
            context: { remind: true } },
          { id: "invoice_id", name: "Invoice id", description: "Internal invoice id (not shown)", ...txt },
          { id: "due_label", name: t("owner.dashboard.f.dueMonth"), description: "Due month as shown on screen", ...txt },
        ] },
        { id: "books", name: t("owner.dashboard.src.books"), data: books, fields: [
          { id: "month", name: t("owner.dashboard.f.month"), description: "Month, YYYY-MM", ...txt },
          { id: "month_name", name: t("owner.dashboard.f.monthName"), description: "Month name only, e.g. August (the year is in the year field)", ...txt },
          { id: "month_label", name: t("owner.dashboard.f.month"), description: "Closed month, e.g. Jul 2026", ...txt },
          { id: "month_start", name: t("owner.dashboard.f.month"), description: "First day of the closed month", format: "dateFormat" as const,
            formatOptions: { format: new Intl.DateTimeFormat(es ? "es-US" : "en-US", { month: "short", year: "numeric" }) } },
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
        { id: "costs", name: t("owner.dashboard.src.costs"), data: spend, fields: [
          { id: "month", name: t("owner.dashboard.f.month"), description: "Month, YYYY-MM", ...txt },
          { id: "category", name: t("owner.dashboard.f.category"), description: "Accounting category (English)", ...txt },
          { id: "category_label", name: t("owner.dashboard.f.category"), description: "Category as shown on screen", ...txt },
          { id: "costs", name: t("owner.dashboard.f.costs"), description: "Costs and expenses in USD for a closed month", ...cur },
        ] },
        { id: "periods", name: t("owner.dashboard.src.periods"), data: periods, fields: [
          { id: "month", name: t("owner.dashboard.f.month"), description: "Month, YYYY-MM", ...txt },
          { id: "month_label", name: t("owner.dashboard.f.month"), description: "Closed month, e.g. Jul 2026", ...txt },
          { id: "pending_count", name: t("owner.dashboard.f.pendingCount"), description: "Transactions still pending to classify", ...yr },
          { id: "pending_usd", name: t("owner.dashboard.f.pendingUsd"), description: "USD still pending to classify (money in plus money out), not in income or costs", ...cur },
        ] },
      ],
    };
  }, [invoices, payments, home, t, es]);

  // 🔄 10-nov (Jose): la tabla vuelve. Es la única tabla de AG Grid del tablero y, a diferencia de la lista de Facturas, se
  // filtra junto con todo al elegir un cliente.
  const LIST_TRACKS = Math.min(20, Math.max(8, Math.ceil(((invoices?.filter((i) => i.status !== "DRAFT").length ?? 8) + 2) * 28 / 22) + 1));
  const TRACKS = 20 + LIST_TRACKS;
  const MANY = new Set((invoices ?? []).filter((i) => i.status !== "DRAFT").map((i) => i.customer?.name)).size > BUTTONS_UP_TO;
  // El tablero ya armado (el dueño no tiene que construir nada); en modo vista: filtra y cruza, no edita.
  // 8-nov (rediseño, revisión de Jose como usuario externo): arriba lo que necesita atención ("Quién me debe" y los
  // indicadores con color), luego la historia (ingresos contra costos, costos por categoría, cobrado por mes) y al final la
  // lista. Un filtro de clientes con botones a la vista reemplaza la pestaña "Filters". Altura total: 48 filas.
  const initialState = useMemo(() => {
    const titled = (key: string) => ({ title: { enabled: true, text: t(`owner.dashboard.w.${key}`) } });
    // 10-nov (Jose): "Por cobrar" y "Vencido" filtran el tablero al tocarlos (por el estado de la factura).
    const kpi = (field: string, byStatus = false) => ({ type: "contabu-kpi", dataMapping: {
      value: [{ id: field, aggregation: "sum" }], ...(byStatus ? { status: [{ id: "invoices.status" }] } : {}) } });
    const chart = (key: string, extra: object = {}) => ({
      ...titled(key),
      // 8-nov: AG Studio no deja dar formato a los números del eje ("20K"): el eje dice en qué moneda está.
      style: { theme: { common: { legend: { enabled: true, position: "bottom" } } },
               custom: { valueAxis: { title: { enabled: true, text: "USD", fontSize: 11, color: "#64748B" } } } },
      ...extra,
    });
    return {
      pages: [{
        id: "collections",
        layout: { minWidth: 320, rowHeight: 22 },   // sin minWidth el lienzo mide 720 px y en el celular se sale
        widgets: {
          // 8-nov (revisión como cliente): sin título ni "Filtrar por: Cliente" (los nombres ya dicen qué son), botones redondos.
          // Con muchos clientes, nuestro buscador (el filtro de lista de AG Studio es una lista vertical: no cabe aquí).
          customers: MANY ? {
            type: "contabu-customer-search",
            dataMapping: { customer: [{ id: "invoices.customer" }], billed: [{ id: "invoices.amount", aggregation: "sum" }] },
          } : { type: "button-filter", dataMapping: { value: [{ id: "invoices.customer" }] },
                       format: { title: { enabled: false }, subtitle: { enabled: false }, caption: { enabled: false },
                                 style: { selection: { type: "single" }, dimensions: { height: 34 },
                                          defaultItem: { backgroundColor: "#FFFFFF", borderEnabled: true, borderColor: "#CBD5E1", borderRadius: 999, color: "#334155" },
                                          activeItem: { backgroundColor: "#0070BA", color: "#FFFFFF", borderColor: "#0070BA" } } } },
          collected: kpi("invoices.collected"),
          open: kpi("invoices.open", true),
          overdue: kpi("invoices.overdue", true),
          contractors: kpi("contractors.amount"),
          pending: kpi("periods.pending_usd"),
          whoOwes: {
            type: "contabu-who-owes",
            dataMapping: { customer: [{ id: "invoices.customer" }], open: [{ id: "invoices.open", aggregation: "sum" }],
                           overdue: [{ id: "invoices.overdue", aggregation: "sum" }], days_overdue: [{ id: "invoices.days_overdue", aggregation: "max" }] },
          },
          // 10-nov (Jose): sin "Ingresos contra costos por mes" ni "Mayores costos por categoría". Las dos ya están en el
          // Resumen y salen de los libros cerrados (todo el negocio), así que no respondían al filtro por cliente: aquí queda
          // solo lo que sale de las facturas y los pagos, y todo se filtra junto.
          byCustomer: {
            type: "column-chart-grouped",
            dataMapping: { categoryKey: [{ id: "invoices.customer_group" }],
                           // 10-nov: Cobrado, Vencido, Por cobrar (el orden da el color: verde, ladrillo, azul).
                           valueKey: [{ id: "invoices.collected", aggregation: "sum" }, { id: "invoices.overdue", aggregation: "sum" },
                                      { id: "invoices.open", aggregation: "sum" }], tooltipKey: [] },
            format: chart("byCustomer"),
          },
          list: {
            type: "grid",
            dataMapping: { cols: [{ id: "invoices.number" }, { id: "invoices.customer" }, { id: "invoices.concept" },
                                  { id: "invoices.status_label" }, { id: "invoices.due_label" }, { id: "invoices.amount", aggregation: "sum" }] },
            format: titled("list"),
          },
        },
        widgetLayout: {
          customers: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 3 },   // sin "Filtrar por" caben en 3 pistas
          collected: { xTrack: 0, yTrack: 3, xSpan: 5, ySpan: 4 },
          open: { xTrack: 5, yTrack: 3, xSpan: 5, ySpan: 4 },
          overdue: { xTrack: 10, yTrack: 3, xSpan: 5, ySpan: 4 },
          contractors: { xTrack: 15, yTrack: 3, xSpan: 5, ySpan: 4 },
          pending: { xTrack: 20, yTrack: 3, xSpan: 4, ySpan: 4 },
          whoOwes: { xTrack: 0, yTrack: 7, xSpan: 9, ySpan: 13 },
          byCustomer: { xTrack: 9, yTrack: 7, xSpan: 15, ySpan: 13 },
          list: { xTrack: 0, yTrack: 20, xSpan: 24, ySpan: LIST_TRACKS },
        },
      }],
      selectedPageId: "collections",
      // 8-nov: el chat siempre abierto (plegado, el dueño lo perdía: no se ve dónde se vuelve a abrir).
      panels: { filters: { collapsed: true }, ai: { collapsed: false } },
    } as unknown as AgReportState;
  }, [t, LIST_TRACKS, MANY]);
  const widgets = useMemo(() => contabuWidgets({ canRemind: !home.preview && !inTour() }), [es, home.preview]);   // los nombres del menú de edición, en el idioma de la pantalla
  // 8-nov: sin "(sum)" / "(suma)" en las leyendas y los tooltips: todas las cifras del tablero son sumas.
  const localeText = useMemo(() => ({ ...(es ? AG_STUDIO_LOCALE_ES_ES : {}), aggregationSumFieldName: "${name}",
                                       aggregationMaxFieldName: "${name}" }), [es]);

  return (
    <>
      <style>{STUDIO_CSS}</style>
      <div hidden={!visible} className={visible ? "space-y-2" : "hidden"}>
        {error ? <p className="text-sm text-rose-600">{t("owner.error")}</p>
          : !data ? <div className="flex justify-center py-16"><span className="loading loading-spinner loading-lg text-brand-green" /></div>
          : (
            <>
              <p className="text-sm text-slate-600">{t("owner.dashboard.intro")}</p>
              {/* `key`: AG Studio fija el idioma al crearse; al cambiarlo se vuelve a crear. El chat de AG Studio solo existe en
                  el modo de edición: con el asistente abierto se pasa a ese modo, pero solo con el panel del chat. */}
              <div className="contabu-studio overflow-hidden" style={{ height: NARROW ? MOBILE_TRACKS * 22 + 60 + LIST_TRACKS * 22 : TRACKS * 22 + 50, minHeight: 420 }}>
                <AgStudioProvider licenseKey={LICENSE} modules={[AgStudioAiModule]}>
                  <AgStudio key={es ? "es" : "en"} data={data as never} initialState={initialState} mode="view" theme={theme} widgets={widgets}
                            onApiReady={canAsk ? (e: { api: unknown }) => setHarness((ai as (p: { api: unknown }) => AgAiHarness)({ api: e.api })) : undefined}
                            localeText={localeText} ai={canAsk ? ai : undefined} aiText={STUDIO_AI_TEXT} aiToolDisplay={tools}
                            responsiveLayoutBreakpoints={RESPONSIVE}
                            panels={{ view: { left: [], right: [] } }}
                            style={{ height: "100%", width: "100%" }} />
                </AgStudioProvider>
              </div>
              <p className="text-[11px] text-slate-400">{t("owner.dashboard.note")}</p>
            </>
          )}
      </div>
      {/* 8-nov (revisión como cliente): antes quedaba al fondo de la página, lejos del chat; ahora arriba, sobre el chat. */}
      {asking && last && (
        <div className="contabu-trace">
          <AiTrace ai={{ model: modelLabel(last.model), latency_ms: last.ms, tokens: last.tokens, cost_usd: last.usd }}
                   steps={last.steps.map((x): TraceStep =>
                     x.kind === "model"
                       ? { label: t("owner.dashboard.ai.step.model", { model: modelLabel(x.model), s: (x.ms / 1000).toFixed(1) }),
                           detail: x.tools.length ? t("owner.dashboard.ai.step.asked", { tools: x.tools.map((n) => toolName(n)).join(", ") })
                                                  : t("owner.dashboard.ai.step.wrote") }
                       : x.kind === "sql"
                         ? { label: t("owner.dashboard.ai.step.sql", { tool: toolName(x.tool), ms: x.ms }) }
                         : x.unverified.length
                           ? { label: t("owner.dashboard.ai.step.checkBad", { figures: x.unverified.join(", ") }), tone: "warn" }
                           : x.figures ? { label: t("owner.dashboard.ai.step.checkOk", { count: x.figures }) }
                                       : { label: t("owner.dashboard.ai.step.checkNone") })} />
        </div>
      )}
      {/* Se oculta al cerrarlo, no se desmonta: la conversación sigue ahí al volver a abrirlo. */}
      {canAsk && <OwnerChat open={asking} harness={harness} toolName={toolName} onClose={() => setAsking(false)} />}
      {showFab && !canAsk && visible && tourNote && (
        <div role="status" className="fixed bottom-24 right-5 z-[61] max-w-xs rounded-xl bg-white p-3 text-sm text-slate-700 shadow-lg ring-1 ring-slate-900/10">
          {t("owner.dashboard.ai.tourOnly")}
        </div>
      )}
      {showFab && (canAsk || visible) && (
        <button data-tour="pp-assistant" onClick={() => (canAsk ? setAsking((a) => !a) : setTourNote((x) => !x))} aria-expanded={asking}
                aria-label={asking ? t("owner.dashboard.ai.close") : t("owner.dashboard.ai.open")}
                title={asking ? t("owner.dashboard.ai.close") : t("owner.dashboard.ai.open")}
                className={`fixed bottom-5 right-5 z-[61] flex h-14 items-center gap-2 rounded-full shadow-lg transition-all
                            ${asking ? "w-14 justify-center bg-[#003087] text-white" : "px-5 bg-[#FFC439] text-[#003087] hover:bg-[#f2ba36]"}`}>
          {asking ? (
            <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round"><path d="M6 6l12 12M18 6 6 18" /></svg>
          ) : (
            <>
              <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /><path d="M8.5 11h.01M12 11h.01M15.5 11h.01" />
              </svg>
              <span className="hidden sm:inline text-sm font-semibold">{t("owner.dashboard.ai.fab")}</span>
            </>
          )}
        </button>
      )}
    </>
  );
}
