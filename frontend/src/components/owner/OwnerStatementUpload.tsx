import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { setTransactionsAccount, uploadCsv, type UploadResult } from "../../api";
import { BTN_PRIMARY, BTN_SECONDARY, DetailCard, DetailHeader } from "../PageKit";

// SUBIR UN ESTADO DE CUENTA, desde el portal del dueño (FASE 16, 1-nov; permitido al CLIENT desde la FASE 13: es lo que
// el dueño APORTA). La misma subida por lotes que la consola (`Upload.tsx`): la IA solo mapea las columnas; las reglas
// del bookkeeper categorizan y lo que ninguna cubre queda Pendiente. Si el archivo no trae el número de cuenta, se le
// pregunta (últimos 4 dígitos) y solo se llena en lo que acaba de subir.

const CHUNK = 200;

export default function OwnerStatementUpload({ preview, onDone }: { preview: boolean; onDone: () => void }) {
  const { t } = useTranslation();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [needAccount, setNeedAccount] = useState<UploadResult | null>(null);
  const [account, setAccount] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const done = (r: UploadResult) => {
    setMsg({ tone: "ok", text: t("owner.statement.done", { inserted: r.inserted, dup: r.skipped_duplicates, held: r.held_pending }) });
    setFile(null); setNeedAccount(null); setAccount(""); onDone();
  };

  async function upload() {
    if (!file) return;
    setBusy(true); setMsg(null);
    try {
      const agg: UploadResult = { inserted: 0, errors: 0, skipped_duplicates: 0, held_pending: 0, account_detected: false, transaction_ids: [] };
      let offset = 0, total = Infinity;
      do {
        const r = await uploadCsv(file, offset, CHUNK);
        if (r.total == null) { done(r); return; }
        total = r.total;
        agg.inserted += r.inserted; agg.errors += r.errors; agg.skipped_duplicates += r.skipped_duplicates;
        agg.held_pending += r.held_pending; agg.account_detected ||= r.account_detected;
        agg.transaction_ids.push(...r.transaction_ids);
        offset += CHUNK;
        setProgress(t("owner.statement.progress", { done: Math.min(offset, total), total }));
      } while (offset < total);
      if (!agg.account_detected && agg.transaction_ids.length > 0) setNeedAccount(agg);
      else done(agg);
    } catch {
      setMsg({ tone: "error", text: t("owner.statement.error") });
    } finally { setBusy(false); setProgress(null); }
  }

  async function saveAccount() {
    if (!needAccount) return;
    setBusy(true);
    try { await setTransactionsAccount(needAccount.transaction_ids, account); done(needAccount); }
    catch { setMsg({ tone: "error", text: t("owner.statement.accountError") }); }
    finally { setBusy(false); }
  }

  return (
    <DetailCard>
      <DetailHeader kicker={t("owner.statement.kicker")} title={t("owner.statement.title")} sub={t("owner.statement.sub")} />
      {!needAccount ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input ref={input} type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                 className="hidden" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setMsg(null); }} />
          <button onClick={() => input.current?.click()} disabled={preview || busy} className={BTN_SECONDARY}
                  title={preview ? t("owner.preview.disabled") : undefined}>
            {file ? file.name : t("owner.statement.choose")}
          </button>
          <button onClick={() => void upload()} disabled={preview || busy || !file} className={BTN_PRIMARY}>
            {busy ? (progress ?? t("owner.paypal.working")) : t("owner.statement.upload")}
          </button>
          <span className="text-[11px] text-slate-500">{t("owner.statement.formats")}</span>
        </div>
      ) : (
        <div className="mt-3 space-y-2">
          <p className="text-sm text-slate-700">{t("owner.statement.askAccount", { count: needAccount.transaction_ids.length })}</p>
          <div className="flex flex-wrap gap-2">
            <input inputMode="numeric" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="1234"
                   className="w-32 rounded-lg border border-slate-200 px-2 py-1.5 text-sm tabular-nums" />
            <button onClick={() => void saveAccount()} disabled={busy || account.replace(/\D/g, "").length < 4} className={BTN_PRIMARY}>
              {t("owner.statement.saveAccount")}
            </button>
          </div>
        </div>
      )}
      {msg && <p className={`mt-2 text-sm ${msg.tone === "ok" ? "text-emerald-800" : "text-rose-700"}`}>{msg.text}</p>}
    </DetailCard>
  );
}
