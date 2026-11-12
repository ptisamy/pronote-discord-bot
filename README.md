# Pronote Bot

Un bot Discord qui surveille Pronote et envoie automatiquement les nouvelles notes, les changements d’emploi du temps et les nouveaux devoirs dans un salon Discord.

## 

## Fonctionnalités

* Détection automatique des nouvelles notes.
* Détection automatique des nouveaux devoirs.
* Détection automatique des changements d'emploi du temps
* Notifications directement sur Discord.
* Vérification de Pronote toutes les 5 minutes.
* Possibilité de scanner Pronote manuellement
* Authentification Pronote par QR code.

## 

## Prérequis

* Python 3.10 ou supérieur.
* Un compte Discord.
* Un compte Pronote.
* Git (facultatif).

## 

## 1\. Installation

Clone le dépôt GitHub :


`git clone https://github.com/ptisamy/pronote-discord-bot.git`

Installe ensuite les dépendances :


`py -m pip install -r requirements.txt`


## 2\. Création du bot Discord

Pour envoyer les notifications, tu dois d'abord créer un bot Discord.

### 

### Étape 1 — Créer une application

1. Va sur le [Discord Developer Portal](https://discord.com/developers/applications).
2. Connecte-toi avec ton compte Discord.
3. Clique sur **New Application**.
4. Donne un nom à ton application (par exemple `Pronote Bot`).
5. Accepte les conditions et clique sur **Create**.

### Étape 2 — Créer le bot

1. Dans le menu de gauche, ouvre l'onglet **Bot**.
2. Clique sur **Add Bot** si nécessaire.
3. Personnalise son nom et son avatar si tu le souhaites.
4. Dans la section **Token**, récupère ton token Discord.



Attention : Le token est un mot de passe. Ne le partage jamais à qui que ce soie.

### 

### Étape 3 — Inviter le bot sur ton serveur

1. Ouvre l'onglet **OAuth2**, puis **URL Generator**.
2. Dans **Scopes**, coche :

   * bot
   * applications.commands
3. Dans **Bot Permissions**, coche :

   * View Channels
   * Send Messages
   * Embed Links
4. Copie l'URL générée en bas de page.
5. Ouvre cette URL dans ton navigateur.
6. Sélectionne ton serveur Discord et autorise l'ajout du bot.



Le bot devrait maintenant apparaître dans ton serveur.

### 

### Étape 4 — Récupérer l'identifiant du salon

1. Dans Discord, ouvre **Paramètres utilisateur → Avancé**.
2. Active le **Mode développeur**.
3. Fais un clic droit sur le salon dans lequel tu souhaites recevoir les notifications.
4. Clique sur **Copier l'identifiant du salon**.

### 

### Étape 5 — Configurer le fichier `.env`

À la racine du projet, crée un fichier nommé `.env` contenant :


`DISCORD_TOKEN=TON_TOKEN_DISCORD`

`DISCORD_CHANNEL_ID=L’ID_DU_SALON`



Remplace les valeurs par ton token et l'identifiant du salon.

## 3\. Authentification Pronote

L'authentification Pronote est séparée du bot Discord.

Lance le script :


`py pronote_auth.py`


Le script te demandera :

* Le chemin vers l'image du QR code Pronote.
* Ton code PIN à 4 chiffres.

Une fois l'authentification réussie, les informations de session seront enregistrées dans :

data/session.json

Attention : Ce fichier contient des informations permettant de se connecter à ton compte Pronote. Ne le partage jamais.

## 4\. Lancement du bot

Une fois l'authentification Pronote terminée, lance :

`py app.py`


Le bot va alors :

1. Se connecter à Discord.
2. Se connecter à Pronote.
3. Récupérer les notes, les changements d'emploi du temps et les devoirs.
4. Comparer les données avec la dernière vérification.
5. Envoyer une notification si une nouvelle note, un nouveau devoir ou un changement d’EdT est détecté.

Les vérifications de notes et de devoirs sont effectuées toutes les 5 minutes, l’EdT toutes les heures.

Lors du premier lancement, le bot peut afficher toutes les notifs de devoirs et de notes. C’est parfaitement normal !

## 

## 5\. Structure du projet

PronoteBot/

    ├── app.py
    ├── pronote_auth.py
    ├── requirements.txt
    ├── .env
    ├── README.md
    ├── .gitignore
    └── data/
         ├── session.json
         ├── app_uuid.txt
         └── last_check.json


## 

## 6\. Dépannage

|Problème|Solution|
|-|-|
|Le bot ne se connecte pas à Discord|Vérifie que le token dans `.env` est correct.|
|Le bot n'envoie aucun message|Vérifie l'identifiant du salon et les permissions du bot.|
|La connexion à Pronote échoue|Vérifie ta connexion Internet et l'accès à Pronote depuis ton réseau.|
|Le QR code n'est pas reconnu|Vérifie que l'image est nette et lisible.|
|La session Pronote ne fonctionne plus|Relance `pronote_auth.py` pour renouveler l'authentification.|



























:33333
