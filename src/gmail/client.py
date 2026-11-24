"""Gmail scope constants. The per-account clients live in `src.gmail.clients`."""

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
# Optional: filters (block sender). Never request https://mail.google.com/
# (permanent delete).
SETTINGS_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic"
