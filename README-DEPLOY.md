# TrendPulse — دليل النشر على استضافة مجانية

الوكيل الوثائقي المستقل الذي ينتج فيديو YouTube سينمائي (10-17 دقيقة) ويرفعه
تلقائياً إلى قناتك **@AIMoneyTested** مرتين يومياً — بدون تدخل بشري.

هذه النسخة **محمولة بالكامل**: لا تعتمد على CLI خاص بالمنصة، وكل خدمة خارجية
لها مزود مجاني (أغلبها يعمل **بدون أي مفتاح**). الفيديو يُرفع مباشرة إلى
YouTube، فلا حاجة لتخزين ملفات ضخمة على الاستضافة.

---

## 1) ماذا تغيّر عن نسخة المنصة؟

| الوظيفة | نسخة المنصة (z-ai CLI) | هذه النسخة المحمولة |
|---|---|---|
| كتابة السكريبت + البحث + الميتاداتا | `z-ai chat` / `web_search` | أي مزود OpenAI-compatible، وبديل **Pollinations مجاني بلا مفتاح** |
| اكتشاف الترندات | `z-ai web_search` | **DuckDuckGo (بلا مفتاح)** أو Tavily/Brave/Serper |
| صور المشاهد الحقيقية | `z-ai image-search` | **Openverse (بلا مفتاح)** أو Pexels/Pixabay بمفتاح مجاني |
| الصور الاصطناعية للمشاهد/المصغرة | `z-ai image` | **Pollinations (بلا مفتاح)** |
| التعليق الصوتي | ElevenLabs أو `z-ai tts` | ElevenLabs (إن وُجد المفتاح) أو **edge-tts مجاني بلا مفتاح** |
| مقاطع الحركة AI | `z-ai video` | تُتخطى تلقائياً (Ken Burns سينمائي بدلاً منها) |
| المونتاج والموسيقى والمزج | ffmpeg | ffmpeg (نفس الكود) |
| الرفع | YouTube Data API | نفس الكود (OAuth refresh token) |

**النتيجة العملية:** حتى بمفاتيح صفرية يعمل خط الإنتاج كاملاً (بحث + سكريبت +
صوت + صور + رندر)، وتحتاج فقط بيانات YouTube للرفع. إضافة مفاتيح مجانية
(Gemini, ElevenLabs, Pexels) ترفع الجودة والسرعة.

---

## 2) الخيار A — GitHub Actions (موصى به: مجاني تماماً وبدون بطاقة)

GitHub يشغّل الدورة مرتين يومياً (09:00 و 21:00 بتوقيت دبي)، ويحفظ نقاط
الاستئناف في الريبو، ويرفع الفيديو إلى قناتك.

### الخطوات

1. **حساب GitHub** — أنشئه مجاناً إن لم يكن لديك.

2. **مستودع جديد** — من github.com اضغط `New repository`:
   - الاسم: `trendpulse`
   - **Public** (موصى به: دقائق Actions غير محدودة مجاناً للأكواد العامة؛
     الأسرار Secrets لا تُقرأ من أي شخص إطلاقاً حتى في الريبو العام)
   - أو Private (مجاني 2000 دقيقة/شهر — تكفي دورة يومية وربما اثنتين)
   - لا تضف README الآن.

3. **ارفع ملفات هذه الحزمة** إلى الريبو — من صفحة الريبو:
   `Add file → Upload files` واسحب **محتويات** المجلد
   `trendpulse-deploy` كاملة (بما فيها المجلدات المخفية `.github` و `lib`
   و `state` و `docker`). ملاحظة: السحب من متصفح قد يتجاهل المجلدات المخفية —
   الأضمن استخدام Git من جهازك:
   ```bash
   cd trendpulse-deploy
   git init && git add -A && git commit -m "TrendPulse initial"
   git branch -M main
   git remote add origin https://github.com/<user>/trendpulse.git
   git push -u origin main
   ```

4. **أضف الأسرار (Secrets)** — في الريبو:
   `Settings → Secrets and variables → Actions → New repository secret`

   | السر | إلزامي؟ | القيمة |
   |---|---|---|
   | `YT_CLIENT_ID` | نعم | من .env الحالي |
   | `YT_CLIENT_SECRET` | نعم | من .env الحالي |
   | `YT_REFRESH_TOKEN` | نعم | من .env الحالي |
   | `LLM_API_KEY` | اختياري | مفتاح Gemini مجاني من aistudio.google.com/apikey (بدونه يعمل Pollinations) |
   | `ELEVENLABS_API_KEY` | اختياري | صوت أدق؛ بدونه edge-tts مجاني |
   | `PEXELS_API_KEY` | اختياري | pexels.com/api — صور فوتوغرافية أجمل |
   | `TAVILY_API_KEY` | اختياري | tavily.com — بحث أدق من DuckDuckGo |
   | `YT_API_KEY` | اختياري | مفتاح API نفسه |
   | `LLM_BASE_URL` / `LLM_MODEL` | اختياري | فقط لمزودات أخرى (OpenRouter/Groq) |

   أنسخ القيم الثلاث الإلزامية من ملف `.env` الحالي للمشروع (لا تضعها في أي
   ملف داخل الريبو — Secrets فقط).

5. **فعّل Actions** — تبويب `Actions` في الريبو، وإن طُلب منك اضغط
   `I understand my workflows, go ahead and enable them`.

6. **أول تشغيل يدوي** — تبويب Actions → `TrendPulse Production` →
   `Run workflow` → شغّله وراقب السجل مباشرة. أول دورة تستغرق عادة
   40-90 دقيقة (سكريبت + صوت + صور + رندر + رفع).

7. **بعد اكتمال أول دورة** — ستجد في سجل الخطوة الأخيرة رسالة commit،
   وفي قناتك @AIMoneyTested الفيديو المنشور، وملفات `summary.json` و
   `REPORT.txt` داخل مجلد run الجديد في الريبو.

### كيف تضمن الاستمرارية؟
- كل دورة تحفظ `state.json` (نقاط الاستئناف) وترجعها للريبو بـ commit تلقائي،
  فلو انقطعت الجلسة (أو فشل خطوة) أكملت الدورة التالية من نفس النقطة.
- ملفات الفيديو/الصوت/الصور **لا تُرفع للريبو** (متجاهلة في .gitignore) —
  فقط JSON صغيرة وسجلات. الريبو يبقى بضعة ميغابايت.
- الرفع للـ YouTube يتم داخل الدورة قبل الـ commit، فلا شيء يُفقد.

### حدود GitHub المجاني
- الريبو العام: دقائق غير محدودة. الريبو الخاص: 2000 دقيقة/شهر
  (الدورة 40-90 د × 60 دورة شهرية = قد تتجاوز الحد؛ لذا ننصح بالعام).
- Job الواحد حتى 6 ساعات؛ الدورة لا تصل لهذا الحد عادة.

---

## 3) الخيار B — Docker على VPS مجاني (Oracle Cloud Always Free)

Oracle Cloud تمنح VM دائم مجاني (4 أنوية ARM + 24GB RAM — أوفر من أي جهاز
مجاني آخر، ويطلب بطاقة للتحقق فقط دون خصم):

1. أنشئ VM من `oracle.com/cloud/free` (شكل Ubuntu).
2. ثبّت Docker ثم:
   ```bash
   scp -r trendpulse-deploy ubuntu@<server-ip>:~/
   ssh ubuntu@<server-ip>
   cd trendpulse-deploy
   cp .env.example .env && nano .env   # عبّئ القيم نفسها
   docker compose up -d --build
   docker compose logs -f              # متابعة أول دورة
   ```
3. الحاوية تشتغل دورة كل 12 ساعة تلقائياً (`TRENDPULSE_INTERVAL_SECONDS`)،
   والـ checkpoints تبقى على القرص عبر مجلدات `outbox` و `state`.

بديل Oracle: أي VPS مجاني آخر أو حتى جهاز قديم في بيتك بنفس أوامر compose.

---

## 4) الخيار C — cron على أي جهاز

```bash
cd trendpulse-deploy && cp .env.example .env && nano .env
pip3 install -r requirements.txt
# كل 12 ساعة:
0 5,17 * * * cd /path/to/trendpulse-deploy && python3 run_cycle.py >> state/cron.log 2>&1
```

---

## 5) بيانات YouTube (الثلاثي الإلزامي للرفع)

- `YT_CLIENT_ID` + `YT_CLIENT_SECRET` + `YT_REFRESH_TOKEN` — موجودة حالياً
  في ملف `.env` للمشروع وتم التحقق منها برفع فعلي إلى القناة.
- التوكن يعمل طويلاً؛ إذا ألغيت الوصول يوماً (Google Account → Security →
  Third-party access) أعد التفويض وحدّث السر الثلاثي في Secrets.
- المصغرات المخصصة تتطلب توثيق الهاتف (منجز) — `thumbnails.set` يعمل الآن.

---

## 6) الأسئلة المتكررة

**هل يعمل بدون أي مفاتيح LLM/صور/صوت؟** نعم — Pollinations للنصوص والصور،
DuckDuckGo للبحث، edge-tts للصوت. الجودة أعلى بمفاتيح Gemini وElevenLabs
وPexels المجانية.

**أين أرى سجل كل دورة؟** مجلد `outbox/run_YYYYMMDD_HHMMSS/` فيه
`run.log` و `summary.json` و `REPORT.txt`؛ الدورة ترفع هذه الصغيرة للريبو
فتقرأها من واجهة GitHub.

**فشلت دورة منتصف الطريق؟** لا شيء يضيع: الدورة القادمة تلقائياً تكمل
نفس الـ run من آخر checkpoint (run_cycle.py يختار أحدث run غير مكتمل).

**أريد تشغيل اختبار سريع؟** `python3 agent.py --mini` (سكريبت مصغر
بدون بحث — للتحقق من البيئة فقط).

**كيف أوقف النشر مؤقتاً؟** من تبويب Actions في GitHub: عطّل الـ workflow،
أو في config.yaml غيّر `upload.enabled: false`، أو غيّر `privacy: private`
للمراجعة قبل النشر.

**التكلفة النهائية:** 0$ — كل المزودات المستخدمة لها طبقة مجانية دائمة.
