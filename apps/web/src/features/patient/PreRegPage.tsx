// Patient-facing pre-registration page. Mobile first, opened from the SMS link.

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, ScanLine } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useParams } from "react-router-dom";
import { Button, ErrorState, Loading } from "../../components/ui";
import { api, post } from "../../lib/api";

interface PreRegInfo {
  status: string;
  first_name: string;
  language: string;
  languages: Record<string, string>;
  appointment: { exam: string; when: string; site: string; address: string };
  prep: string;
}

type Lang = "en" | "fr" | "zh" | "pa";

const T: Record<Lang, Record<string, string>> = {
  en: { title: "Pre-registration", hello: "Hello", appt: "Your appointment", prep: "How to prepare", contact: "Contact details", phone: "Phone", email: "Email", address: "Address", language: "Preferred language", insurance: "Insurance", ohip: "OHIP", private: "Private insurance", card: "Health card number", version: "Version code", insurer: "Insurer", policy: "Policy number", consent: "I consent to the clinic using this information for my imaging appointment.", submit: "Submit", done: "Thank you. Your pre-registration is complete.", coverage: "Insurance check" },
  fr: { title: "Préinscription", hello: "Bonjour", appt: "Votre rendez-vous", prep: "Préparation", contact: "Coordonnées", phone: "Téléphone", email: "Courriel", address: "Adresse", language: "Langue préférée", insurance: "Assurance", ohip: "RAMO (OHIP)", private: "Assurance privée", card: "Numéro de carte santé", version: "Code de version", insurer: "Assureur", policy: "Numéro de police", consent: "J'accepte que la clinique utilise ces renseignements pour mon examen d'imagerie.", submit: "Envoyer", done: "Merci. Votre préinscription est terminée.", coverage: "Vérification de l'assurance" },
  zh: { title: "网上预登记", hello: "您好", appt: "您的预约", prep: "检查前须知", contact: "联系方式", phone: "电话", email: "电子邮箱", address: "地址", language: "首选语言", insurance: "保险", ohip: "OHIP 安省医保", private: "私人保险", card: "健康卡号码", version: "版本码", insurer: "保险公司", policy: "保单号", consent: "我同意诊所为本次影像检查使用以上信息。", submit: "提交", done: "谢谢，您的预登记已完成。", coverage: "保险核验" },
  pa: { title: "ਪ੍ਰੀ-ਰਜਿਸਟ੍ਰੇਸ਼ਨ", hello: "ਸਤ ਸ੍ਰੀ ਅਕਾਲ", appt: "ਤੁਹਾਡੀ ਮੁਲਾਕਾਤ", prep: "ਤਿਆਰੀ ਕਿਵੇਂ ਕਰਨੀ ਹੈ", contact: "ਸੰਪਰਕ ਵੇਰਵੇ", phone: "ਫ਼ੋਨ", email: "ਈਮੇਲ", address: "ਪਤਾ", language: "ਪਸੰਦੀਦਾ ਭਾਸ਼ਾ", insurance: "ਬੀਮਾ", ohip: "OHIP", private: "ਨਿੱਜੀ ਬੀਮਾ", card: "ਹੈਲਥ ਕਾਰਡ ਨੰਬਰ", version: "ਵਰਜਨ ਕੋਡ", insurer: "ਬੀਮਾ ਕੰਪਨੀ", policy: "ਪਾਲਿਸੀ ਨੰਬਰ", consent: "ਮੈਂ ਸਹਿਮਤ ਹਾਂ ਕਿ ਕਲੀਨਿਕ ਮੇਰੀ ਜਾਂਚ ਲਈ ਇਹ ਜਾਣਕਾਰੀ ਵਰਤੇ।", submit: "ਭੇਜੋ", done: "ਧੰਨਵਾਦ। ਤੁਹਾਡੀ ਪ੍ਰੀ-ਰਜਿਸਟ੍ਰੇਸ਼ਨ ਪੂਰੀ ਹੋ ਗਈ ਹੈ।", coverage: "ਬੀਮਾ ਜਾਂਚ" },
};

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
    </label>
  );
}

const input = "h-11 w-full rounded-lg border border-slate-300 px-3 text-base";

export function PreRegPage() {
  const { token } = useParams();
  const info = useQuery({ queryKey: ["prereg", token], queryFn: () => api<PreRegInfo>(`/api/public/prereg/${token}`) });
  const [lang, setLang] = useState<Lang>("en");
  const [form, setForm] = useState({ phone: "", email: "", address: "", insurance_type: "ohip", health_card: "", health_card_version: "", insurer: "", policy_number: "", consent: false });
  useEffect(() => { if (info.data) setLang(info.data.language as Lang); }, [info.data]);
  const submit = useMutation({
    mutationFn: () => post<{ status: string; coverage: { valid: boolean; detail: string } }>(`/api/public/prereg/${token}`, { ...form, preferred_language: lang }),
  });
  const t = T[lang] ?? T.en;
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  return (
    <div className="min-h-full bg-slate-50">
      <header className="flex items-center gap-2 bg-brand-600 px-4 py-3 text-white">
        <ScanLine className="size-5" />
        <span className="font-semibold">{t.title}</span>
      </header>
      <main className="mx-auto max-w-md px-4 py-5">
        {info.isLoading && <Loading />}
        {info.error && <ErrorState error={info.error} />}
        {info.data && (
          <div className="space-y-5">
            <div className="flex items-center justify-between gap-2">
              <p className="text-lg font-semibold text-slate-900">{t.hello}, {info.data.first_name}</p>
              <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label={t.language}>
                {Object.entries(info.data.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <section className="rounded-xl border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-semibold text-slate-500">{t.appt}</h2>
              <p className="mt-1 font-medium text-slate-900">{info.data.appointment.exam}</p>
              <p className="text-sm text-slate-700">{info.data.appointment.when}</p>
              <p className="text-sm text-slate-700">{info.data.appointment.site} · {info.data.appointment.address}</p>
              <h2 className="mt-3 text-sm font-semibold text-slate-500">{t.prep}</h2>
              <p className="mt-1 text-sm text-slate-700">{info.data.prep}</p>
            </section>

            {submit.isSuccess || info.data.status === "completed" ? (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-800" role="status">
                <p className="flex items-center gap-2 font-medium"><CheckCircle2 className="size-5" /> {t.done}</p>
                {submit.data && <p className="mt-1 text-sm">{t.coverage}: {submit.data.coverage.detail}</p>}
              </div>
            ) : (
              <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
                <h2 className="text-sm font-semibold text-slate-500">{t.contact}</h2>
                <Field label={t.phone}><input className={input} required type="tel" value={form.phone} onChange={set("phone")} /></Field>
                <Field label={t.email}><input className={input} required type="email" value={form.email} onChange={set("email")} /></Field>
                <Field label={t.address}><input className={input} required value={form.address} onChange={set("address")} /></Field>
                <h2 className="pt-2 text-sm font-semibold text-slate-500">{t.insurance}</h2>
                <div className="flex gap-4">
                  {(["ohip", "private"] as const).map((k) => (
                    <label key={k} className="flex items-center gap-2 text-sm">
                      <input type="radio" name="insurance" checked={form.insurance_type === k} onChange={() => setForm({ ...form, insurance_type: k })} />
                      {t[k]}
                    </label>
                  ))}
                </div>
                {form.insurance_type === "ohip" ? (
                  <div className="grid grid-cols-[1fr_6rem] gap-3">
                    <Field label={t.card}><input className={input} required inputMode="numeric" value={form.health_card} onChange={set("health_card")} /></Field>
                    <Field label={t.version}><input className={input} required maxLength={2} value={form.health_card_version} onChange={set("health_card_version")} /></Field>
                  </div>
                ) : (
                  <>
                    <Field label={t.insurer}><input className={input} required value={form.insurer} onChange={set("insurer")} /></Field>
                    <Field label={t.policy}><input className={input} required value={form.policy_number} onChange={set("policy_number")} /></Field>
                  </>
                )}
                <label className="flex items-start gap-2 text-sm text-slate-700">
                  <input type="checkbox" className="mt-1" required checked={form.consent} onChange={set("consent")} />
                  {t.consent}
                </label>
                {submit.error && <p className="text-sm text-rose-600" role="alert">{(submit.error as Error).message}</p>}
                <Button type="submit" variant="primary" className="h-11 w-full text-base" loading={submit.isPending}>{t.submit}</Button>
              </form>
            )}
            <p className="text-center text-xs text-slate-400">Demo · synthetic data · insurance checks are simulated</p>
          </div>
        )}
      </main>
    </div>
  );
}
