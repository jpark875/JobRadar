# JobRadar

JobRadar pulls job postings from many sites into one place, ranks every posting against your resume, and alerts you when a strong new match shows up. It also includes tools that make it realistic to apply to 10+ jobs a day:

- a daily application queue
- an answer bank
- per-job cover letters
- an application tracker with follow-up reminders
- a browser extension that autofills application forms

It runs locally on your machine. Your resume, credentials and application history stay in a SQLite database in `./data`, and saved passwords and API keys are encrypted.

## Features

**Connections**
- **Job alert emails** from Indeed, LinkedIn, Handshake, Glassdoor, ZipRecruiter, Wellfound and Monster. JobRadar reads them from your inbox over IMAP (read-only) and extracts every posting. You can also paste or upload a single alert email.
- **JSearch API**: Indeed, LinkedIn, Glassdoor, ZipRecruiter and other boards indexed by Google for Jobs.
- **Adzuna, Jooble and USAJOBS** aggregator APIs.
- **Company career pages** on Greenhouse, Lever, Ashby and SmartRecruiters.
- **The Muse, Remotive and Remote OK.**
- **Manual entry**, or one click from the browser extension, for anything else.

**Recommendations**
- Upload a PDF, DOCX or TXT resume. JobRadar extracts your skills (about 150 skills and their aliases) and estimates your years of experience.
- Each posting gets a 0-100 score built from:
  - skill coverage
  - title similarity to your target roles
  - TF-IDF text similarity between your resume and the posting
  - seniority fit (detected from the title and phrases like "5+ years")
  - location and remote fit
  - recency
  - your salary floor
- Every score comes with reasons: skills you match, skills the posting lists that your resume doesn't, and warnings like "Asks for 7+ years".
- Hard filters (excluded title words or companies, remote only, must-have keywords, maximum age) hide postings without deleting them.

**Alerts**
- After each scheduled sync, you get one alert listing new postings above your score threshold.
- A daily digest of your best open matches.
- Channels: phone push through [ntfy](https://ntfy.sh), email (SMTP), Discord, Slack, or any webhook.

**Applying faster**
- **Today's queue** fills itself with your best open matches up to your daily goal. Work top to bottom with Apply / Done / Skip, and track your progress and streak.
- **Bulk actions** on the job list: queue, save, dismiss or mark applied many jobs at once (shift-click selects a range).
- **Cover letters** generated per job from your template. Placeholders such as `{company}`, `{title}` and `{top_skills}` are filled in, and `{top_skills}` uses the skills that matched the posting.
- **Answer bank** for repeated questions like sponsorship, salary and "why us", with per-job placeholders and copy buttons.
- **Keyword gaps** list the skills a posting asks for that aren't on your resume.
- **Tracker** board covering saved through offer/rejected. Follow-up dates are set automatically when you apply.
- **Browser extension** that:
  - autofills Greenhouse, Lever, Ashby, Workday, SmartRecruiters, iCIMS, Jobvite, Workable and similar forms from your profile
  - answers dropdown, radio and text questions from your answer bank
  - pastes your cover letter
  - marks the job as applied when you submit

## Quick start

Requires Python 3.10+.

```bash
git clone https://github.com/jpark875/JobRadar.git
cd JobRadar
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
jobradar serve
```

Open http://127.0.0.1:8765, then:

1. **Profile**: upload your resume, then set target titles, locations, level and filters.
2. **Connections**: add sources. A good starting set is email alerts plus JSearch, plus Greenhouse/Lever boards for companies you like.
3. **Alerts**: add ntfy (free phone push) or another channel and hit Test.

The app syncs on the interval you choose (60 minutes by default) while it's running. To sync from cron instead:

```bash
jobradar sync          # fetch, score, alert
jobradar top           # print best open matches
jobradar digest        # send the digest now
jobradar resume ~/resume.pdf
jobradar rescore
```

Configuration lives in environment variables or a `.env` file. See `.env.example`.

## Connecting Indeed, LinkedIn and Handshake

These sites don't offer job-seeker APIs, and their terms prohibit scraping or logging in with bots. JobRadar therefore uses the channels they support:

1. On each site, create job alerts for your searches:
   - **Indeed**: "Get new jobs for this search by email"
   - **LinkedIn**: "Set alert", with daily email
   - **Handshake**: "Save search" with email notifications
2. In JobRadar, add **Email job alerts** with your IMAP server and an **app password**. For Gmail, enable 2-step verification and then create one at https://myaccount.google.com/apppasswords.
3. Optionally add **JSearch** (free tier on RapidAPI). It returns Indeed and LinkedIn listings with full descriptions, which gives better scores than alert emails alone.

The extension, the answer bank and your profile make applying on any of these sites faster. Logging in still happens in your own browser.

## Browser extension

1. Open `chrome://extensions` and turn on Developer mode.
2. Click **Load unpacked** and select the `extension/` folder.
3. Keep `jobradar serve` running. The extension talks to it at `http://127.0.0.1:8765`, which you can change in the popup.

On a supported application page, click **Autofill with JobRadar** (bottom right), review everything, attach your resume file, and submit. The popup shows today's queue and can save the current page as a job or mark it applied.

## Free API keys

| Source | Where |
| --- | --- |
| JSearch (Indeed, LinkedIn, Glassdoor...) | https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch |
| Adzuna | https://developer.adzuna.com |
| Jooble | https://jooble.org/api/about |
| USAJOBS | https://developer.usajobs.gov |
| The Muse (optional) | https://www.themuse.com/developers/api/v2 |

Greenhouse, Lever, Ashby, SmartRecruiters, Remotive and Remote OK need no key. For career-page boards, enter the company's board name from its jobs URL, for example `stripe` from `boards.greenhouse.io/stripe`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Project layout:

```
jobradar/
  sources/      connectors (APIs, career pages, email alerts)
  matching.py   scoring and recommendation logic
  resume.py     resume text extraction and experience estimate
  skills.py     skill taxonomy
  service.py    sync, dedupe, alerts, queue
  notify.py     ntfy / email / Discord / Slack / webhook
  apply.py      cover letter and answer templating
  web/          FastAPI app, templates, static files
extension/      Chrome extension (Manifest V3)
tests/
```

## Privacy and security

- The server binds to `127.0.0.1` by default. Don't expose it publicly; it has no login.
- Connection and alert credentials are encrypted with a key stored in `data/secret.key`, or in `JOBRADAR_SECRET_KEY` if you set it.
- Requests that change data must come from the app itself or the extension. Cross-site requests are rejected.
