
import pronotepy
import cv2
import json
import uuid
import getpass

from pathlib import Path

DATA_DIR = Path("data")
SESSION_FILE = DATA_DIR / "session.json"
UUID_FILE = DATA_DIR / "app_uuid.txt"


def read_qr_code(image_path):
    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError("Image introuvable.")

    detector = cv2.QRCodeDetector()
    data, _, _ = detector.detectAndDecode(image)

    if not data:
        raise ValueError("Aucun QR code détecté.")

    try:
        return json.loads(data)
    except json.JSONDecodeError as error:
        raise ValueError("QR code JSON invalide.") from error


def authenticate():
    DATA_DIR.mkdir(exist_ok=True)

    print("=== PRONOTE DISCORD ===")
    print("Authentification par QR code\n")

    image_path = input("Chemin de l'image QR : ").strip()
    pin = getpass.getpass("PIN à 4 chiffres : ")

    if len(pin) != 4 or not pin.isdigit():
        print("PIN invalide.")
        return

    try:
        qr_data = read_qr_code(image_path)

        if UUID_FILE.exists():
            app_uuid = UUID_FILE.read_text().strip()
        else:
            app_uuid = str(uuid.uuid4())
            UUID_FILE.write_text(app_uuid)

        client = pronotepy.Client.qrcode_login(
            qr_code=qr_data,
            pin=pin,
            uuid=app_uuid,
            device_name="PronoteDiscord"
        )

        if not client.logged_in:
            print("Échec de connexion.")
            return

        credentials = client.export_credentials()

        SESSION_FILE.write_text(
            json.dumps(credentials, indent=4),
            encoding="utf-8"
        )

        print("\nConnexion réussie !")
        print("Session sauvegardée.")

    except Exception as error:
        print(f"\nErreur : {error}")


if __name__ == "__main__":
    authenticate()
