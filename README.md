# Therapist Finder

A modern Python tool for parsing therapist data from PDF/text files and generating personalized email drafts.

## Features

- 📄 Parse therapist data from PDF and text files
- ✉️ Generate personalized email drafts
- 📊 Export data to JSON and Markdown formats
- 🍎 Generate AppleScript for Mail.app automation
- 🔧 Modern Python project structure with Poetry
- 🎨 Rich CLI interface with progress bars
- 🧪 Comprehensive testing with pytest

## Installation

### Prerequisites

- Python 3.9 or higher
- Poetry (recommended) or pip

### Using Poetry (Recommended)

```bash
# Install Poetry if you haven't already
curl -sSL https://install.python-poetry.org | python3 -

# Install dependencies
poetry install

# Activate virtual environment
poetry env activate
```

### Using pip

```bash
pip install -r requirements.txt
```

## Usage

### Command Line Interface

```bash
# Parse a PDF file
therapist-finder process --pdf path/to/therapists.pdf

# Parse a text file
therapist-finder process --text path/to/therapists.txt

# Crawl Berlin directories for the N closest providers to an address
# (default sources 116117 + OSM work from anywhere)
therapist-finder crawl-berlin \
  --address "Kastanienallee 12, 10435 Berlin" \
  --max 20 \
  --output clients/anna/

# Full coverage (run from your laptop, not CI):
therapist-finder crawl-berlin \
  --address "Kastanienallee 12, 10435 Berlin" \
  --max 20 \
  --sources 116117,osm,psych_info,therapie_de \
  --output clients/anna/

# Generate AppleScript for existing client
therapist-finder applescript --client "John Doe"

# Show help
therapist-finder --help
```

See [docs/sources.md](docs/sources.md) for the crawl pipeline, supported
directories (116117, OSM, PTK Berlin, Ärztekammer Berlin), and configuration.

### Python API

```python
from therapist_finder import TherapistParser, EmailGenerator
from therapist_finder.config import Settings

# Initialize components
parser = TherapistParser()
email_gen = EmailGenerator()
settings = Settings()

# Parse PDF
therapists = parser.parse_pdf("therapists.pdf")

# Generate emails
emails = email_gen.create_drafts(therapists, user_info)
```

## Configuration

Configuration can be managed through:

1. Environment variables
2. `config.yaml` file
3. Command line arguments

Example `config.yaml`:

```yaml
email_template_path: "templates/email_template.txt"
output_directory: "clients"
default_subject: "Terminanfrage"
```

### Contacts database

The `/api/contacts/*` endpoints (used for the send-queue balancer and
"don't email the same therapist twice" dedupe) persist to a SQLite file.
Path is configurable via `THERAPIST_FINDER_CONTACTS_DB`; default is
`contacts.db` in the working directory.

The same DB also stores the body of every sent mail (`sent_mails` table)
so the AI generator (below) can vary phrasing on re-contact.

> ⚠️ **Render free tier:** the service runs on ephemeral disk, so
> `contacts.db` is wiped on every redeploy and after long idle periods.
> Counts and per-browser history reset with it. For durable storage,
> attach a Render persistent disk and point `THERAPIST_FINDER_CONTACTS_DB`
> at a path on it, or migrate the store to managed Postgres
> (`therapist_finder/api/contacts_store.py` is the only module that needs
> to change).

### AI mail body generation (optional)

Step 4 has a **"Generate with AI"** button that drafts the user's message
body via Google Gemini. To enable it, set `GEMINI_API_KEY` (free tier via
[Google AI Studio](https://aistudio.google.com/)). When the key is unset
the endpoint returns 503 and the frontend shows an inline "not configured"
notice — everything else still works, the user just writes the body
themselves.

| Env var | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | _unset_ | Enables the feature when set. |
| `GEMINI_MODEL`   | `gemini-2.5-flash-lite` | Override the model without redeploying. Useful when Google rotates free-tier quotas (e.g. `gemini-2.5-flash`, `gemini-flash-latest`). Check your account's quota at <https://ai.dev/rate-limit>. |

No PII is sent to the LLM: only the target language, insurance label,
and previously sent bodies to the same therapists (for anti-repetition).
Greeting, contact info, and closing are inserted client-side after
generation.

## Development

### Setup Development Environment

```bash
# Clone the repository
git clone <repository-url>
cd therapist-finder

# Install development dependencies
poetry install --with dev

# Install pre-commit hooks
pre-commit install
```

### Running Tests

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=therapist_finder

# Run specific test file
poetry run pytest tests/test_parser.py
```

### Code Quality

```bash
# Format code
poetry run black .

# Lint code
poetry run ruff check .

# Type checking
poetry run mypy therapist_finder
```

## Project Structure

```
therapist-finder/
├── therapist_finder/           # Main package
│   ├── __init__.py
│   ├── cli.py                  # CLI interface
│   ├── config.py               # Configuration management
│   ├── models.py               # Data models
│   ├── parsers/                # Parser modules
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── pdf_parser.py
│   │   └── text_parser.py
│   ├── email/                  # Email generation
│   │   ├── __init__.py
│   │   ├── generator.py
│   │   └── templates.py
│   └── utils/                  # Utility functions
│       ├── __init__.py
│       ├── file_utils.py
│       └── text_utils.py
├── templates/                  # Email templates
│   └── email_template.txt
├── tests/                      # Test suite
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_parsers.py
│   └── test_email.py
├── clients/                    # Client data (gitignored)
├── pyproject.toml              # Poetry configuration
├── README.md                   # This file
└── .gitignore                  # Git ignore rules
```

## License

MIT License - see LICENSE file for details.
