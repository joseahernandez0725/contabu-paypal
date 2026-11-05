import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { deleteInvoiceLogo, getInvoiceProfile, logoSrc, putInvoiceProfile, uploadInvoiceLogo, type InvoiceProfile } from "../../api";
import { BTN_GHOST, BTN_SECONDARY, BTN_SMALL_PRIMARY } from "../PageKit";

// TUS DATOS EN LA FACTURA, en Cobros (FASE 16, 4-nov; `backend/invoices.py`). El dueño los pone una vez: dirección,
// teléfono, sitio web, tax ID, y la nota y los términos por defecto. Van a PayPal en cada factura NUEVA (las enviadas no
// cambian). El backend valida con los límites de PayPal y dice qué campo falló. En la vista previa, solo lectura.

const EMPTY: InvoiceProfile = { address_line: "", city: "", state: "", postal_code: "", phone: "", website: "", tax_id: "", note: "", terms: "" };

// El logo se ajusta AQUÍ, en el navegador, al máximo de PayPal (250 × 90, sin deformarlo) y se manda en PNG.
const LOGO_W = 250, LOGO_H = 90;
async function fitLogo(file: File): Promise<Blob> {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
    const k = Math.min(LOGO_W / img.naturalWidth, LOGO_H / img.naturalHeight, 1);
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(img.naturalWidth * k)); c.height = Math.max(1, Math.round(img.naturalHeight * k));
    c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
    return await new Promise<Blob>((ok, bad) => c.toBlob((b) => (b ? ok(b) : bad(new Error("canvas"))), "image/png"));
  } finally { URL.revokeObjectURL(url); }
}

export function formatPhone(digits: string): string {
  return digits.length === 10 ? `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}` : digits;
}

export default function InvoiceProfileCard({ preview, businessName, onChange }: {
  preview: boolean; businessName: string; onChange?: (p: InvoiceProfile) => void;
}) {
  const { t } = useTranslation();
  const [p, setP] = useState<InvoiceProfile | null>(null);
  const [draft, setDraft] = useState<InvoiceProfile>(EMPTY);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { getInvoiceProfile().then((x) => { setP(x); onChange?.(x); }).catch(() => setP(EMPTY)); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (!p) return null;

  const place = [p.city, [p.state, p.postal_code].filter(Boolean).join(" ")].filter(Boolean).join(", ");
  const summary = [businessName, place, p.phone && formatPhone(p.phone), p.website].filter(Boolean).join(" · ");

  async function onLogo(file: File | undefined) {
    if (!file) return;
    setBusy(true); setErr(null);
    try { const x = await uploadInvoiceLogo(await fitLogo(file)); setP(x); setDraft((d) => ({ ...d, logo_path: x.logo_path })); onChange?.(x); }
    catch { setErr(t("owner.profile.logoErr")); }
    finally { setBusy(false); }
  }
  async function dropLogo() {
    setBusy(true); setErr(null);
    try { const x = await deleteInvoiceLogo(); setP(x); onChange?.(x); } catch { setErr(t("owner.profile.errSave")); } finally { setBusy(false); }
  }

  async function save() {
    setBusy(true); setErr(null);
    try { const x = await putInvoiceProfile(draft); setP(x); onChange?.(x); setEdit(false); }
    catch (e) {
      const c = e instanceof Error ? e.message : "";
      setErr(c.startsWith("bad_") ? t("owner.profile.bad", { field: t(`owner.profile.f.${c.slice(4)}`, { defaultValue: c.slice(4) }) }) : t("owner.profile.errSave"));
    } finally { setBusy(false); }
  }

  type TextKey = Exclude<keyof InvoiceProfile, "logo_path" | "logo_on_paypal">;
  const field = (k: TextKey, span = "", extra: Record<string, string | number> = {}) => (
    <label className={`text-xs text-slate-600 space-y-1 ${span}`}>
      <span>{t(`owner.profile.f.${k}`)}</span>
      <input value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} {...extra}
             className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
    </label>
  );
  return (
    <section className="bg-white rounded-xl border border-slate-200 px-3 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{t("owner.profile.title")}</span>
        {!edit && p.logo_path && <img src={logoSrc(p.logo_path)} alt={t("owner.profile.logo")} className="h-6 w-auto max-w-[80px] object-contain" />}
        {!edit && <span className="text-xs text-slate-600 truncate">{summary || t("owner.profile.empty")}</span>}
        <span className="flex-1" />
        {!preview && !edit && <button onClick={() => { setDraft(p); setEdit(true); }} className={BTN_GHOST}>{t("owner.auto.edit")}</button>}
      </div>
      {edit && (
        <div className="mt-2 grid gap-2 sm:grid-cols-4 [&>*]:min-w-0">
          <p className="sm:col-span-4 text-xs text-slate-500">{t("owner.profile.hint")}</p>
          <div className="sm:col-span-4 flex flex-wrap items-center gap-3">
            <span className="text-xs text-slate-600">{t("owner.profile.logo")}</span>
            {p.logo_path ? (
              <img src={logoSrc(p.logo_path)} alt={t("owner.profile.logo")} className="h-[45px] w-auto max-w-[125px] object-contain rounded border border-slate-200 bg-white p-1" />
            ) : <span className="text-xs text-slate-400">{t("owner.profile.noLogo")}</span>}
            <label className={`${BTN_SECONDARY} cursor-pointer`}>
              {p.logo_path ? t("owner.profile.logoChange") : t("owner.profile.logoUpload")}
              <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" disabled={busy}
                     onChange={(e) => { void onLogo(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
            {p.logo_path && <button disabled={busy} onClick={() => void dropLogo()} className={BTN_GHOST}>{t("owner.profile.logoRemove")}</button>}
            <span className="basis-full text-[11px] text-slate-500">
              {t(p.logo_path && !p.logo_on_paypal ? "owner.profile.logoLocal" : "owner.profile.logoHint")}
            </span>
          </div>
          {field("address_line", "sm:col-span-4", { maxLength: 100 })}
          {field("city", "sm:col-span-2", { maxLength: 60 })}
          {field("state", "", { maxLength: 2, placeholder: "FL" })}
          {field("postal_code", "", { maxLength: 10, inputMode: "numeric" })}
          {field("phone", "sm:col-span-2", { inputMode: "tel" })}
          {field("website", "sm:col-span-2", { placeholder: "https://" })}
          {field("tax_id", "sm:col-span-2", { maxLength: 100 })}
          <label className="text-xs text-slate-600 space-y-1 sm:col-span-4">
            <span>{t("owner.profile.f.note")}</span>
            <textarea value={draft.note} rows={2} maxLength={1000} onChange={(e) => setDraft({ ...draft, note: e.target.value })}
                      className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
          </label>
          <label className="text-xs text-slate-600 space-y-1 sm:col-span-4">
            <span>{t("owner.profile.f.terms")}</span>
            <textarea value={draft.terms} rows={2} maxLength={1000} onChange={(e) => setDraft({ ...draft, terms: e.target.value })}
                      className="w-full min-w-0 rounded-md border border-slate-300 px-2 py-1 text-sm" />
          </label>
          <div className="sm:col-span-4 flex gap-2">
            <button disabled={busy} onClick={() => void save()} className={BTN_SMALL_PRIMARY}>{busy ? "…" : t("owner.profile.save")}</button>
            <button disabled={busy} onClick={() => { setEdit(false); setErr(null); }} className={BTN_SECONDARY}>{t("owner.invoices.cancel")}</button>
          </div>
        </div>
      )}
      {err && <p className="mt-1 text-xs text-rose-700">{err}</p>}
    </section>
  );
}
