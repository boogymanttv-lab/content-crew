# Content Crew

Multi-brand AI content pipeline. Each brand has a niche profile and one or more channels
(TikTok, Instagram, YouTube, Facebook). A crew of four agents hands a short video from topic
to platform-ready posts, and nothing moves forward without human approval.

Scout (topics) -> Writer (script) -> Designer (thumbnail and shots) -> Publisher (per-platform posts) -> queue -> results

## What works today
- Accounts, brands in different niches, several channels per brand
- Full agent pipeline with approve / redo-with-note at every stage, editable captions
- Scheduling with a per-channel daily cap (default 3), cancel, "publish due" button
- Simulated publishing (provider `dryrun`) and manual results entry per post
- Per-user AI usage counter
- Demo mode without an API key; real Claude output with `ANTHROPIC_API_KEY`

## Not built yet
- Real posting. Add a provider in `publisher.py` that calls a posting service whose app already
  has platform approvals (TikTok only allows public API posts from audited apps; Instagram
  needs a Business account). Keep the `(channel, post) -> {"external_id": ...}` contract.
- Video generation, automatic analytics collection, billing, background scheduler.

## Run locally
```
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000, create an account, add a brand and a channel, start a run.

Set `ANTHROPIC_API_KEY` (see `.env.example`) for real agents.

## Deploy on Vercel
Vercel's disk is not persistent, so the app needs Postgres there.
1. Create a Postgres database (for example Neon, available from the Vercel Storage tab) and copy its connection string.
2. In Vercel choose Add New > Project and import this GitHub repo.
3. Add environment variables: `DATABASE_URL` (the Postgres string), `SECRET_KEY` (any long random text), `ANTHROPIC_API_KEY`.
4. Deploy. Tables are created on first start.

`vercel.json` routes every request to `api/index.py` and allows 60 seconds per request because
agent calls can be slow. Your Vercel plan may cap that, and long runs may need a plan that allows it.

## Deploy elsewhere
`gunicorn wsgi:app` behind HTTPS with `SECRET_KEY` and `DATABASE_URL` set.

## Tests
`pip install -r requirements-dev.txt`, then `python -m pytest`. Set `TEST_DATABASE_URL` to also run the suite against Postgres.
