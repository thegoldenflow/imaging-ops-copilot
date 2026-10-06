// Patient-facing MRI safety questionnaire. Mobile first, four languages.

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, ScanLine } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, ErrorState, Loading } from "../../components/ui";
import { api, post } from "../../lib/api";

type Lang = "en" | "fr" | "zh" | "pa";

const T: Record<Lang, Record<string, string>> = {
  en: {
    title: "MRI safety questionnaire", hello: "Hello", intro: "MRI uses a strong magnet. Please answer every question so our team can keep you safe.",
    yes: "Yes", no: "No", other: "Do you have any other implant, device or metal in your body? Describe it in your own words.",
    submit: "Send answers", done: "Thank you. A technologist will review your answers before your scan and may call you.",
    pacemaker: "Do you have a pacemaker or defibrillator?", aneurysm_clip: "Do you have a clip on a brain aneurysm?",
    cochlear_implant: "Do you have a cochlear implant (hearing implant)?", neurostimulator: "Do you have a nerve or spinal cord stimulator?",
    metal_fragments: "Have you ever had metal pieces in your body or eyes (for example from welding)?", drug_pump: "Do you use an insulin or other drug pump?",
    recent_surgery: "Have you had surgery in the last 6 weeks?", pregnant: "Are you pregnant or could you be pregnant?",
    claustrophobic: "Are you uncomfortable in small, closed spaces?",
  },
  fr: {
    title: "Questionnaire de sécurité IRM", hello: "Bonjour", intro: "L'IRM utilise un aimant puissant. Veuillez répondre à chaque question pour votre sécurité.",
    yes: "Oui", no: "Non", other: "Avez-vous un autre implant, appareil ou métal dans le corps ? Décrivez-le avec vos mots.",
    submit: "Envoyer", done: "Merci. Un technologue examinera vos réponses avant l'examen et pourrait vous appeler.",
    pacemaker: "Avez-vous un stimulateur cardiaque ou un défibrillateur ?", aneurysm_clip: "Avez-vous un clip d'anévrisme cérébral ?",
    cochlear_implant: "Avez-vous un implant cochléaire ?", neurostimulator: "Avez-vous un neurostimulateur ?",
    metal_fragments: "Avez-vous déjà eu des éclats de métal dans le corps ou les yeux ?", drug_pump: "Utilisez-vous une pompe à insuline ou à médicament ?",
    recent_surgery: "Avez-vous été opéré(e) au cours des 6 dernières semaines ?", pregnant: "Êtes-vous enceinte ou pourriez-vous l'être ?",
    claustrophobic: "Êtes-vous mal à l'aise dans les espaces clos ?",
  },
  zh: {
    title: "核磁共振（MRI）安全问卷", hello: "您好", intro: "MRI 使用强磁场。请回答每个问题，以确保您的安全。",
    yes: "是", no: "否", other: "您体内是否有其他植入物、装置或金属？请用自己的话描述。",
    submit: "提交", done: "谢谢。技师会在检查前审核您的回答，可能会给您打电话。",
    pacemaker: "您是否装有心脏起搏器或除颤器？", aneurysm_clip: "您脑部是否有动脉瘤夹？",
    cochlear_implant: "您是否装有人工耳蜗？", neurostimulator: "您是否装有神经或脊髓刺激器？",
    metal_fragments: "您的身体或眼睛里是否进过金属碎片（例如焊接时）？", drug_pump: "您是否使用胰岛素泵或其他药物泵？",
    recent_surgery: "您最近 6 周内是否做过手术？", pregnant: "您是否怀孕或可能怀孕？",
    claustrophobic: "您在狭小封闭的空间里会感到不适吗？",
  },
  pa: {
    title: "MRI ਸੁਰੱਖਿਆ ਪ੍ਰਸ਼ਨਾਵਲੀ", hello: "ਸਤ ਸ੍ਰੀ ਅਕਾਲ", intro: "MRI ਇੱਕ ਤਾਕਤਵਰ ਚੁੰਬਕ ਵਰਤਦਾ ਹੈ। ਤੁਹਾਡੀ ਸੁਰੱਖਿਆ ਲਈ ਕਿਰਪਾ ਕਰਕੇ ਹਰ ਸਵਾਲ ਦਾ ਜਵਾਬ ਦਿਓ।",
    yes: "ਹਾਂ", no: "ਨਹੀਂ", other: "ਕੀ ਤੁਹਾਡੇ ਸਰੀਰ ਵਿੱਚ ਕੋਈ ਹੋਰ ਇਮਪਲਾਂਟ, ਯੰਤਰ ਜਾਂ ਧਾਤ ਹੈ? ਆਪਣੇ ਸ਼ਬਦਾਂ ਵਿੱਚ ਦੱਸੋ।",
    submit: "ਜਵਾਬ ਭੇਜੋ", done: "ਧੰਨਵਾਦ। ਟੈਕਨੋਲੋਜਿਸਟ ਤੁਹਾਡੇ ਸਕੈਨ ਤੋਂ ਪਹਿਲਾਂ ਜਵਾਬ ਦੇਖੇਗਾ ਅਤੇ ਤੁਹਾਨੂੰ ਫ਼ੋਨ ਕਰ ਸਕਦਾ ਹੈ।",
    pacemaker: "ਕੀ ਤੁਹਾਡੇ ਪੇਸਮੇਕਰ ਜਾਂ ਡੀਫਿਬ੍ਰਿਲੇਟਰ ਲੱਗਿਆ ਹੈ?", aneurysm_clip: "ਕੀ ਤੁਹਾਡੇ ਦਿਮਾਗ ਵਿੱਚ ਐਨਿਉਰਿਜ਼ਮ ਕਲਿੱਪ ਹੈ?",
    cochlear_implant: "ਕੀ ਤੁਹਾਡੇ ਕੋਕਲੀਅਰ ਇਮਪਲਾਂਟ (ਸੁਣਨ ਵਾਲਾ ਇਮਪਲਾਂਟ) ਹੈ?", neurostimulator: "ਕੀ ਤੁਹਾਡੇ ਨਸਾਂ ਜਾਂ ਰੀੜ੍ਹ ਦਾ ਸਟਿਮੂਲੇਟਰ ਹੈ?",
    metal_fragments: "ਕੀ ਕਦੇ ਤੁਹਾਡੇ ਸਰੀਰ ਜਾਂ ਅੱਖਾਂ ਵਿੱਚ ਧਾਤ ਦੇ ਟੁਕੜੇ ਗਏ ਹਨ?", drug_pump: "ਕੀ ਤੁਸੀਂ ਇਨਸੁਲਿਨ ਜਾਂ ਦਵਾਈ ਪੰਪ ਵਰਤਦੇ ਹੋ?",
    recent_surgery: "ਕੀ ਪਿਛਲੇ 6 ਹਫ਼ਤਿਆਂ ਵਿੱਚ ਤੁਹਾਡਾ ਅਪਰੇਸ਼ਨ ਹੋਇਆ ਹੈ?", pregnant: "ਕੀ ਤੁਸੀਂ ਗਰਭਵਤੀ ਹੋ ਜਾਂ ਹੋ ਸਕਦੇ ਹੋ?",
    claustrophobic: "ਕੀ ਤੁਸੀਂ ਛੋਟੀਆਂ ਬੰਦ ਥਾਵਾਂ ਵਿੱਚ ਬੇਆਰਾਮ ਮਹਿਸੂਸ ਕਰਦੇ ਹੋ?",
  },
};

interface Info { first_name: string; language: Lang; languages: Record<string, string>; questions: string[]; submitted: boolean }

export function MriQuestionnairePage() {
  const { token } = useParams();
  const info = useQuery({ queryKey: ["mri-q", token], queryFn: () => api<Info>(`/api/public/mri-screening/${token}`) });
  const [lang, setLang] = useState<Lang>("en");
  const [answers, setAnswers] = useState<Record<string, boolean | undefined>>({});
  const [freeText, setFreeText] = useState("");
  useEffect(() => { if (info.data) setLang(info.data.language); }, [info.data]);
  const submit = useMutation({
    mutationFn: () => post(`/api/public/mri-screening/${token}`, { answers, free_text: freeText, language: lang }),
  });
  const t = T[lang] ?? T.en;
  const allAnswered = info.data?.questions.every((q) => answers[q] !== undefined);

  return (
    <div className="min-h-full bg-slate-50">
      <header className="flex items-center gap-2 bg-brand-600 px-4 py-3 text-white"><ScanLine className="size-5" /><span className="font-semibold">{t.title}</span></header>
      <main className="mx-auto max-w-md px-4 py-5">
        {info.isLoading && <Loading />}
        {info.error && <ErrorState error={info.error} />}
        {info.data && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-2">
              <p className="text-lg font-semibold text-slate-900">{t.hello}, {info.data.first_name}</p>
              <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} className="h-9 rounded-lg border border-slate-300 px-2 text-sm" aria-label="Language">
                {Object.entries(info.data.languages).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            {submit.isSuccess ? (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-800" role="status">
                <p className="flex items-start gap-2 font-medium"><CheckCircle2 className="mt-0.5 size-5 shrink-0" /> {t.done}</p>
              </div>
            ) : (
              <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
                <p className="text-sm text-slate-600">{t.intro}</p>
                {info.data.questions.map((q) => (
                  <fieldset key={q} className="rounded-xl border border-slate-200 bg-white p-3">
                    <legend className="sr-only">{t[q]}</legend>
                    <p className="mb-2 text-sm text-slate-800">{t[q]}</p>
                    <div className="flex gap-2">
                      {[true, false].map((v) => (
                        <label key={String(v)} className={`flex h-10 flex-1 cursor-pointer items-center justify-center rounded-lg border text-sm ${answers[q] === v ? "border-brand-600 bg-brand-50 font-medium text-brand-700" : "border-slate-300"}`}>
                          <input type="radio" name={q} className="sr-only" checked={answers[q] === v} onChange={() => setAnswers({ ...answers, [q]: v })} data-testid={`${q}-${v ? "yes" : "no"}`} />
                          {v ? t.yes : t.no}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ))}
                <label className="block rounded-xl border border-slate-200 bg-white p-3">
                  <span className="mb-2 block text-sm text-slate-800">{t.other}</span>
                  <textarea value={freeText} onChange={(e) => setFreeText(e.target.value)} rows={3} className="w-full rounded-lg border border-slate-300 p-2 text-base" aria-label={t.other} />
                </label>
                {submit.error && <p className="text-sm text-rose-600" role="alert">{(submit.error as Error).message}</p>}
                <Button type="submit" variant="primary" className="h-11 w-full text-base" disabled={!allAnswered} loading={submit.isPending}>{t.submit}</Button>
              </form>
            )}
            <p className="text-center text-xs text-slate-400">Demo · synthetic data</p>
          </div>
        )}
      </main>
    </div>
  );
}
