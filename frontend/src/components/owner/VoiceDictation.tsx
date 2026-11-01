import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { voiceInvoice, type VoiceResult } from "../../api";
import { BTN_SECONDARY, BTN_SMALL_PRIMARY } from "../PageKit";

// DICTAR LA FACTURA (FASE 16 Paso 6). El micrófono usa la Web Speech API del navegador (gratis; en Chrome el audio lo
// procesa el navegador con su servicio, no ContaBu). Si no existe (algunos navegadores), el dictado del teclado escribe
// en el MISMO campo. El texto se ve y se corrige antes de usarlo. Nemotron solo EXTRAE monto, fecha y concepto — y no
// ve el nombre del cliente (el backend lo cambia por [CUSTOMER]); el código valida y llena el formulario. Nada se envía:
// después viene la pantalla de revisión de siempre.

type Rec = { lang: string; interimResults: boolean; continuous: boolean; start: () => void; stop: () => void;
             onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
             onend: (() => void) | null; onerror: (() => void) | null };

function recognizer(): (new () => Rec) | null {
  const w = window as unknown as { SpeechRecognition?: new () => Rec; webkitSpeechRecognition?: new () => Rec };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export default function VoiceDictation({ customerId, onFill }: {
  customerId: number | "";
  onFill: (r: VoiceResult) => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = (i18n.language || "en").split("-")[0];
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "info" | "warn" | "error"; text: string } | null>(null);
  const rec = useRef<Rec | null>(null);
  const Rec = recognizer();
  useEffect(() => () => rec.current?.stop(), []);

  function toggle() {
    if (!Rec) return;
    if (listening) { rec.current?.stop(); return; }
    const r = new Rec();
    r.lang = lang === "es" ? "es-US" : "en-US";
    r.interimResults = true;
    r.continuous = false;
    r.onresult = (e) => setText(Array.from(e.results).map((x) => x[0].transcript).join(" "));
    r.onend = () => setListening(false);
    r.onerror = () => { setListening(false); setMsg({ tone: "warn", text: t("owner.voice.micError") }); };
    rec.current = r; setMsg(null); setListening(true); r.start();
  }

  async function fill() {
    setBusy(true); setMsg(null);
    try {
      const r = await voiceInvoice(text.trim(), lang, customerId || undefined);
      onFill(r);
      if (r.needs_customer) setMsg({ tone: "warn", text: r.candidates.length ? t("owner.voice.pickOne") : t("owner.voice.noCustomer") });
      else if (r.warnings.length)
        setMsg({ tone: "info", text: `${t("owner.voice.filled")} ${r.warnings.map((w) => t(`owner.voice.warn.${w}`, { defaultValue: w })).join(" ")}` });
      else setMsg({ tone: "info", text: t("owner.voice.filled") });
    } catch (e) {
      const c = e instanceof Error ? e.message : "";
      setMsg({ tone: "error", text: t(`owner.voice.err.${["ai_cap_reached", "ai_unavailable", "text_required"].includes(c) ? c : "other"}`) });
    } finally { setBusy(false); }
  }

  return (
    <div className="sm:col-span-2 rounded-xl border border-emerald-200 bg-emerald-50/40 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-slate-900">{t("owner.voice.title")}</span>
        <span className="text-xs text-slate-500">{t("owner.voice.subtitle")}</span>
      </div>
      <div className="flex gap-2 items-start">
        {Rec && (
          <button type="button" onClick={toggle} aria-label={t("owner.voice.mic")} title={t("owner.voice.mic")}
                  className={`shrink-0 size-10 rounded-full flex items-center justify-center text-white ${listening ? "bg-rose-600 animate-pulse" : "bg-emerald-700 hover:bg-emerald-800"}`}>
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
            </svg>
          </button>
        )}
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={500}
                  placeholder={t("owner.voice.placeholder")}
                  className="flex-1 rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-sm" />
      </div>
      {listening && <p className="text-xs text-rose-700">{t("owner.voice.listening")}</p>}
      {!Rec && <p className="text-xs text-slate-500">{t("owner.voice.noMic")}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={busy || !text.trim()} onClick={fill} className={BTN_SMALL_PRIMARY}>
          {busy ? t("owner.paypal.working") : t("owner.voice.fill")}
        </button>
        {text && <button type="button" onClick={() => { setText(""); setMsg(null); }} className={BTN_SECONDARY}>{t("owner.voice.clear")}</button>}
        <span className="text-[11px] text-slate-500">{t("owner.voice.privacy")}</span>
      </div>
      {msg && <p className={`text-xs ${msg.tone === "error" ? "text-rose-700" : msg.tone === "warn" ? "text-amber-800" : "text-slate-700"}`}>{msg.text}</p>}
    </div>
  );
}

