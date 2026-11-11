import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { goLive, inTour } from "../../demo";
import type { View } from "../Sidebar";
import { bubblePosition, SpotlightBackdrop, useHole, useViewport, waitFor } from "../Spotlight";
import AiTrace from "./AiTrace";
import type { OwnerView } from "./OwnerSidebar";
import PayPalIntro from "./PayPalIntro";

// LA GUÍA DE `paypal.contabu.com` (FASE 16, Jose 5-nov: "una visita guiada parecida a la de Nebius"). La pantalla de
// inicio (`PayPalIntro`) y sus paradas con el MISMO foco que `demo.contabu.com` (`Spotlight.tsx`): la pantalla se oscurece,
// se ilumina lo que se explica y al lado va una burbuja. Recorre el portal del dueño y dos pantallas de la consola del
// bookkeeper. La visita NO llama a la IA (Jose, 5-nov) pero SÍ la muestra (6-nov): una parada con una corrida real del
// dictado y otra con el equipo de agentes, con las cifras medidas. Todo lo iluminado lleva tapón: se avanza con Next.
// En la visita se abre sola una vez por carga; en cualquier momento, con el botón "Guide" de la barra de arriba.

export const OPEN_PAYPAL_GUIDE = "contabu:paypal-guide-open";
export function openPayPalGuide() { window.dispatchEvent(new Event(OPEN_PAYPAL_GUIDE)); }

/** El botón de la barra de arriba (portal y consola) que reinicia la guía (Jose, 6-nov). */
export function GuideButton() {
  const { t } = useTranslation();
  return (
    <button onClick={openPayPalGuide}
            className="shrink-0 rounded-full border border-[#0070BA] px-3 py-1 text-xs font-semibold text-[#0070BA] hover:bg-[#009CDE]/10">
      {t("owner.guide.open")}
    </button>
  );
}

// `click`: lo que hay que tocar antes de iluminar (una pestaña dentro de la pantalla).
type Stop = { key: string; owner?: OwnerView; console?: View; click?: string; sel?: string };
// 🔄 6-nov (Jose): sin la parada de los bancos (mencionaba Plaid) · + la IA del dictado, los contratistas y el equipo de IA.
const STOPS: Stop[] = [
  { key: "year", owner: "summary", sel: '[data-tour="pp-year"]' },
  { key: "categories", owner: "summary", sel: '[data-tour="pp-categories"]' },
  { key: "collected", owner: "invoices", sel: '[data-tour="pp-collected"]' },
  { key: "paid", owner: "invoices", sel: '[data-tour="pp-paid-invoice"]' },
  { key: "newInvoice", owner: "invoices", sel: '[data-tour="pp-new-invoice"]' },
  { key: "ai" },
  // 8-nov (Jose): el panel Análisis (AG Studio) y una respuesta medida de su asistente (la visita no llama a la IA).
  { key: "insights", owner: "dashboard", sel: '[data-tour="pp-owes"]' },
  { key: "insightsAi", owner: "dashboard", sel: '[data-tour="pp-assistant"]' },
  { key: "contractors", owner: "contractors", sel: '[data-tour="pp-contractors"]' },
  { key: "paypal", owner: "accounts", sel: '[data-tour="pp-paypal-card"]' },
  { key: "bank", console: "upload", sel: '[data-tour="pp-bank"]' },
  { key: "close", console: "settings", sel: '[data-tour="pp-close"]' },
  // 🔄 6-nov (Jose): la hoja de reglas del libro (lo que de verdad categoriza) y, después, el equipo de IA en Auditoría,
  // con la revisión que se guardó al sembrar (`demo.py reseed --review`).
  { key: "rules", console: "transactions", click: '[data-tour="rules-tab"]', sel: '[data-tour="rules-formula"]' },
  { key: "team", console: "audit", sel: '[data-tour="ai-team"]' },
  { key: "live" },
];

// Una corrida REAL del dictado (6-nov, Nemotron 3 Nano: 5 dictados entre 0.6 y 1.3 s, mediana 0.9 s; 543–593 tokens;
// $0.00004–0.00005). La visita no llama a la IA: muestra esta, con el mismo panel que ve el dueño en su libro.
const SAMPLE_AI = { model: "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", latency_ms: 945, tokens: 587, cost_usd: 0.000045 };
// Una respuesta REAL del asistente del panel Análisis (8-nov, "¿A quién le recuerdo primero?"): 4.1 s, 25.304 tokens,
// $0.00510; el modelo pidió la lista ordenada por el código (2.4 s), redactó (1.7 s) y el código revisó sus 2 cifras.
const SAMPLE_INSIGHTS = { model: "Qwen/Qwen3-235B-A22B-Instruct-2507", latency_ms: 4100, tokens: 25304, cost_usd: 0.0051 };

let introShownThisLoad = false;   // en la visita: la bienvenida sale una vez por carga (todos los jueces empiezan igual)

export default function PayPalGuide({ goOwner, goConsole }: {
  goOwner: (v: OwnerView) => void;
  goConsole: (v: View) => void;
}) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<"intro" | number | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const hole = useHole(target);
  const vp = useViewport();
  const bubbleRef = useRef<HTMLDivElement>(null);
  const [bubbleH, setBubbleH] = useState(180);
  // Las funciones de App cambian en cada pintada: en refs, para no re-disparar el efecto de las paradas.
  const nav = useRef({ goOwner, goConsole });
  nav.current = { goOwner, goConsole };

  useEffect(() => {
    if (inTour() && !introShownThisLoad) { introShownThisLoad = true; setPhase("intro"); }
    const open = () => setPhase("intro");
    window.addEventListener(OPEN_PAYPAL_GUIDE, open);
    return () => window.removeEventListener(OPEN_PAYPAL_GUIDE, open);
  }, []);
  // Al cerrar a mitad o al final, de vuelta al Resumen del dueño (lo que se concursa), no en la consola.
  const close = useCallback(() => {
    if (typeof phase === "number") nav.current.goOwner("summary");
    setPhase(null);
    setTarget(null);
  }, [phase]);
  const next = useCallback(() => setPhase((p) => (typeof p === "number" ? (p + 1 >= STOPS.length ? null : p + 1) : p)), []);
  const back = useCallback(() => setPhase((p) => (typeof p === "number" ? (p === 0 ? "intro" : p - 1) : p)), []);

  useEffect(() => {
    if (phase === null) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); close(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, close]);

  // Cada parada abre su pantalla (del portal o de la consola) y dice qué iluminar.
  useEffect(() => {
    if (typeof phase !== "number") return;
    let alive = true;
    const s = STOPS[phase];
    setTarget(null);
    if (s.owner) nav.current.goOwner(s.owner);
    if (s.console) nav.current.goConsole(s.console);
    if (!s.sel) return;
    (async () => {
      if (s.click) {
        const tab = await waitFor(s.click, 6000);
        if (!alive) return;
        tab?.click();
      }
      const el = await waitFor(s.sel!, 6000);
      if (!alive || !el) return;
      el.scrollIntoView({ block: "center", behavior: "auto" });
      setTarget(s.sel!);
    })();
    return () => { alive = false; };
  }, [phase]);

  useLayoutEffect(() => {
    const h = bubbleRef.current?.offsetHeight;
    if (h && Math.abs(h - bubbleH) > 1) setBubbleH(h);
  });

  if (phase === null) return null;
  if (phase === "intro") return <PayPalIntro onStart={() => setPhase(0)} onSkip={close} />;

  const s = STOPS[phase];
  const centered = !s.sel;
  const last = phase === STOPS.length - 1;
  const title = t(`owner.guide.${s.key}.t`);
  return (
    <div className="fixed inset-0 z-[70] pointer-events-none" role="dialog" aria-modal="true" aria-label={title}>
      <SpotlightBackdrop hole={hole} centered={centered} shielded />
      <div ref={bubbleRef} className="fixed pointer-events-auto rounded-2xl bg-white shadow-2xl border border-slate-200 p-4 space-y-2"
           style={bubblePosition(hole, centered, vp, bubbleH)}>
        <div role="progressbar" aria-label={t("owner.guide.progress")} aria-valuemin={1} aria-valuemax={STOPS.length}
             aria-valuenow={phase + 1} className="h-1 rounded-full bg-slate-100 overflow-hidden">
          <div className="h-full rounded-full bg-[#0070BA] transition-all duration-200" style={{ width: `${((phase + 1) / STOPS.length) * 100}%` }} />
        </div>
        <div className="flex items-baseline gap-2">
          <h2 className="font-semibold text-slate-900 leading-snug">{title}</h2>
          {s.console && <span className="text-[11px] text-slate-400">{t("owner.tour.bookkeeper")}</span>}
        </div>
        <p className="text-sm text-slate-600">{t(`owner.guide.${s.key}.b`)}</p>
        {s.key === "ai" && (
          <AiTrace ai={SAMPLE_AI} steps={[
            { label: t("owner.guide.ai.s1"), detail: t("owner.guide.ai.s1d") },
            { label: t("owner.guide.ai.s2"), detail: t("owner.guide.ai.s2d") },
            { label: t("owner.guide.ai.s3") },
            { label: t("owner.guide.ai.s4"), tone: "wait" },
          ]} />
        )}
        {s.key === "insightsAi" && (
          <AiTrace ai={SAMPLE_INSIGHTS} steps={[
            { label: t("owner.guide.insightsAi.s1"), detail: t("owner.guide.insightsAi.s1d") },
            { label: t("owner.guide.insightsAi.s2"), detail: t("owner.guide.insightsAi.s2d") },
            { label: t("owner.guide.insightsAi.s3") },
          ]} />
        )}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-2 pt-1">
          <button type="button" onClick={close} className="text-sm text-slate-500 hover:text-slate-800">{t("owner.guide.skip")}</button>
          <button type="button" onClick={back} className="px-2 py-1 rounded-lg text-sm text-slate-600 hover:bg-slate-100 whitespace-nowrap">
            ← {t("owner.guide.back")}
          </button>
          <div className="ms-auto flex items-center gap-2">
            {last && inTour() && (
              <button type="button" onClick={() => { close(); void goLive(); }}
                      className="px-3 py-1.5 rounded-lg text-sm font-semibold whitespace-nowrap border border-[#0070BA] text-[#0070BA] hover:bg-[#009CDE]/10">
                {t("owner.guide.tryLive")}
              </button>
            )}
            <button type="button" onClick={last ? close : next} autoFocus
                    className="px-3 py-1.5 rounded-lg text-sm font-semibold text-[#003087] bg-[#FFC439] hover:bg-[#f2ba36]">
              {last ? t("owner.guide.done") : t("owner.guide.next")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
