"""Copilot: streaming NL to SQL analyst with preview-and-approve."""

from dotenv import load_dotenv

# Same reasoning as the chassis repos: credentials arrive either from a real
# environment (CI, Render) or from .env next to the checkout, and most import
# paths never touch app.config. override=False keeps CI variables winning.
load_dotenv(override=False)
