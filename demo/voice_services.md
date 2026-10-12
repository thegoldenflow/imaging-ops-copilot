# Demo: patient voice services (WP8, 3 minutes)

What it shows: spec 7.4. A hospital-wide call centre that answers general questions from a knowledge base, checks
who is calling before anything personal, proposes a new appointment time that a clerk confirms in one click, never
answers a clinical question (the nurse line gets it) and puts the caller through to a person when asked, when
distressed or after two turns it does not understand. And the follow-up call 48 hours after discharge: questions
written from the patient's signed instructions, in the patient's language, red-flag rules first, a high-priority
nurse Task at the top of the nurse's queue when something is wrong, the patient told when a nurse calls back.

Calls are text-simulated: you play the caller by typing (or speaking, in Chrome or Edge: the browser's own speech
recognition) and the browser reads the agent's replies aloud. Only text reaches the server; no audio is recorded.

Setup: `docker compose up -d postgres`, start the API and the web app (on this machine:
`LLM_PROVIDER=mock PW_API_PORT=9433 PW_WEB_PORT=5197 VITE_API_PROXY_TARGET=http://127.0.0.1:9433`). Press **Reset
demo**. No API key needed: the mock models run. Logins: Maya Nakamura (clerk), Jonah Eriksen (nurse, Medicine A).

The planted cases (app/ehr/seed/voice.py; `GET /api/voice/demo` lists them with the lines to say) on the Saturday
2026-10-10 data: caller **George Gordon** (MRN 40000059, two fracture-clinic visits), follow-up patient **郭明 Ming
Guo** (MRN 40000116, Simplified Chinese, discharged from Medicine A on Tuesday after a coronary stent, signed
instructions). The call centre's **ID: en_fracture** (and other **ID:** …) quick phrases say the caller's identity line for you; the follow-up
console has a **出生日期** quick answer with the patient's birth date.

Say once, at the start: "Everything is synthetic. The calls are typed, not recorded. The agent proposes; a clerk or a
nurse confirms. It never gives medical advice."

## Script (web app)

1. **0:00 — General information without identity.** Sign in as the clerk, **Call centre** → **Start call**. The
   greeting offers Chinese ("需要中文服务请直接说中文"). Type *What are the visiting hours?* — the answer comes from
   the hospital knowledge base (**Answered from the FAQ** under the call's actions), and **Call details** still says
   **Not verified**: nobody has to identify themselves for a public answer.

2. **0:20 — Reschedule after the identity check.** *I need to reschedule my fracture clinic appointment.* The agent
   asks for the patient's full name, date of birth and last four card digits before anything personal. Pick
   **ID: en_fracture** (George Gordon; or type the line): **Verified**. The agent offers three open times; *The second
   one* → "To confirm: move your fracture clinic visit from … to …?" → *Yes, please*. "I've asked our booking clerk
   to move your appointment." The action says **Reschedule requested (clerk confirms)**: a proposed Appointment and an approval Task,
   nothing booked yet. "Two wrong identity answers and the caller goes to a person; the agent never guesses."

3. **0:55 — The clerk's one click.** Under **Requests to confirm**: the reschedule, old time and new time side by
   side. **Confirm reschedule** → the new time is booked and the old one cancelled in the same step (the booking is
   a privileged tool: it runs only on the clerk's approval). The list says "Nothing to confirm".

4. **1:10 — A clinical question is never answered.** Back on the call: *Is it normal that my ankle is swollen?* →
   "I'm not able to answer medical questions. I've passed your question to our nurse line …, call 911 if it gets
   worse". The actions show **Nurse line: Communication + Task**. *No, that's all, thanks* ends the call;
   the summary lists what happened. Optional: a new call, quick phrase **转人工** — straight through to a person, in Chinese.

5. **1:30 — The Chinese follow-up call escalates.** **Switch role** → the nurse → **Follow-up calls**. The stays
   discharged 48 hours ago are in the **Calls** tab; those missing a condition (no consent, no phone number,
   instructions not signed) are already manual follow-ups in the **Queue**. Open **郭明** → **Place call**: the agent
   introduces itself in Mandarin and asks for the date of birth (quick answer **出生日期**). "How are you feeling?" —
   type *还行，就是伤口一直在渗液，纱布都湿透了* (the wound keeps draining, the gauze is soaked). At once: "谢谢您告诉我。
   护士会在30分钟内给您回电话。" — the red-flag rule caught it, no model needed. **没有了** ends the call: "如果在这之前
   情况加重，请拨打911。再见。" "Recall first: when in doubt the call escalates; a classifier below 0.7 confidence
   escalates too."

6. **2:15 — The nurse queue.** **Queue**: Ming Guo is first, **Urgent — Heavy wound drainage**, with her own words
   (*伤口一直在渗液*) and the nav badge counting escalations. **As the AI saw it (de-identified)** shows the transcript
   the classifier got: no name, no birth date. Write *Called back: the dressing is soaked twice a day; asked her to
   come in today.* → **Save call-back note**, then *Wound review by the physician today.* → **Refer to a physician**.
   Each is a Communication on the chart; the referral is an urgent physician Task. "Clerks cannot see this queue;
   physicians see it but the nurse acts."

7. **2:45 — Close.** "The patient-reported answers went back as preliminary Observations, the call as a
   Communication. The agent asked; the nurse decides."

## In the terminal (no browser)

```bash
cd apps/api && uv run python scripts/voice_demo.py
```

The same story in one run (rolled back unless `--commit`): the call-centre call with the clerk's confirmation, the
follow-up sweep with its manual follow-ups, the Chinese call escalating on the rules, and the nurse's call-back and
referral.

## Evals

```bash
cd apps/api && uv run python scripts/voice_eval.py
```

Call centre (40 scripts): intent accuracy, identity failures transferred. Follow-up (50 scripted calls, 15 with red
flags): recall with and without the model, over-escalation. Bilingual (10 English + 10 Chinese, paired): the same
decision in both languages. Reports under `evals/call_center`, `evals/followup`, `evals/voice_bilingual`; the prompt
injection extension in `evals/injection`.
