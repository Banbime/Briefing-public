import os, re, html, asyncio, datetime, pathlib, json
from zoneinfo import ZoneInfo
import requests, feedparser

TZ = ZoneInfo("Europe/Paris")
NOW = datetime.datetime.now(TZ)
ROOT = pathlib.Path(__file__).parent
SITE = ROOT / "site"
SITE.mkdir(exist_ok=True)

RSS = {
    "world": [
        ("Le Monde — À la une", "https://www.lemonde.fr/rss/une.xml"),
        ("New Scientist", "https://www.newscientist.com/feed/home/"),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Guardian — World", "https://www.theguardian.com/world/rss"),
    ],
    "nature": [
        ("Nature", "https://www.nature.com/nature.rss"),
        ("Nature News", "https://www.nature.com/subjects/scientific-community/nature.rss"),
    ],
    "chemistry": [
        ("Nature Chemistry", "https://www.nature.com/nchem.rss"),
    ],
    "building": [
        ("Building and Environment", "https://rss.sciencedirect.com/publication/science/03601323"),
    ],
}
THERMAL_TERMS = [
    "indoor air", "air quality", "ventilation", "thermal comfort", "overheating",
    "occupant comfort", "indoor environment", "co2 concentration", "particulate",
    "volatile organic", "humidity", "heat stress", "building energy"
]

def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def fetch_feed(name, url, category):
    try:
        resp = requests.get(url, timeout=18, headers={"User-Agent": "MorningBriefingPrototype/0.1"})
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        items = []
        for e in parsed.entries[:25]:
            title = clean(e.get("title", ""))
            link = e.get("link", "")
            desc = clean(e.get("summary", e.get("description", "")))
            date = clean(e.get("published", e.get("updated", "")))
            if not title or not link:
                continue
            items.append({"source": name, "title": title, "link": link, "summary": desc[:1800], "date": date, "category": category})
        return items
    except Exception as ex:
        print(f"Feed error {name}: {ex}")
        return []

def unique(items):
    seen, out = set(), []
    for x in items:
        key = x["title"].lower()
        if key not in seen:
            seen.add(key); out.append(x)
    return out

def collect(group):
    items = []
    for name, url in RSS[group]:
        items.extend(fetch_feed(name, url, group))
    return unique(items)

def weather():
    # Coordinates approximatives d'Ambérieu-en-Bugey
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": 45.96, "longitude": 5.35,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "hourly": "temperature_2m,precipitation_probability",
        "timezone": "Europe/Paris", "forecast_days": 1
    }
    try:
        r = requests.get(url, params=params, timeout=15); r.raise_for_status()
        d = r.json()["daily"]
        return (f"Ambérieu-en-Bugey : minimum {d['temperature_2m_min'][0]} °C, "
                f"maximum {d['temperature_2m_max'][0]} °C, probabilité maximale de précipitations "
                f"{d['precipitation_probability_max'][0]} %. Source : Open-Meteo.")
    except Exception as ex:
        return f"Météo indisponible pour le moment ({ex})."

def select_items():
    world = collect("world")
    nature = collect("nature")
    chemistry = collect("chemistry")
    building = collect("building")

    # Priorité à Nature Chemistry pour garantir une place à la chimie.
    selected_nature = []
    if chemistry:
        selected_nature.append(chemistry[0])
    for x in nature:
        if len(selected_nature) >= 3: break
        if x["title"].lower() not in {y["title"].lower() for y in selected_nature}:
            selected_nature.append(x)
    # Si le flux Nature général est vide, compléter avec des articles de chimie.
    for x in chemistry[1:]:
        if len(selected_nature) >= 3: break
        selected_nature.append(x)

    # Filtre thématique Building and Environment ; si peu de résultats, conserver les derniers articles
    # en signalant que le filtre est indicatif.
    ranked = []
    for x in building:
        text = (x["title"] + " " + x["summary"]).lower()
        score = sum(term in text for term in THERMAL_TERMS)
        ranked.append((score, x))
    ranked.sort(key=lambda z: (z[0], z[1].get("date","")), reverse=True)
    selected_building = [x for score, x in ranked if score > 0][:3]
    if len(selected_building) < 3:
        for x in building:
            if x not in selected_building and len(selected_building) < 3:
                selected_building.append(x)
    return world[:3], selected_nature[:3], selected_building[:3]

def gemini_rewrite(items, weather_text):
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    # Free-tier availability and model names may change; override model with GEMINI_MODEL if needed.
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    blocks = []
    for section, entries in items.items():
        blocks.append(f"\n## {section}\n")
        for i, x in enumerate(entries, 1):
            blocks.append(f"{i}. Titre: {x['title']}\nSource: {x['source']}\nDate: {x['date']}\nLien: {x['link']}\nRésumé RSS: {x['summary']}\n")
    prompt = f"""Rédige un briefing matinal en français, clair et naturel à écouter, daté du {NOW:%d/%m/%Y}.
Météo: {weather_text}
Règles: utilise uniquement les informations présentes ci-dessous; ne complète pas les détails absents; signale si le résumé source est insuffisant; n'invente aucun résultat; conserve chaque titre, source et URL. Pour les articles scientifiques, distingue résultats, interprétation et limites. Pour Building and Environment, insiste sur qualité de l'air intérieur et confort thermique si le contenu le permet. Ne présente pas les flux RSS comme des textes intégraux.
Structure: météo; 3 actualités mondiales; 3 articles de Nature dont un Nature Chemistry; 3 articles Building and Environment; conclusion très courte.
Fais environ 900 à 1300 mots au maximum et ajoute des liens Markdown sous chaque sujet.
Données:
{''.join(blocks)}"""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        r = requests.post(url, json={"contents":[{"parts":[{"text":prompt}]}],
                                     "generationConfig":{"temperature":0.2}}, timeout=90)
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as ex:
        print("Gemini error; falling back to RSS summaries:", ex)
        return None

def fallback_script(weather_text, world, nature, building):
    lines = [f"Bonjour. Voici votre briefing du {NOW:%A %d %B %Y}.", weather_text]
    for label, entries in [("Actualités mondiales", world), ("Nature, dont Nature Chemistry", nature),
                           ("Building and Environment : qualité de l'air et confort thermique", building)]:
        lines.append(label + ".")
        for i, x in enumerate(entries, 1):
            lines.append(f"Sujet {i}. {x['title']}. Source : {x['source']}. {x['summary'] or 'Aucun résumé fourni dans le flux RSS.'} Pour en savoir plus, consultez le lien associé dans la version texte.")
    lines.append("Fin du briefing. Bonne journée.")
    return "\n\n".join(lines)

async def make_audio(text, out):
    import edge_tts
    # Découpage simple pour limiter les longues requêtes de synthèse vocale
    chunks = [text[i:i+2800] for i in range(0, len(text), 2800)]
    parts = []
    for i, chunk in enumerate(chunks):
        p = SITE / f"part_{i:02d}.mp3"
        communicate = edge_tts.Communicate(chunk, "fr-FR-DeniseNeural", rate="-5%")
        await communicate.save(str(p))
        parts.append(p)
    # Concatenate MP3 chunks with ffmpeg if available; otherwise keep individual parts and make a playlist.
    import shutil, subprocess
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg and len(parts) > 1:
        listing = SITE / "parts.txt"
        listing.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)],
                       cwd=SITE, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for p in parts: p.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)
    elif len(parts) == 1:
        parts[0].replace(out)
    else:
        (SITE / "audio_playlist.txt").write_text("\n".join(p.name for p in parts), encoding="utf-8")

def item_html(x):
    return f"""<article><h3>{html.escape(x['title'])}</h3><p><small>{html.escape(x['source'])} · {html.escape(x['date'])}</small></p><p>{html.escape(x['summary'])}</p><p><a href="{html.escape(x['link'], quote=True)}" target="_blank" rel="noopener">Lire la source</a></p></article>"""

def main():
    weather_text = weather()
    world, nature, building = select_items()
    sections = {"Actualités mondiales": world, "Nature (dont Nature Chemistry)": nature,
                "Building and Environment": building}
    ai_text = gemini_rewrite(sections, weather_text)
    script = ai_text or fallback_script(weather_text, world, nature, building)
    (SITE / "briefing.txt").write_text(script, encoding="utf-8")
    audio_path = SITE / "briefing.mp3"
    try:
        asyncio.run(make_audio(script, audio_path))
    except Exception as ex:
        print("Audio generation failed:", ex)

    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Briefing matinal</title>
<style>body{{font-family:system-ui,sans-serif;max-width:850px;margin:2rem auto;padding:0 1rem;line-height:1.55}}article{{border-bottom:1px solid #ddd;padding:0.7rem 0}}audio{{width:100%}}small{{color:#555}}.weather{{padding:1rem;background:#eef5ff;border-radius:12px}}</style></head><body>
<h1>Briefing matinal — {NOW:%d/%m/%Y}</h1><p>Généré automatiquement. Les résumés s'appuient sur les métadonnées et descriptions disponibles dans les flux RSS ; consultez les sources originales.</p>
<div class="weather"><h2>Météo</h2><p>{html.escape(weather_text)}</p><small>Données météo : Open-Meteo, licence CC BY 4.0.</small></div>
<h2>Écouter le briefing</h2>
{"<audio controls src='briefing.mp3'></audio>" if audio_path.exists() else "<p>Audio indisponible pour cette exécution ; consultez le texte ci-dessous.</p>"}
<p><a href="briefing.txt">Lire ou télécharger le texte complet</a></p>
<h2>3 actualités mondiales</h2>{''.join(item_html(x) for x in world)}
<h2>3 articles de Nature (dont Nature Chemistry)</h2>{''.join(item_html(x) for x in nature)}
<h2>3 articles de Building and Environment</h2>{''.join(item_html(x) for x in building)}
<footer><p>Prototype personnel. Vérifiez les informations dans les publications originales.</p></footer></body></html>"""
    (SITE / "index.html").write_text(page, encoding="utf-8")
    print("Briefing created:", NOW.isoformat())

if __name__ == "__main__":
    main()
