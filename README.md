PronoteDiscord
Un bot Discord qui surveille Pronote et envoie automatiquement les nouvelles notes et les nouveaux devoirs dans un salon Discord.

Fonctionnalités
Détection automatique des nouvelles notes.
Détection automatique des nouveaux devoirs.
Notifications directement sur Discord.
Vérification de Pronote toutes les 5 minutes.
Authentification Pronote par QR code.

Prérequis
Python 3.10 ou supérieur.
Un compte Discord.
Un compte Pronote.
Git (facultatif).

1. Installation
Clone le dépôt Git :

git clone URL_DU_DEPOT
cd PronoteDiscord

Installe ensuite les dépendances :

py -m pip install -r requirements.txt

2. Création du bot Discord
Pour envoyer les notifications, tu dois d'abord créer un bot Discord.

Étape 1 — Créer une application
Va sur le Discord Developer Portal.
Connecte-toi avec ton compte Discord.
Clique sur New Application.
Donne un nom à ton application (par exemple `PronoteDiscord`).
Accepte les conditions et clique sur Create.
Étape 2 — Créer le bot
Dans le menu de gauche, ouvre l'onglet Bot.
Clique sur Add Bot si nécessaire.
Personnalise son nom et son avatar si tu le souhaites.
Dans la section Token, récupère ton token Discord.

Attention : Le token est un mot de passe. Ne le partage jamais et ne le publie surtout pas sur GitHub. Si tu le divulgues, régénère-le immédiatement.

Étape 3 — Inviter le bot sur ton serveur
Ouvre l'onglet OAuth2, puis URL Generator.
Dans Scopes, coche :
bot
applications.commands
Dans Bot Permissions, coche :
View Channels
Send Messages
Embed Links
Copie l'URL générée en bas de page.
Ouvre cette URL dans ton navigateur.
Sélectionne ton serveur Discord et autorise l'ajout du bot.

Le bot devrait maintenant apparaître dans ton serveur.

Étape 4 — Récupérer l'identifiant du salon
Dans Discord, ouvre Paramètres utilisateur → Avancé.
Active le Mode développeur.
Fais un clic droit sur le salon dans lequel tu souhaites recevoir les notifications.
Clique sur Copier l'identifiant du salon.

Étape 5 — Configurer le fichier `.env`
À la racine du projet, crée un fichier nommé `.env` contenant :

DISCORD_TOKEN=TON_TOKEN_DISCORD
DISCORD_CHANNEL_ID=123456789012345678

Remplace les valeurs par ton token et l'identifiant du salon.
3. Authentification Pronote
L'authentification Pronote est séparée du bot Discord.
Lance le script :

py pronote_auth.py

Le script te demandera :
Le chemin vers l'image du QR code Pronote.
Ton code PIN à 4 chiffres.
Une fois l'authentification réussie, les informations de session seront enregistrées dans :
data/session.json
Attention : Ce fichier contient des informations permettant de se connecter à ton compte Pronote. Ne le partage jamais.
4. Lancement du bot
Une fois l'authentification Pronote terminée, lance :

py bot.py

Le bot va alors :
Se connecter à Discord.
Se connecter à Pronote.
Récupérer les notes et les devoirs.
Comparer les données avec la dernière vérification.
Envoyer une notification si une nouvelle note ou un nouveau devoir est détecté.
Les vérifications sont effectuées toutes les 5 minutes.
Lors du premier lancement, le bot enregistre les données existantes comme référence. Il ne devrait donc pas envoyer de notifications pour les anciennes notes et les anciens devoirs.

5. Structure du projet
PronoteDiscord/
├── bot.py
├── pronote_auth.py
├── config.py
├── requirements.txt
├── .env
├── README.md
├── .gitignore
└── data/
├── session.json
├── app_uuid.txt
└── last_check.json

6. Dépannage
Problème	Solution
Le bot ne se connecte pas à Discord	Vérifie que le token dans `.env` est correct.
Le bot n'envoie aucun message	Vérifie l'identifiant du salon et les permissions du bot.
La connexion à Pronote échoue	Vérifie ta connexion Internet et l'accès à Pronote depuis ton réseau.
Le QR code n'est pas reconnu	Vérifie que l'image est nette et lisible.
La session Pronote ne fonctionne plus	Relance `pronote\_auth.py` pour renouveler l'authentification.

7. Sécurité
Ne publie jamais les fichiers suivants :
.env
data/session.json
data/app_uuid.txt
Ils contiennent des informations sensibles liées à tes comptes
Vérifie que ces fichiers sont bien exclus de Git grâce au fichier .gitignore
