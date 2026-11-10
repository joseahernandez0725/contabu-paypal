import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgCharts, AllCommunityModule, ModuleRegistry, type AgCartesianChartOptions, type AgChartInstance } from "ag-charts-community";
import { money, type OwnerYear as Year } from "../../api";

// LAS GRÁFICAS DEL RESUMEN, CON AG CHARTS (10-nov, Jose: "no me gusta cómo se ven"; el Resumen abre la visita guiada).
// AG Charts es la librería de gráficas sobre la que corre AG Studio (la ventana Análisis): las dos pantallas se ven como
// un solo producto. Todo número viene del backend (SQL sobre las fotos de los meses cerrados); aquí solo se dibuja.
// Guía de gráficos (skill dataviz): una escala, columnas y barras delgadas con la punta redondeada, rejilla recesiva (sin crosshair: es de AG Charts Enterprise; el tooltip compartido marca el mes), etiquetas directas solo donde ayudan, tooltip al pasar el mouse y la tabla aparte.
// Paleta validada con `validate_palette.js` (10-nov, elegida por Jose: verde y rojo): ingresos = verde #059669, costos =
// rojo #DC2626. Todo PASS, pero el par rojo/verde queda justo sobre el piso de daltonismo (ΔE 8.6 deutan): por eso cada
// serie lleva su nombre en la leyenda y sus cifras escritas, nunca solo el color. Más claros (#16A34A/#EF4444) NO pasan.
export const IN = "#059669";
// 🔄 10-nov (Jose: "un rojo más tenue"): #D9534F, un rojo ladrillo. Con el verde queda en la franja 6–8 de daltonismo
// (ΔE 6.8 deutan, WARN): es legal solo con otra señal además del color, y la tiene (leyenda con nombre, el espacio entre
// las dos columnas del mes, cifras escritas y tooltip). Rojos más claros (#E06666, #E57373, #EF6461) NO pasan.
export const OUT = "#D9534F";
// Un degradado sutil (Jose, 10-nov: "más como degradé, solo un poco"): el color de la serie en la punta de la barra y
// un 25 % más claro (mezclado con blanco) en la base. La muestra de la leyenda sigue sólida, con el color de la punta.
const lighter = (hex: string, k = 0.25) => "#" + [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * (1 - k) + 255 * k)
  .toString(16).padStart(2, "0")).join("");
const fade = (color: string, rotation: number) => ({ type: "gradient" as const, rotation,
  colorStops: [{ color: lighter(color), stop: 0 }, { color, stop: 1 }] });
// Al pasar el mouse por una barra, las demás NO se vuelven traslúcidas (Jose, 10-nov): el tooltip ya dice cuál es.
const NO_DIM = { unhighlightedItem: { opacity: 1 }, unhighlightedSeries: { opacity: 1 } };
const INK = "#334155", MUTED = "#64748B", GRID = "#E2E8F0";

ModuleRegistry.registerModules([AllCommunityModule]);

const compact = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });

function fontFamily() {
  return typeof document === "undefined" ? "system-ui, sans-serif" : getComputedStyle(document.body).fontFamily;
}

/** Un gráfico de AG Charts dentro de React: se crea una vez y se actualiza con las opciones nuevas. */
function AgChart({ options, height, label }: { options: AgCartesianChartOptions; height: number; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<AgChartInstance | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const opts = { ...options, container: ref.current } as AgCartesianChartOptions;
    if (chart.current) void chart.current.update(opts);
    else chart.current = AgCharts.create(opts);
  }, [options]);
  useEffect(() => () => { chart.current?.destroy(); chart.current = null; }, []);
  return <div ref={ref} style={{ height }} role="img" aria-label={label} />;
}

const base = (): Partial<AgCartesianChartOptions> => ({
  background: { fill: "transparent" },
  theme: { params: { fontFamily: fontFamily(), fontSize: 12, foregroundColor: INK } },
  legend: { enabled: false },   // la leyenda va en el encabezado de la tarjeta (HTML), con la misma muestra de color
});

/** ¿Pantalla angosta (celular)? Se actualiza al girar o cambiar el tamaño. */
function useNarrow() {
  const q = "(max-width: 480px)";
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setNarrow(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return narrow;
}

/** Los meses cerrados del año (opción C, Jose 10-nov): por cada mes, ingresos y costos en columnas lado a lado, y encima
 *  de cada par lo ACUMULADO del año hasta ese mes ("Σ ingresos · costos"). Solo los meses cerrados: el eje hasta
 *  diciembre dejaba media gráfica vacía. Las columnas, delgadas aunque haya pocos meses (el ancho se calcula con el de
 *  la tarjeta). Una sola escala: la etiqueta del acumulado es texto, no una segunda serie con otro eje. */
export function MonthsChart({ months, lang }: { months: Year["months"]; lang: string }) {
  const { t } = useTranslation();
  const [box, width] = useWidth();
  const options = useMemo<AgCartesianChartOptions>(() => {
    const data = [...months].sort((a, b) => a.month.localeCompare(b.month)).map((m) => {
      const d = new Date(Number(m.month.slice(0, 4)), Number(m.month.slice(5, 7)) - 1, 1);
      const inn = Number(m.total_in), out = Number(m.total_out);
      return {
        label: d.toLocaleDateString(lang, { month: "short" }).replace(".", ""),
        full: d.toLocaleDateString(lang, { month: "long", year: "numeric" }),
        in: inn, out, cum_in: Number(m.cum_in), cum_out: Number(m.cum_out), top: Math.max(inn, out, 0),
      };
    });
    type D = (typeof data)[number];
    // Columnas de ~30 px y 6 px entre las dos del mes, aunque haya 3 meses en una tarjeta ancha.
    const band = Math.max(1, (width - 70) / Math.max(1, data.length));
    const group = Math.min(band * 0.7, 2 * 30 + 6);
    const tip = (d: D) => ({
      heading: d.full,
      data: [{ label: `${t("owner.summary.in")} · ${t("owner.year.thisMonth")}`, value: money(d.in) },
             { label: `${t("owner.summary.out")} · ${t("owner.year.thisMonth")}`, value: money(d.out) },
             { label: `${t("owner.summary.in")} · ${t("owner.year.soFar")}`, value: money(d.cum_in) },
             { label: `${t("owner.summary.out")} · ${t("owner.year.soFar")}`, value: money(d.cum_out) }],
    });
    const column = (key: "in" | "out", color: string, name: string) => ({
      type: "bar" as const, xKey: "label", yKey: key, yName: name, fill: fade(color, 180), strokeWidth: 0, cornerRadius: 4, highlight: NO_DIM,
      tooltip: { renderer: ({ datum }: { datum: D }) => tip(datum) },
    });
    return {
      ...base(),
      data,
      padding: { top: 26, right: 8, bottom: 4, left: 4 },
      series: [
        column("in", IN, t("owner.summary.in")), column("out", OUT, t("owner.summary.out")),
        // El acumulado, escrito sobre el par: una línea invisible a la altura de la columna más alta, solo por su etiqueta.
        { type: "line", xKey: "label", yKey: "top", yName: "Σ", stroke: "transparent", strokeWidth: 0,
          marker: { enabled: false }, tooltip: { enabled: false }, showInLegend: false, highlight: NO_DIM,
          label: { enabled: true, placement: "top", color: INK, fontSize: 11, fontWeight: 600,
                   formatter: ({ datum }: { datum: D }) => `Σ ${compact(datum.cum_in)} · ${compact(datum.cum_out)}` } },
      ],
      axes: {
        x: { type: "category", crosshair: { enabled: false }, position: "bottom", line: { enabled: false },
             paddingInner: Math.min(0.85, Math.max(0.2, 1 - group / band)), groupPaddingInner: 0.1,
             label: { color: MUTED, fontSize: 12 }, gridLine: { enabled: false } },
        y: { type: "number", crosshair: { enabled: false }, position: "left", nice: true, line: { enabled: false },
             label: { color: MUTED, fontSize: 11, formatter: ({ value }: { value: number }) => compact(value) },
             gridLine: { style: [{ stroke: GRID, lineDash: [3, 3] }] } },
      },
    } as AgCartesianChartOptions;
  }, [months, lang, t, width]);
  return <div ref={box}>{width > 0 && <AgChart options={options} height={240} label={t("owner.year.cumulative")} />}</div>;
}

/** El ancho de una caja (para que las columnas no se engorden con pocos meses). */
function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** El año por categoría, en barras horizontales ordenadas de mayor a menor (un solo color por gráfico). La cifra va al
 *  final de cada barra, así que el eje numérico sobra. En español, el nombre en español; el término contable en inglés,
 *  en el tooltip (el dueño no es contador). Una categoría en negativo (una devolución) crece hacia la izquierda. */
type CatRow = { category: string; amount: string | number; count?: number };
/** `height` fijo: el widget de Análisis (10-nov) le da el alto de su caja. */
export function CategoryBars({ rows, color, label, height }: { rows: CatRow[]; color: string; label: string; height?: number }) {
  const { t, i18n } = useTranslation();
  const es = (i18n.language || "en").split("-")[0] !== "en";
  // En el celular (10-nov, visto a 375 px) los nombres se cortaban a "Renta o arren…": ahí van en dos líneas.
  const narrow = useNarrow();
  const options = useMemo<AgCartesianChartOptions>(() => {
    const data = [...rows].sort((a, b) => Number(b.amount) - Number(a.amount)).map((r) => {
      const name = (es && t(`cat.${r.category}`, { defaultValue: "" })) || r.category;
      return { name, full: name, en: r.category, amount: Number(r.amount), count: r.count };
    });
    type D = (typeof data)[number];
    return {
      ...base(),
      data,
      padding: { top: 4, right: 76, bottom: 4, left: 4 },
      series: [{
        type: "bar", direction: "horizontal", xKey: "name", yKey: "amount", yName: label,
        fill: fade(color, 90), strokeWidth: 0, cornerRadius: 4, highlight: NO_DIM,
        label: { enabled: true, placement: "outside-end", color: INK, fontSize: 12, fontWeight: 600,
                 formatter: ({ value }: { value: number }) => money(value) },
        tooltip: {
          renderer: ({ datum }: { datum: D }) => ({
            heading: datum.full,
            data: [{ label: datum.en === datum.full ? label : datum.en, value: money(datum.amount) },
                   ...(datum.count != null ? [{ label: t("owner.year.txLabel"), value: String(datum.count) }] : [])],
          }),
        },
      }],
      axes: {
        x: { type: "category", crosshair: { enabled: false }, position: "left", line: { enabled: false }, paddingInner: 0.35, paddingOuter: 0.15,
             label: { color: INK, fontSize: narrow ? 11 : 12, maxWidth: narrow ? 118 : 170, maxHeight: narrow ? 30 : 18,
                      wrapping: narrow ? "on-space" : "never", truncate: true, avoidCollisions: false },
             gridLine: { enabled: false } },
        y: { type: "number", crosshair: { enabled: false }, position: "bottom", line: { enabled: false }, label: { enabled: false },
             gridLine: { enabled: false } },
      },
    } as AgCartesianChartOptions;
  }, [rows, es, t, label, color, narrow]);
  if (rows.length === 0) return <p className="text-sm text-slate-400">{t("owner.summary.nothing")}</p>;
  // Alto por fila fijo: con pocas categorías las barras no se engordan (10-nov: "Ingresos por categoría" tiene 2).
  return <AgChart options={options} height={height ?? rows.length * (narrow ? 42 : 36) + 12} label={label} />;
}
