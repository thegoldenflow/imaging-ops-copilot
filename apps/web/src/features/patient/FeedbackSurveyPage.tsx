// Patient-facing satisfaction survey. Mobile first, four languages.

import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, ScanLine, Star } from "lucide-react";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Button, ErrorState, Loading } from "../../components/ui";
import { api, post } from "../../lib/api";
import { LANGUAGE_LABEL } from "../../lib/format";

type Lang = "en" | "fr" | "zh" | "pa";

const T: Record<Lang, Record<string, string>> = {
  en: { title: "How was your visit?", hello: "Hello", intro: "Your {exam} at {site}. Please rate your visit and tell us anything we could do better.",
    rating: "Your rating", comment: "Comments (optional)", submit: "Send", done: "Thank you for your feedback. It helps us improve.", already: "We already have your answer. Thank you!" },
  fr: { title: "Comment s'est passée votre visite ?", hello: "Bonjour", intro: "Votre examen ({exam}) à {site}. Notez votre visite et dites-nous ce que nous pourrions améliorer.",
    rating: "Votre note", comment: "Commentaires (facultatif)", submit: "Envoyer", done: "Merci pour vos commentaires. Ils nous aident à nous améliorer.", already: "Nous avons déjà votre réponse. Merci !" },
  zh: { title: "您这次就诊感觉如何？", hello: "您好", intro: "您在{site}做的{exam}检查。请为本次就诊打分，并告诉我们可以改进的地方。",
    rating: "您的评分", comment: "意见（可选）", submit: "提交", done: "感谢您的反馈，我们会努力改进。", already: "我们已经收到您的回答，谢谢！" },
  pa: { title: "ਤੁਹਾਡੀ ਫੇਰੀ ਕਿਵੇਂ ਰਹੀ?", hello: "ਸਤ ਸ੍ਰੀ ਅਕਾਲ", intro: "{site} ਵਿਖੇ ਤੁਹਾਡਾ {exam}। ਕਿਰਪਾ ਕਰਕੇ ਆਪਣੀ ਫੇਰੀ ਨੂੰ ਰੇਟ ਕਰੋ ਅਤੇ ਦੱਸੋ ਅਸੀਂ ਕੀ ਬਿਹਤਰ ਕਰ ਸਕਦੇ ਹਾਂ।",
    rating: "ਤੁਹਾਡੀ ਰੇਟਿੰਗ", comment: "ਟਿੱਪਣੀ (ਚੋਣਵੀਂ)", submit: "ਭੇਜੋ", done: "ਤੁਹਾਡੀ ਰਾਏ ਲਈ ਧੰਨਵਾਦ।", already: "ਸਾਡੇ ਕੋਲ ਤੁਹਾਡਾ ਜਵਾਬ ਪਹਿਲਾਂ ਹੀ ਹੈ। ਧੰਨਵਾਦ!" },
};

interface Info { first_name: string; language: Lang; site_name: string; exam_name: string; submitted: boolean }

export function FeedbackSurveyPage() {
  const { token } = useParams();
  const info = useQuery({ queryKey: ["fb-survey", token], queryFn: () => api<Info>(`/api/public/feedback/${token}`), retry: false });
  const [lang, setLang] = useState<Lang>("en");
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  useEffect(() => { if (info.data) setLang(info.data.language); }, [info.data]);
  const submit = useMutation({ mutationFn: () => post(`/api/public/feedback/${token}`, { rating, comment }) });
  const t = T[lang] ?? T.en;
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
                {Object.entries(LANGUAGE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            {submit.isSuccess || info.data.submitted ? (
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-emerald-800" role="status" data-testid="feedback-done">
                <p className="flex items-start gap-2 font-medium"><CheckCircle2 className="mt-0.5 size-5 shrink-0" /> {submit.isSuccess ? t.done : t.already}</p>
              </div>
            ) : (
              <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
                <p className="text-sm text-slate-600">{t.intro.replace("{exam}", info.data.exam_name).replace("{site}", info.data.site_name)}</p>
                <fieldset className="rounded-xl border border-slate-200 bg-white p-4">
                  <legend className="sr-only">{t.rating}</legend>
                  <p className="mb-2 text-sm font-medium text-slate-800">{t.rating}</p>
                  <div className="flex justify-between">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button key={n} type="button" onClick={() => setRating(n)} className="grid size-12 place-items-center rounded-lg" aria-label={`${n} / 5`} aria-pressed={rating === n} data-testid={`star-${n}`}>
                        <Star className={`size-9 ${n <= rating ? "fill-amber-400 text-amber-400" : "text-slate-300"}`} />
                      </button>
                    ))}
                  </div>
                </fieldset>
                <label className="block rounded-xl border border-slate-200 bg-white p-3">
                  <span className="mb-2 block text-sm text-slate-800">{t.comment}</span>
                  <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={4} className="w-full rounded-lg border border-slate-300 p-2 text-base" aria-label={t.comment} />
                </label>
                {submit.error && <p className="text-sm text-rose-600" role="alert">{(submit.error as Error).message}</p>}
                <Button type="submit" variant="primary" className="h-11 w-full text-base" disabled={rating === 0} loading={submit.isPending} data-testid="send-feedback">{t.submit}</Button>
              </form>
            )}
            <p className="text-center text-xs text-slate-400">Demo · synthetic data</p>
          </div>
        )}
      </main>
    </div>
  );
}
