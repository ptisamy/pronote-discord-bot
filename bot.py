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
# ÉTAT DU BOT
# =========================

pronote_connected = None
last_scan_at = None

last_grades_count = 0
last_homework_count = 0

scan_lock = asyncio.Lock()


# =========================
# STOCKAGE
# =========================

def load_json(path):
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

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

    # Actualisation des identifiants de session
    save_json(SESSION_FILE, client.export_credentials())

    return client


def fetch_data():
    client = get_client()

    grades = []
    homework = []

    # Récupération des notes
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

    # Récupération des devoirs
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


# =========================
# EMBEDS
# =========================

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

    embed.set_footer(text="PronoteDiscord")

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

    embed.set_footer(text="PronoteDiscord")

    return embed


# =========================
# SURVEILLANCE
# =========================

async def check_pronote():
    global pronote_connected, last_scan_at
    global last_grades_count, last_homework_count

    async with scan_lock:

        last_scan_at = datetime.datetime.now(
            datetime.timezone.utc
        )

        channel = bot.get_channel(CHANNEL_ID)

        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)

        try:
            data = await asyncio.to_thread(fetch_data)
            pronote_connected = True

        except Exception:
            pronote_connected = False
            raise

        last_grades_count = len(data["grades"])
        last_homework_count = len(data["homework"])

        current = {
            "grades": {
                x["id"]: x for x in data["grades"]
            },
            "homework": {
                x["id"]: x for x in data["homework"]
            }
        }

        previous = load_json(STATE_FILE)

        # =========================
        # PREMIÈRE SYNCHRONISATION
        # =========================

        if previous is None:

            await channel.send(
                "📚 **Première synchronisation Pronote !**\n"
                "Voici toutes les notes et tous les devoirs récupérés."
            )

            for grade in data["grades"]:
                await channel.send(
                    embed=make_grade_embed(grade)
                )

            for hw in data["homework"]:
                await channel.send(
                    embed=make_homework_embed(hw)
                )

            save_json(STATE_FILE, current)

            print(
                f"Première synchronisation terminée : "
                f"{len(data['grades'])} notes et "
                f"{len(data['homework'])} devoirs."
            )

            return

        # =========================
        # NOUVELLES NOTES
        # =========================

        old_grades = previous.get("grades", {})

        for grade_id, grade in current["grades"].items():

            if grade_id not in old_grades:
                await channel.send(
                    embed=make_grade_embed(grade)
                )

        # =========================
        # NOUVEAUX DEVOIRS
        # =========================

        old_homework = previous.get("homework", {})

        for hw_id, hw in current["homework"].items():

            if hw_id not in old_homework:
                await channel.send(
                    embed=make_homework_embed(hw)
                )

        # Enregistrement de la nouvelle référence
        save_json(STATE_FILE, current)

        print(
            f"[{datetime.datetime.now():%H:%M:%S}] "
            f"Scan terminé : {last_grades_count} notes, "
            f"{last_homework_count} devoirs."
        )


# =========================
# COMMANDE /STATUS
# =========================

@bot.tree.command(
    name="status",
    description="Affiche le statut de Pronote Bot."
)
async def status(interaction: discord.Interaction):

    if pronote_connected is True:
        connection = "🟢 Connecté"
        color = discord.Color.green()

    elif pronote_connected is False:
        connection = "🔴 Déconnecté"
        color = discord.Color.red()

    else:
        connection = "🟡 Pas encore testé"
        color = discord.Color.orange()

    # Dernier scan
    if last_scan_at:
        last_scan = (
            f"<t:{int(last_scan_at.timestamp())}:R>"
        )
    else:
        last_scan = "Aucun scan effectué"

    # Prochain scan automatique
    next_iteration = pronote_loop.next_iteration

    if next_iteration:
        now = datetime.datetime.now(
            datetime.timezone.utc
        )

        remaining = max(
            0,
            int((next_iteration - now).total_seconds())
        )

        minutes, seconds = divmod(remaining, 60)
        next_scan = f"{minutes} min {seconds} sec"

    else:
        next_scan = "En attente"

    embed = discord.Embed(
        title="📊 Statut de Pronote Bot",
        color=color
    )

    embed.add_field(
        name="Connexion Pronote",
        value=connection,
        inline=False
    )

    embed.add_field(
        name="Dernier scan",
        value=last_scan,
        inline=True
    )

    embed.add_field(
        name="Prochain scan",
        value=next_scan,
        inline=True
    )

    embed.add_field(
        name="Notes récupérées",
        value=str(last_grades_count),
        inline=True
    )

    embed.add_field(
        name="Devoirs récupérés",
        value=str(last_homework_count),
        inline=True
    )

    embed.set_footer(
        text="Pronote Bot"
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================
# COMMANDE /SCAN
# =========================

@bot.tree.command(
    name="scan",
    description="Lance immédiatement un scan Pronote."
)
async def scan(interaction: discord.Interaction):

    await interaction.response.defer(
        ephemeral=True
    )

    try:
        await check_pronote()

        await interaction.followup.send(
            "✅ Scan terminé ! Les données Pronote ont été vérifiées.",
            ephemeral=True
        )

    except Exception as error:

        await interaction.followup.send(
            f"❌ Échec du scan : `{str(error)[:1500]}`",
            ephemeral=True
        )


# =========================
# BOUCLE AUTOMATIQUE
# =========================

@tasks.loop(seconds=CHECK_INTERVAL)
async def pronote_loop():

    try:
        await check_pronote()

    except Exception as error:
        print(
            f"Erreur de surveillance : {error}"
        )


@pronote_loop.before_loop
async def before_pronote_loop():
    await bot.wait_until_ready()


# =========================
# DEMARRAGE
# =========================

@bot.event
async def on_ready():

    print(f"Connecté à Discord : {bot.user}")

    await bot.tree.sync()
    print("Commandes slash synchronisées.")

    if not pronote_loop.is_running():
        pronote_loop.start()


# =========================
# LANCEMENT
# =========================

if __name__ == "__main__":

    if not TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "Configure DISCORD_TOKEN et DISCORD_CHANNEL_ID dans .env"
        )

    bot.run(TOKEN)