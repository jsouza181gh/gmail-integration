
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_PATH = Path("credentials.json")
TOKEN_PATH = Path("secrets/token.json")


def authorize():
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_PATH),
        SCOPES
    )

    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent"
    )

    if not creds.refresh_token:
        raise RuntimeError(
            "Google não retornou um refresh token."
        )

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")

    print("Autorização concluída!")


if __name__ == "__main__":
    authorize()
