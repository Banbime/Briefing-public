# Briefing matinal gratuit — prototype

Prototype pour produire chaque jour à 7 h (heure de Paris) une page web et un audio en français comprenant :
- météo d'Ambérieu-en-Bugey ;
- 3 actualités mondiales issues de flux RSS de médias ;
- 3 articles de Nature, dont au moins 1 de Nature Chemistry ;
- 3 articles de Building and Environment, filtrés sur la qualité de l'air intérieur et le confort thermique.

Le PC personnel n'a pas besoin d'être allumé : GitHub Actions exécute le script dans le cloud.
Le site généré est publié sur GitHub Pages. Le dépôt et la page seront publics : ne pas y mettre de données privées.

## Gratuité et limites
- GitHub Actions et GitHub Pages peuvent suffire gratuitement pour un petit projet personnel, sous réserve des quotas et conditions GitHub.
- Open-Meteo fournit la météo sans clé API pour un usage non commercial.
- La génération IA utilise la clé Gemini facultative `GEMINI_API_KEY`, obtenue via le niveau gratuit disponible de Google AI Studio. Les quotas et conditions peuvent changer. Sans clé, le prototype utilise les résumés et descriptions des flux RSS sans réécriture par IA.
- L'audio utilise `edge-tts`, un outil communautaire qui s'appuie sur le service vocal en ligne de Microsoft ; ce n'est pas une garantie de service officiel ou permanent.
- Les flux RSS et leur contenu peuvent changer, être indisponibles ou ne donner que des résumés. Le prototype ne contourne pas les paywalls et ne télécharge pas les textes intégrals protégés.
- L'heure planifiée est 7 h Europe/Paris, mais GitHub Actions ne garantit pas une exécution à la minute exacte.

## Mise en route depuis un Samsung Galaxy Z Fold6
1. Ouvrir GitHub dans le navigateur du téléphone et créer un compte si nécessaire.
2. Créer un dépôt **public** nommé `briefing-matinal` (public pour pouvoir utiliser GitHub Pages simplement et gratuitement).
3. Importer les fichiers de ce projet ZIP dans le dépôt, en conservant leur arborescence.
4. Dans **Settings > Pages**, choisir **GitHub Actions** comme source de déploiement.
5. Dans **Settings > Secrets and variables > Actions**, ajouter un secret nommé `GEMINI_API_KEY` (facultatif). Pour éviter la clé, ne pas ajouter ce secret : les résumés RSS seront utilisés.
6. Dans **Actions**, activer les workflows si GitHub le demande, puis lancer `Build daily briefing` avec **Run workflow** pour le premier essai.
7. Ouvrir **Settings > Pages** pour trouver l'adresse du site. Ajouter cette page aux favoris de Chrome sur le téléphone.

## Sources configurées
- Le Monde (flux RSS de la une, à vérifier selon disponibilité)
- New Scientist
- Nature
- Nature Chemistry
- Building and Environment / ScienceDirect
- Open-Meteo pour la météo

Les flux peuvent évoluer. Si un flux ne fonctionne plus, il faudra remplacer son URL.

## Remarques
- La sélection des 3 articles de Nature inclut en priorité un article provenant du flux Nature Chemistry.
- Building and Environment est filtré par mots-clés en anglais liés à indoor air quality, ventilation, thermal comfort, overheating et occupant comfort.
- L'IA ne doit pas inventer les éléments manquants : les titres, dates et liens proviennent des flux RSS. Vérifier les articles originaux pour les décisions professionnelles.
