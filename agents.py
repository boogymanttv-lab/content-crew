"""The four agents. Each returns (data, usage) and validates its own output."""
import llm
from llm import LLMError

SYSTEM = (
    "You are one member of an AI content crew that makes short vertical videos. "
    "Respond with only valid JSON and no markdown."
)

PLATFORM_FORMATS = {
    "TikTok": "TikTok video",
    "Instagram": "Instagram Reel",
    "YouTube": "YouTube Short",
    "Facebook": "Facebook Reel",
}


def brand_context(brand):
    return (
        f"Brand: {brand['name']}\n"
        f"Niche: {brand['niche']}\n"
        f"Audience: {brand['audience'] or 'general viewers interested in the niche'}\n"
        f"Tone: {brand['tone'] or 'clear, friendly, practical'}\n"
        f"Language: {brand['language']}\n"
        f"Brand rules: {brand['rules'] or 'none'}\n"
        "Global rules: no invented statistics, no income or revenue promises, no claims about "
        "named companies or people, no medical, legal or financial advice stated as certain."
    )


def _need(data, key, kind):
    value = data.get(key)
    if not isinstance(value, kind) or not value:
        raise LLMError(f"The agent's answer was incomplete ({key}). Try again.")
    return value


# ---- Scout -----------------------------------------------------------------

def scout(brand):
    prompt = (
        brand_context(brand)
        + "\n\nRole: Scout. Propose 5 distinct video topics for this brand.\n"
        'JSON: {"ideas":[{"title":"","hook":"first spoken sentence, under 14 words",'
        '"why":"one sentence on why the audience cares"}]}'
    )

    def mock():
        n = brand["niche"]
        return {
            "ideas": [
                {"title": f"The first mistake beginners make in {n}", "hook": f"Most people start {n} the wrong way.", "why": "Mistakes are easy to recognise and share."},
                {"title": f"One simple habit that improves {n}", "hook": "This takes two minutes a day.", "why": "Small habits feel doable."},
                {"title": f"A myth about {n} explained", "hook": "You have probably heard this myth.", "why": "Myths invite comments."},
                {"title": f"A tool that helps with {n}", "hook": "I wish I had found this sooner.", "why": "Tools give a concrete next step."},
                {"title": f"Three questions to ask before starting {n}", "hook": "Ask these three questions first.", "why": "Checklists get saved."},
            ]
        }

    data, usage = llm.generate_json(SYSTEM, prompt, mock)
    ideas = _need(data, "ideas", list)[:5]
    for idea in ideas:
        if not isinstance(idea, dict) or not idea.get("title") or not idea.get("hook"):
            raise LLMError("An idea was incomplete. Try again.")
        idea.setdefault("why", "")
    return ideas, usage


# ---- Writer ----------------------------------------------------------------

def writer(brand, topic, note=""):
    prompt = (
        brand_context(brand)
        + f"\n\nRole: Writer. Write a script for this topic.\nTopic: {topic['title']}\nOpening hook: {topic['hook']}\n"
        + (f"Editor note to apply: {note}\n" if note else "")
        + "Spoken length 80 to 110 words in total. JSON: "
        '{"hook":"","beats":["3 to 5 spoken lines"],"cta":"one line call to action","caption":"under 150 characters"}'
    )

    def mock():
        return {
            "hook": topic["hook"],
            "beats": [
                f"Here is the short version of {topic['title'].lower()}.",
                "Start with one clear goal and write it down.",
                "Test it on a small scale before you change everything.",
                "Review what happened after a week and adjust.",
            ],
            "cta": "Follow for one practical tip every day.",
            "caption": topic["title"][:140],
        }

    data, usage = llm.generate_json(SYSTEM, prompt, mock)
    _need(data, "hook", str)
    _need(data, "beats", list)
    data.setdefault("cta", "")
    data.setdefault("caption", topic["title"][:140])
    return data, usage


# ---- Designer --------------------------------------------------------------

def designer(brand, script, note=""):
    prompt = (
        brand_context(brand)
        + "\n\nRole: Designer. Plan the visuals for this script. Vertical 9:16. Use screen recordings, "
        "simple animated text and graphics. No stock footage of real people.\n"
        f"Hook: {script['hook']}\nBeats: {' | '.join(script['beats'])}\nCTA: {script.get('cta', '')}\n"
        + (f"Editor note to apply: {note}\n" if note else "")
        + 'JSON: {"thumbnailText":"max 4 words","thumbnailVisual":"one sentence",'
        '"shots":[{"time":"0-3s","visual":"","onScreenText":""}]} with 5 to 7 shots covering the full length.'
    )

    def mock():
        return {
            "thumbnailText": "Start here",
            "thumbnailVisual": "Large bold text on a dark background with a simple icon.",
            "shots": [
                {"time": "0-3s", "visual": "Big text on a plain background", "onScreenText": script["hook"][:60]},
                {"time": "3-12s", "visual": "Animated checklist appears line by line", "onScreenText": "Step 1"},
                {"time": "12-24s", "visual": "Screen recording of a simple example", "onScreenText": "Step 2"},
                {"time": "24-34s", "visual": "Before and after comparison graphic", "onScreenText": "Step 3"},
                {"time": "34-40s", "visual": "Follow prompt with the account name", "onScreenText": script.get("cta", "")[:50]},
            ],
        }

    data, usage = llm.generate_json(SYSTEM, prompt, mock)
    _need(data, "thumbnailText", str)
    _need(data, "shots", list)
    data.setdefault("thumbnailVisual", "")
    return data, usage


# ---- Publisher -------------------------------------------------------------

def publisher(brand, platforms, topic, script, note=""):
    names = ", ".join(PLATFORM_FORMATS[p] + f" (key {p})" for p in platforms)
    prompt = (
        brand_context(brand)
        + f"\n\nRole: Publisher. Adapt this post for each platform: {names}.\n"
        f"Topic: {topic['title']}\nHook: {script['hook']}\nBase caption: {script.get('caption', '')}\n"
        f"CTA: {script.get('cta', '')}\n"
        + (f"Editor note to apply: {note}\n" if note else "")
        + 'JSON: {"variants":[{"platform":"key as given","caption":"","hashtags":["#tag"],'
        '"bestTime":"typical weekday window, to be tested","note":"one format tip"}]} '
        "with one variant per platform key and 3 to 5 hashtags each."
    )

    def mock():
        return {
            "variants": [
                {
                    "platform": p,
                    "caption": f"{script.get('caption', topic['title'])} ({PLATFORM_FORMATS[p]})",
                    "hashtags": ["#tips", "#howto", "#learn"],
                    "bestTime": "Weekday 12:00 to 14:00 local time, to be tested",
                    "note": "Keep the first two seconds on the hook.",
                }
                for p in platforms
            ]
        }

    data, usage = llm.generate_json(SYSTEM, prompt, mock)
    variants = _need(data, "variants", list)
    by_platform = {}
    for v in variants:
        if isinstance(v, dict) and v.get("platform") in platforms and v.get("caption"):
            v["caption"] = str(v["caption"])[:2200]
            v["hashtags"] = [str(t) for t in (v.get("hashtags") or [])][:5]
            v.setdefault("bestTime", "")
            v.setdefault("note", "")
            by_platform.setdefault(v["platform"], v)
    missing = [p for p in platforms if p not in by_platform]
    if missing:
        raise LLMError("The agent skipped: " + ", ".join(missing) + ". Try again.")
    return [by_platform[p] for p in platforms], usage
