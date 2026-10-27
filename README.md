# Mise à jour du briefing : abstracts scientifiques

## Installation
1. Téléversez `main.py` à la racine du dépôt en remplaçant l'ancien après en avoir gardé une copie.
2. Remplacez aussi `requirements.txt` par celui fourni ici (ajout de `beautifulsoup4`).
3. Le workflow `.github/workflows/daily.yml` est inclus à titre de référence : remplacez-le seulement si vous souhaitez aussi ajouter les variables/secrets météo et Gemini au job. Le cron est en UTC, donc 07:00 UTC et non 07:00 heure française toute l'année.
4. Lancez Actions > Build daily briefing > Run workflow.

## Ce que fait ce script
- Pour les 3 articles Nature et les 3 articles Building and Environment sélectionnés, il essaie de récupérer le résumé depuis la page de l'éditeur ou ses métadonnées.
- La page web indique si le texte vient de la page éditeur, des métadonnées, ou si le script a dû utiliser le résumé RSS.
- Si `GEMINI_API_KEY` est configurée, Gemini reçoit l'abstract récupéré et peut produire le briefing en français.
- Sans Gemini, le fallback audio contient le résumé scientifique disponible en anglais. Il n'est donc pas traduit automatiquement sans service de traduction.
- Certains éditeurs peuvent bloquer les requêtes automatisées ou ne pas rendre l'abstract complet accessible.
- Les variables météo Infoclimat doivent correspondre à des URL/format valides ; ce script conserve les variables du prototype existant.

## Secrets/variables facultatifs
- Secret `INFOCLIMAT_API_KEY`
- Variable `INFOCLIMAT_OBSERVATIONS_URL`
- Variable `INFOCLIMAT_FORECAST_URL`
- Secret `GEMINI_API_KEY` (nécessaire pour la génération française du briefing à partir des abstracts)
- Variable `GEMINI_MODEL` (facultatif)
