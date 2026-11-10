
import discord
import pronotepy
import asyncio
import json
import os
import datetime
import hashlib

from pathlib import Path
from dotenv import load_dotenv
from discord.ext import commands, tasks


# =========================
# CONFIGURATION
# =========================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

SESSION_FILE = Path("data/session.json")
STATE_FILE = Path("data/last_check.json")

CHECK_INTERVAL = 5 * 60


# =========================
# STOCKAGE
# =========================

def load_json(path):
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)


def fingerprint(data):
    raw = json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        default=str
    )

    return hashlib.sha256(raw.encode()).hexdigest()


# =========================
# PRONOTE
# =========================

def get_client():
    credentials = load_json(SESSION_FILE)

    if not credentials:
        raise RuntimeError(
            "Session absente. Lance pronote_auth.py."
        )

    client = pronotepy.Client.token_login(**credentials)

    if not client.logged_in:
        raise RuntimeError("Connexion Pronote impossible.")

    # Les tokens Pronote peuvent changer après connexion.
    save_json(SESSION_FILE, client.export_credentials())

    return client


def fetch_data():
    client = get_client()

    grades = []
    homework = []

    for period in client.periods:
        for grade in period.grades:
            item = {
                "id": fingerprint({
                    "date": grade.date,
                    "subject": grade.subject.name,
                    "grade": grade.grade,
                    "out_of": grade.out_of,
                    "coefficient": grade.coefficient,
                    "comment": grade.comment
                }),
                "subject": grade.subject.name,
                "grade": grade.grade,
                "out_of": grade.out_of,
                "coefficient": grade.coefficient,
                "comment": grade.comment,
                "date": str(grade.date),
                "period": period.name
            }

            grades.append(item)

    for hw in client.homework(datetime.date.today()):
        item = {
            "id": fingerprint({
                "date": hw.date,
                "subject": hw.subject.name,
                "description": hw.description
            }),
            "subject": hw.subject.name,
            "description": hw.description,
            "date": str(hw.date),
            "done": hw.done
        }

        homework.append(item)

    return {
        "grades": grades,
        "homework": homework
    }


# =========================
# DISCORD
# =========================

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


def make_grade_embed(grade):
    embed = discord.Embed(
        title="Nouvelle note !",
        description=f"**{grade['subject']}**",
        color=discord.Color.green()
    )

    embed.add_field(
        name="Note",
        value=f"{grade['grade']}/{grade['out_of']}",
        inline=True
    )

    embed.add_field(
        name="Coefficient",
        value=grade["coefficient"] or "Non précisé",
        inline=True
    )

    embed.add_field(
        name="Date",
        value=grade["date"],
        inline=True
    )

    embed.add_field(
        name="Période",
        value=grade["period"],
        inline=True
    )

    if grade["comment"]:
        embed.add_field(
            name="Commentaire",
            value=grade["comment"][:1024],
            inline=False
        )

    embed.set_footer(text="Pronote Discord")

    return embed


def make_homework_embed(hw):
    embed = discord.Embed(
        title="Nouveau devoir !",
        description=f"**{hw['subject']}**",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="À faire pour le",
        value=hw["date"],
        inline=True
    )

    embed.add_field(
        name="Description",
        value=hw["description"][:1024] or "Aucune description",
        inline=False
    )

    embed.set_footer(text="Pronote Discord")

    return embed


# =========================
# SURVEILLANCE
# =========================

async def check_pronote():
    channel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await bot.fetch_channel(CHANNEL_ID)

    data = await asyncio.to_thread(fetch_data)

    current = {
        "grades": {x["id"]: x for x in data["grades"]},
        "homework": {x["id"]: x for x in data["homework"]}
    }

    previous = load_json(STATE_FILE)

    # Premier démarrage : création de la référence.
    if previous is None:
        save_json(STATE_FILE, current)
        print("Référence initiale enregistrée.")
        return

    # Détection des nouvelles notes.
    old_grades = previous.get("grades", {})

    for grade_id, grade in current["grades"].items():
        if grade_id not in old_grades:
            await channel.send(embed=make_grade_embed(grade))

    # Détection des nouveaux devoirs.
    old_homework = previous.get("homework", {})

    for hw_id, hw in current["homework"].items():
        if hw_id not in old_homework:
            await channel.send(embed=make_homework_embed(hw))

    save_json(STATE_FILE, current)

    print(
        f"[{datetime.datetime.now():%H:%M:%S}] "
        "Vérification terminée."
    )


@tasks.loop(seconds=CHECK_INTERVAL)
async def pronote_loop():
    try:
        await check_pronote()
    except Exception as error:
        print(f"Erreur de surveillance : {error}")


@pronote_loop.before_loop
async def before_pronote_loop():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    print(f"Connecté à Discord : {bot.user}")

    if not pronote_loop.is_running():
        pronote_loop.start()


# =========================
# DEMARRAGE
# =========================

if __name__ == "__main__":
    if not TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "Configure DISCORD_TOKEN et DISCORD_CHANNEL_ID dans .env"
        )

    bot.run(TOKEN)
