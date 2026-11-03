import { useState } from "react";
import { useTranslation } from "react-i18next";
import { goLive } from "../../demo";

// LA PANTALLA DE INICIO DE LA VISITA GUIADA de `paypal.contabu.com` (FASE 16 Paso 7, Jose 3-nov): fondo oscuro y una
// tarjeta, como la de `demo.contabu.com` (`DemoGuide.tsx`), pero sobre PayPal. Sale CADA vez que se abre la visita (una por
// carga de la página): todos los jueces empiezan en el mismo lugar. Sin cifras inventadas: lo que dice, lo hace el código.

let shownThisLoad = false;
const BEFORE = ["net", "deposit", "purchase", "owner"] as const;

export default function PayPalIntro() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(() => !shownThisLoad);
  if (!open) return null;
  const close = () => { shownThisLoad = true; setOpen(false); };

  return (
    <div className="fixed inset-0 z-[70] bg-slate-900/60 overflow-y-auto" role="dialog" aria-modal="true" aria-label={t("owner.intro.title")}>
      <div className="min-h-full flex items-center justify-center p-4">
        <div className="w-full max-w-3xl rounded-3xl bg-white shadow-2xl p-5 sm:p-7 space-y-5">
          <div className="space-y-2 text-center">
            <div className="inline-block text-[11px] font-semibold tracking-wide uppercase text-[#003087] bg-[#FFC439]/40 rounded-full px-3 py-1">
              {t("owner.intro.kicker")}
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900">{t("owner.intro.title")}</h1>
            <p className="text-slate-600 max-w-xl mx-auto">{t("owner.intro.lead")}</p>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4 space-y-1.5">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("owner.intro.before")}</h2>
              <ul className="space-y-1.5">
                {BEFORE.map((k) => <li key={k} className="text-sm text-slate-700">{t(`owner.intro.problem.${k}`)}</li>)}
              </ul>
            </section>
            <section className="rounded-2xl border border-[#009CDE]/40 bg-[#009CDE]/5 p-4 space-y-1.5">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-[#003087]">{t("owner.intro.after")}</h2>
              <ul className="space-y-1.5">
                {BEFORE.map((k) => <li key={k} className="text-sm text-slate-700">{t(`owner.intro.solution.${k}`)}</li>)}
              </ul>
            </section>
          </div>
          <p className="text-xs text-center text-slate-500">{t("owner.intro.sandbox")}</p>

          <div className="flex flex-col-reverse sm:flex-row items-center justify-center gap-2">
            <button type="button" onClick={() => void goLive()} className="px-4 py-2 text-sm text-slate-500 hover:text-slate-800">
              {t("owner.intro.live")}
            </button>
            <button type="button" onClick={close} autoFocus
                    className="px-5 py-2.5 rounded-xl font-semibold text-[#003087] bg-[#FFC439] hover:bg-[#f2ba36] shadow-sm">
              {t("owner.intro.start")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
