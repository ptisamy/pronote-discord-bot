# PronoteDiscord

Un bot Discord qui surveille Pronote et envoie automatiquement les nouvelles notes et les nouveaux devoirs dans un salon Discord.

## 

## Fonctionnalités

* Détection automatique des nouvelles notes.
* Détection automatique des nouveaux devoirs.
* Notifications directement sur Discord.
* Vérification de Pronote toutes les 5 minutes.
* Authentification Pronote par QR code.

## 

## Prérequis

* Python 3.10 ou supérieur.
* Un compte Discord.
* Un compte Pronote.
* Git (facultatif).

## 

## 1\. Installation

Clone le dépôt Git :


git clone https://github.com/ptisamy/pronote-discord-bot.git

puis

cd PronoteDiscord


Installe ensuite les dépendances :


py -m pip install -r requirements.txt


## 2\. Création du bot Discord

Pour envoyer les notifications, tu dois d'abord créer un bot Discord.

### 

### Étape 1 — Créer une application

1. Va sur le [Discord Developer Portal](https://discord.com/developers/applications).
2. Connecte-toi avec ton compte Discord.
3. Clique sur **New Application**.
4. Donne un nom à ton application (par exemple `PronoteDiscord`).
5. Accepte les conditions et clique sur **Create**.

### Étape 2 — Créer le bot

1. Dans le menu de gauche, ouvre l'onglet **Bot**.
2. Clique sur **Add Bot** si nécessaire.
3. Personnalise son nom et son avatar si tu le souhaites.
4. Dans la section **Token**, récupère ton token Discord.



Attention : Le token est un mot de passe. Ne le partage jamais et ne le publie surtout pas sur GitHub. Si tu le divulgues, régénère-le immédiatement.

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


DISCORD_TOKEN=TON_TOKEN_DISCORD

DISCORD_CHANNEL_ID=123456789012345678



Remplace les valeurs par ton token et l'identifiant du salon.

## 3\. Authentification Pronote

L'authentification Pronote est séparée du bot Discord.

Lance le script :


py pronote\_auth.py


Le script te demandera :

* Le chemin vers l'image du QR code Pronote.
* Ton code PIN à 4 chiffres.

Une fois l'authentification réussie, les informations de session seront enregistrées dans :

data/session.json

Attention : Ce fichier contient des informations permettant de se connecter à ton compte Pronote. Ne le partage jamais.

## 4\. Lancement du bot

Une fois l'authentification Pronote terminée, lance :



py bot.py


Le bot va alors :

1. Se connecter à Discord.
2. Se connecter à Pronote.
3. Récupérer les notes et les devoirs.
4. Comparer les données avec la dernière vérification.
5. Envoyer une notification si une nouvelle note ou un nouveau devoir est détecté.

Les vérifications sont effectuées toutes les 5 minutes.

Lors du premier lancement, le bot enregistre les données existantes comme référence. Il ne devrait donc pas envoyer de notifications pour les anciennes notes et les anciens devoirs.

## 

## 5\. Structure du projet

PronoteDiscord/

    ├── bot.py

    ├── pronote\_auth.py

    ├── config.py

    ├── requirements.txt

    ├── .env

    ├── README.md

    ├── .gitignore

    └── data/
  
        ├── session.json
  
        ├── app\_uuid.txt
  
        └── last\_check.json


## 

## 6\. Dépannage

|Problème|Solution|
|-|-|
|Le bot ne se connecte pas à Discord|Vérifie que le token dans `.env` est correct.|
|Le bot n'envoie aucun message|Vérifie l'identifiant du salon et les permissions du bot.|
|La connexion à Pronote échoue|Vérifie ta connexion Internet et l'accès à Pronote depuis ton réseau.|
|Le QR code n'est pas reconnu|Vérifie que l'image est nette et lisible.|
|La session Pronote ne fonctionne plus|Relance `pronote\_auth.py` pour renouveler l'authentification.|

Vérifie que ces fichiers sont bien exclus de Git grâce au fichier .gitignore

