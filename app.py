import discord
import pronotepy
import asyncio
import json
import os
import datetime
import hashlib
import unicodedata
import time
from zoneinfo import ZoneInfo

from pathlib import Path
from dotenv import load_dotenv
from discord.ext import commands, tasks

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

SESSION_FILE = Path("data/session.json")
STATE_FILE = Path("data/last_check.json")
TIMETABLE_FILE = Path("data/last_timetable.json")

CHECK_INTERVAL = 5 * 60
TIMETABLE_INTERVAL = 60 * 60
TIMETABLE_DAYS = 14

TIMETABLE_STATUS_KEYWORDS = (
    "changement de salle",
    "cours annulé",
    "exceptionnel",
)

pronote_connected = None
last_scan_at = None
last_timetable_scan_at = None
last_grades_count = 0
last_homework_count = 0
last_timetable_count = 0

scan_lock = asyncio.Lock()
timetable_lock = asyncio.Lock()
pronote_access_lock = asyncio.Lock()


# =========================
# STOCKAGE ET IDENTIFIANTS
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
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_text(value):
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def is_interesting_timetable_status(status):
    normalized = normalize_text(status)
    return any(normalize_text(k) in normalized for k in TIMETABLE_STATUS_KEYWORDS)


def timetable_lesson_id(subject, start, end):
    """Identifiant stable : ne dépend pas de l'ID temporaire de Pronote."""
    return fingerprint({
        "subject": str(subject or "Cours").strip().lower(),
        "start": start.isoformat() if hasattr(start, "isoformat") else str(start),
        "end": end.isoformat() if hasattr(end, "isoformat") else str(end or ""),
    })


# =========================
# CONNEXION ET RÉCUPÉRATION PRONOTE
# =========================

def get_client():
    credentials = load_json(SESSION_FILE)
    if not credentials:
        raise RuntimeError("Session absente. Lance pronote_auth.py.")

    client = pronotepy.Client.token_login(**credentials)
    if not client.logged_in:
        raise RuntimeError("Connexion Pronote impossible.")

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
                    "comment": grade.comment,
                }),
                "subject": grade.subject.name,
                "grade": grade.grade,
                "out_of": grade.out_of,
                "coefficient": grade.coefficient,
                "comment": grade.comment,
                "date": str(grade.date),
                "period": period.name,
            }
            grades.append(item)

    for hw in client.homework(datetime.date.today()):
        item = {
            "id": fingerprint({
                "date": hw.date,
                "subject": hw.subject.name,
                "description": hw.description,
            }),
            "subject": hw.subject.name,
            "description": hw.description,
            "date": str(hw.date),
            "done": hw.done,
        }
        homework.append(item)

    return {"grades": grades, "homework": homework}


def fetch_timetable():
    client = get_client()
    today = datetime.date.today()
    end_date = today + datetime.timedelta(days=TIMETABLE_DAYS)
    timetable = []

    for lesson in client.lessons(today, end_date):
        subject_obj = getattr(lesson, "subject", None)
        subject = getattr(subject_obj, "name", None) if subject_obj else None
        start = getattr(lesson, "start", None)
        end = getattr(lesson, "end", None)
        if start is None:
            continue

        status = str(getattr(lesson, "status", "") or "").strip()
        if not is_interesting_timetable_status(status):
            continue

        item = {
            # On ignore lesson.id, qui peut changer entre deux sessions.
            "id": timetable_lesson_id(subject, start, end),
            "subject": str(subject or "Cours"),
            "start": start.isoformat() if hasattr(start, "isoformat") else str(start),
            "end": end.isoformat() if hasattr(end, "isoformat") else str(end or ""),
            "teacher": str(getattr(lesson, "teacher_name", None) or "Non précisé"),
            "classroom": str(getattr(lesson, "classroom", None) or "Non précisée"),
            "canceled": bool(getattr(lesson, "canceled", False)),
            "status": status,
        }
        timetable.append(item)

    return timetable


# =========================
# BOT DISCORD ET EMBEDS
# =========================

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def make_grade_embed(grade):
    embed = discord.Embed(
        title="Nouvelle note !",
        description=f"**{grade['subject']}**",
        color=discord.Color.green(),
    )
    embed.add_field(name="Note", value=f"{grade['grade']}/{grade['out_of']}", inline=True)
    embed.add_field(name="Coefficient", value=grade["coefficient"] or "Non précisé", inline=True)
    embed.add_field(name="Date", value=grade["date"], inline=True)
    embed.add_field(name="Période", value=grade["period"], inline=True)
    if grade["comment"]:
        embed.add_field(name="Commentaire", value=str(grade["comment"])[:1024], inline=False)
    embed.set_footer(text="PronoteDiscord")
    return embed


def make_homework_embed(hw):
    embed = discord.Embed(
        title="Nouveau devoir !",
        description=f"**{hw['subject']}**",
        color=discord.Color.blue(),
    )
    embed.add_field(name="À faire pour le", value=hw["date"], inline=True)
    embed.add_field(
        name="Description",
        value=str(hw["description"])[:1024] or "Aucune description",
        inline=False,
    )
    embed.set_footer(text="PronoteDiscord")
    return embed


def format_lesson_time(value):
    try:
        return datetime.datetime.fromisoformat(value).strftime("%d/%m/%Y à %H:%M")
    except (TypeError, ValueError):
        return value or "Non précisé"


def make_timetable_embed(lesson, old=None):
    status = normalize_text(lesson.get("status", ""))
    if "annul" in status:
        title, color = "❌ Cours annulé", discord.Color.red()
    elif "salle" in status:
        title, color = "🚪 Changement de salle", discord.Color.orange()
    else:
        title, color = "📌 Information exceptionnelle", discord.Color.blurple()

    embed = discord.Embed(
        title=title,
        description=f"**{lesson['subject']}**",
        color=color,
    )
    embed.add_field(
        name="Horaire",
        value=f"{format_lesson_time(lesson['start'])}\n→ {format_lesson_time(lesson['end'])}",
        inline=False,
    )
    embed.add_field(name="Salle", value=lesson["classroom"], inline=True)
    embed.add_field(name="Professeur", value=lesson["teacher"], inline=True)
    embed.add_field(
        name="Statut Pronote",
        value=lesson.get("status") or "Non précisé",
        inline=False,
    )

    if old:
        labels = {
            "start": "Début",
            "end": "Fin",
            "teacher": "Professeur",
            "classroom": "Salle",
            "status": "Statut",
        }
        changes = []
        for key, label in labels.items():
            if old.get(key) != lesson.get(key):
                before = old.get(key, "Non précisé")
                after = lesson.get(key, "Non précisé")
                if key in ("start", "end"):
                    before = format_lesson_time(before)
                    after = format_lesson_time(after)
                changes.append(f"**{label} :** {before} → {after}")
        if changes:
            embed.add_field(
                name="Changements détectés",
                value="\n".join(changes)[:1024],
                inline=False,
            )

    embed.set_footer(text="PronoteDiscord • Surveillance EDT")
    return embed


# =========================
# SCAN NOTES ET DEVOIRS
# =========================

async def check_pronote(scan_type="all"):
    """Scanne les notes, les devoirs, ou les deux (mode automatique)."""
    global pronote_connected, last_scan_at
    global last_grades_count, last_homework_count

    if scan_type not in ("all", "notes", "devoirs"):
        raise ValueError("Type de scan Pronote invalide.")

    async with scan_lock:
        last_scan_at = datetime.datetime.now(datetime.timezone.utc)
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)

        try:
            async with pronote_access_lock:
                data = await asyncio.to_thread(fetch_data)
            pronote_connected = True
        except Exception:
            pronote_connected = False
            raise

        last_grades_count = len(data["grades"])
        last_homework_count = len(data["homework"])
        current = {
            "grades": {x["id"]: x for x in data["grades"]},
            "homework": {x["id"]: x for x in data["homework"]},
        }
        previous = load_json(STATE_FILE)
        first_sync = previous is None
        if previous is None:
            previous = {"grades": {}, "homework": {}}

        if first_sync and scan_type == "all":
            await channel.send(
                "📚 **Première synchronisation Pronote !**\\n"
                "Voici toutes les notes et tous les devoirs récupérés."
            )

        if scan_type == "all":
            sections = ("grades", "homework")
        elif scan_type == "notes":
            sections = ("grades",)
        else:
            sections = ("homework",)

        for section in sections:
            old_items = previous.get(section, {})
            for item_id, item in current[section].items():
                if item_id not in old_items:
                    if section == "grades":
                        await channel.send(embed=make_grade_embed(item))
                    else:
                        await channel.send(embed=make_homework_embed(item))
            previous[section] = current[section]

        save_json(STATE_FILE, previous)
        print(
            f"[{datetime.datetime.now():%H:%M:%S}] Scan {scan_type} terminé : "
            f"{last_grades_count} notes, {last_homework_count} devoirs."
        )


# =========================
# SCAN EMPLOI DU TEMPS
# =========================

async def check_timetable():
    global last_timetable_scan_at, last_timetable_count, pronote_connected

    async with timetable_lock:
        last_timetable_scan_at = datetime.datetime.now(datetime.timezone.utc)
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(CHANNEL_ID)

        try:
            async with pronote_access_lock:
                lessons = await asyncio.to_thread(fetch_timetable)
            pronote_connected = True
        except Exception:
            pronote_connected = False
            raise

        last_timetable_count = len(lessons)
        current = {lesson["id"]: lesson for lesson in lessons}
        previous = load_json(TIMETABLE_FILE)

        # Première synchronisation EDT : enregistre sans notifier.
        if previous is None:
            save_json(TIMETABLE_FILE, {"lessons": current})
            print(
                "Première synchronisation EDT : "
                f"{len(current)} événement(s) enregistré(s)."
            )
            return

        old_lessons_raw = previous.get("lessons", {})

        # Convertit les anciens enregistrements utilisant les IDs Pronote
        # vers les nouveaux identifiants stables.
        old_lessons = {}
        for old in old_lessons_raw.values():
            stable_id = timetable_lesson_id(
                old.get("subject", "Cours"),
                old.get("start", ""),
                old.get("end", ""),
            )
            old_lessons[stable_id] = old

        now = datetime.datetime.now()

        def is_upcoming(lesson):
            try:
                start = datetime.datetime.fromisoformat(lesson["start"])
                reference = datetime.datetime.now(start.tzinfo) if start.tzinfo else now
                return start >= reference
            except (KeyError, TypeError, ValueError):
                return True

        for lesson_id, lesson in current.items():
            old = old_lessons.get(lesson_id)

            if old is None:
                if is_upcoming(lesson):
                    await channel.send(embed=make_timetable_embed(lesson))
                continue

            if old != lesson and is_upcoming(lesson):
                await channel.send(embed=make_timetable_embed(lesson, old))

        # Sauvegarde les données courantes pour éviter les notifications répétées.
        save_json(TIMETABLE_FILE, {"lessons": current})
        print(
            f"[{datetime.datetime.now():%H:%M:%S}] Scan EDT terminé : "
            f"{last_timetable_count} événement(s) ciblé(s)."
        )


# =========================
# COMMANDES ET STATUT
# =========================

def format_task_countdown(task):
    next_iteration = task.next_iteration
    if next_iteration is None:
        return "En attente"
    now = datetime.datetime.now(datetime.timezone.utc)
    remaining = max(0, int((next_iteration - now).total_seconds()))
    minutes, seconds = divmod(remaining, 60)
    return f"{minutes} min {seconds} sec" if minutes else f"{seconds} sec"


def format_relative_time(value):
    return f"<t:{int(value.timestamp())}:R>" if value else "Aucun scan effectué"
def format_datetime_fr(timestamp):
    if timestamp is None:
        return "Jamais"

    paris_tz = ZoneInfo("Europe/Paris")

    # Si la date n'a pas de fuseau, on considère qu'elle est en UTC.
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.timezone.utc)

    dt = timestamp.astimezone(paris_tz)

    mois = [
        "janvier", "février", "mars", "avril",
        "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"
    ]

    return (
        f"{dt.day} {mois[dt.month - 1]} {dt.year} "
        f"à {dt.hour:02d}h{dt.minute:02d}"
    )

@bot.tree.command(name="status", description="Teste la connexion Pronote et affiche le statut du bot.")
async def status(interaction: discord.Interaction):
    # Le test effectue une vraie connexion authentifiée à Pronote.
    await interaction.response.defer(thinking=True)

    started_at = time.perf_counter()
    try:
        async with pronote_access_lock:
            await asyncio.to_thread(get_client)

        latency_ms = (time.perf_counter() - started_at) * 1000
        connection = f"🟢 Accessible • {latency_ms:.0f} ms"
        color = discord.Color.green()
        test_result = f"✅ Connexion réussie en **{latency_ms:.0f} ms**."

        global pronote_connected
        pronote_connected = True

    except Exception as error:
        connection = "🔴 Inaccessible"
        color = discord.Color.red()
        test_result = f"❌ Échec du test (`{type(error).__name__}`)."
        pronote_connected = False

    embed = discord.Embed(
        title="📊 Statut de Pronote Bot",
        description=f"Connexion Pronote : {connection}",
        color=color,
    )

    embed.add_field(
        name="Test de connexion Pronote",
        value=test_result,
        inline=False,
    )

    embed.add_field(
        name="Notes et devoirs",
        value=(
            f"Dernier scan : {format_datetime_fr(last_scan_at)}\n"
            f"Prochain scan : {format_datetime_fr(pronote_loop.next_iteration)}\n"
            f"Notes : {last_grades_count}\n"
            f"Devoirs : {last_homework_count}"
        ),
        inline=False,
    )

    embed.add_field(
        name="Emploi du temps",
        value=(
            f"Dernier scan : {format_datetime_fr(last_timetable_scan_at)}\n"
            f"Prochain scan : {format_datetime_fr(timetable_loop.next_iteration)}\n"
            f"Événements ciblés : {last_timetable_count}\n"
            f"Période : {TIMETABLE_DAYS} jours"
        ),
        inline=False,
    )

    embed.set_footer(text="PronoteDiscord")
    await interaction.followup.send(embed=embed)


# Commande groupée : /scan devoirs, /scan notes ou /scan edt
scan_group = discord.app_commands.Group(
    name="scan",
    description="Lance un scan Pronote ciblé.",
)


@scan_group.command(name="devoirs", description="Vérifie immédiatement les devoirs.")
async def scan_devoirs(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await check_pronote("devoirs")
        await interaction.followup.send("✅ Scan des devoirs terminé.", ephemeral=True)
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan des devoirs : `{str(error)[:1500]}`",
            ephemeral=True,
        )


@scan_group.command(name="notes", description="Vérifie immédiatement les notes.")
async def scan_notes(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await check_pronote("notes")
        await interaction.followup.send("✅ Scan des notes terminé.", ephemeral=True)
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan des notes : `{str(error)[:1500]}`",
            ephemeral=True,
        )


@scan_group.command(name="edt", description="Vérifie les changements importants de l'emploi du temps.")
async def scan_edt(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        await check_timetable()
        await interaction.followup.send(
            "✅ Scan EDT terminé. Seuls les statuts ciblés sont surveillés.",
            ephemeral=True,
        )
    except Exception as error:
        await interaction.followup.send(
            f"❌ Échec du scan EDT : `{str(error)[:1500]}`",
            ephemeral=True,
        )


bot.tree.add_command(scan_group)


# =========================
# BOUCLES AUTOMATIQUES
# =========================

@tasks.loop(seconds=CHECK_INTERVAL)
async def pronote_loop():
    try:
        await check_pronote()
    except Exception as error:
        print(f"Erreur de surveillance Pronote : {error}")


@pronote_loop.before_loop
async def before_pronote_loop():
    await bot.wait_until_ready()


@tasks.loop(seconds=TIMETABLE_INTERVAL)
async def timetable_loop():
    try:
        await check_timetable()
    except Exception as error:
        print(f"Erreur de surveillance EDT : {error}")


@timetable_loop.before_loop
async def before_timetable_loop():
    await bot.wait_until_ready()


# =========================
# DÉMARRAGE
# =========================

@bot.event
async def on_ready():
    print(f"Connecté à Discord : {bot.user}")
    await bot.tree.sync()
    print("Commandes slash synchronisées.")

    if not pronote_loop.is_running():
        pronote_loop.start()
    if not timetable_loop.is_running():
        timetable_loop.start()


if __name__ == "__main__":
    if not TOKEN or not CHANNEL_ID:
        raise RuntimeError(
            "Configure DISCORD_TOKEN et DISCORD_CHANNEL_ID dans .env"
        )
    bot.run(TOKEN)
