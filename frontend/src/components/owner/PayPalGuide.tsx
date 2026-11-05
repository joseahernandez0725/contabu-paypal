import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { goLive, inTour } from "../../demo";
import type { View } from "../Sidebar";
import { bubblePosition, SpotlightBackdrop, useHole, useViewport, waitFor } from "../Spotlight";
import type { OwnerView } from "./OwnerSidebar";
import PayPalIntro from "./PayPalIntro";

// LA GUÍA DE `paypal.contabu.com` (FASE 16, Jose 5-nov: "una visita guiada parecida a la de Nebius"). La pantalla de
// inicio (`PayPalIntro`) y 9 paradas con el MISMO foco que `demo.contabu.com` (`Spotlight.tsx`): la pantalla se oscurece,
// se ilumina lo que se explica y al lado va una burbuja. Recorre el portal del dueño y dos pantallas de la consola del
// bookkeeper. SIN IA (Jose, 5-nov): solo muestra resultados del libro sembrado. Todo lo iluminado lleva tapón: se avanza
// con Next. En la visita se abre sola una vez por carga; en local y en vivo, con el botón "Guide" de la tarjeta de paradas.

export const OPEN_PAYPAL_GUIDE = "contabu:paypal-guide-open";
export const PAYPAL_GUIDE_STATE = "contabu:paypal-guide-state";   // detail: boolean (abierta) — la tarjeta se oculta
export function openPayPalGuide() { window.dispatchEvent(new Event(OPEN_PAYPAL_GUIDE)); }
// También como variable de módulo: la tarjeta de la consola se monta DESPUÉS del aviso al cruzar de cáscara.
let guideOpen = false;
export function isPayPalGuideOpen() { return guideOpen; }

type Stop = { key: string; owner?: OwnerView; console?: View; sel?: string };
const STOPS: Stop[] = [
  { key: "year", owner: "summary", sel: '[data-tour="pp-year"]' },
  { key: "categories", owner: "summary", sel: '[data-tour="pp-categories"]' },
  { key: "collected", owner: "invoices", sel: '[data-tour="pp-collected"]' },
  { key: "paid", owner: "invoices", sel: '[data-tour="pp-paid-invoice"]' },
  { key: "newInvoice", owner: "invoices", sel: '[data-tour="pp-new-invoice"]' },
  { key: "paypal", owner: "accounts", sel: '[data-tour="pp-paypal-card"]' },
  { key: "banks", owner: "accounts", sel: '[data-tour="pp-banks"]' },
  { key: "bank", console: "upload", sel: '[data-tour="pp-bank"]' },
  { key: "close", console: "settings", sel: '[data-tour="pp-close"]' },
  { key: "live" },
];

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
  useEffect(() => {
    guideOpen = phase !== null;
    window.dispatchEvent(new CustomEvent(PAYPAL_GUIDE_STATE, { detail: guideOpen }));
  }, [phase]);

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
