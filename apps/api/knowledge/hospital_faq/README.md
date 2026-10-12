# Hospital call-centre FAQ (spec 7.4)

The call centre answers questions about visiting hours, where departments are, and parking from these files
(`app/modules/voice_services/kb.py`: keyword retrieval, no embeddings, no internet). No identity check is needed
for them, and nothing is written back.

**Synthetic content for the demo hospital (Lakeside General Hospital, a made-up name).** Hours, floors and rates
are invented; a real deployment would load the hospital's own approved information.

Format: a header block (`id`, `title`) between `---` lines, then one `## <section id>` per section with a
`topic:` line (visiting_hours, location, parking, general), a `keywords:` line (English and Chinese, comma
separated) and the answer in `en:`, `zh-Hans:` and `zh-Hant:` lines. The answer is read out as written, in the
caller's language; the model only picks the topic, it never writes the answer.
