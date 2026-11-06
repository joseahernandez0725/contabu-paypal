import { useTranslation } from "react-i18next";
import { goLive, inTour } from "../../demo";

// LA PANTALLA DE INICIO DE LA VISITA GUIADA de `paypal.contabu.com` (FASE 16 Paso 7, Jose 3-nov): fondo oscuro y una
// tarjeta, como la de `demo.contabu.com` (`DemoGuide.tsx`), pero sobre PayPal. Sin cifras inventadas: lo que dice, lo hace
// el código. 🔄 5-nov: es la entrada de la guía (`PayPalGuide`): "Start" abre las paradas; quién y cuándo la abre, la guía.


export default function PayPalIntro({ onStart, onSkip }: { onStart: () => void; onSkip: () => void }) {
  const { t } = useTranslation();

  return (
    <div className="fixed inset-0 z-[70] bg-slate-900/60 overflow-y-auto" role="dialog" aria-modal="true" aria-label={t("owner.intro.title")}>
      <div className="min-h-full flex items-center justify-center p-4">
        <div className="w-full max-w-3xl rounded-3xl bg-white shadow-2xl p-5 sm:p-7 space-y-5">
          <div className="space-y-2 text-center">
            <div className="inline-block text-[11px] font-semibold tracking-wide uppercase text-[#003087] bg-[#FFC439]/40 rounded-full px-3 py-1">
              {t("owner.intro.kicker")}
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-slate-900">{t("owner.intro.title")}</h1>
          </div>
          {/* 🔄 6-nov (Jose): un solo párrafo —qué construimos, cómo y para qué— en vez de las dos columnas "sin / con". */}
          <p className="text-[15px] leading-relaxed text-slate-700 max-w-2xl mx-auto">{t("owner.intro.body")}</p>
          <p className="text-xs text-center text-slate-500">{t("owner.intro.sandbox")}</p>

          <div className="flex flex-col-reverse sm:flex-row items-center justify-center gap-2">
            <button type="button" onClick={onSkip} className="px-4 py-2 text-sm text-slate-500 hover:text-slate-800">
              {t("owner.guide.skip")}
            </button>
            {inTour() && (
              <button type="button" onClick={() => void goLive()} className="px-4 py-2 text-sm text-slate-500 hover:text-slate-800">
                {t("owner.intro.live")}
              </button>
            )}
            <button type="button" onClick={onStart} autoFocus
                    className="px-5 py-2.5 rounded-xl font-semibold text-[#003087] bg-[#FFC439] hover:bg-[#f2ba36] shadow-sm">
              {t("owner.intro.start")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
