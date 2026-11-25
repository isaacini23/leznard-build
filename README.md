# Leznard

Leznard is an early-stage software project being developed around business operations, with a planned desktop experience and a dedicated professional workflow for accountants.

## Project Direction

The product direction includes a desktop application for day-to-day users and a specialised accountant-facing workspace. The backend is being developed first so future web and desktop clients can share the same business rules, permissions and tenant boundaries.

## Current Status

**Foundation / early development.** The repository includes organization membership and access controls, accounting foundations, and the first Contacts domain. This is not yet a finished or production-ready accounting system. Features, calculations, data integrity, security and reporting must be implemented and tested before real business use.

## API

The versioned API is rooted at `/api/v1/`. Contacts endpoints, permissions, filters, pagination and examples are documented in [the Contacts API guide](docs/api/contacts.md).

API requests that access organization-owned data must include an authenticated session and the `X-Organization-ID` header. See the API guide for the Contacts endpoint contract.

## Development

The project uses Django, Django REST Framework and PostgreSQL. Run checks and tests using the project's configured virtual environment and database. The exact local setup is environment-specific; do not assume the repository is production-ready.

## Repository Hygiene

Keep local virtual environments, environment files, generated databases, build outputs and IDE settings out of version control. Use the `.gitignore` rules and never commit passwords, API keys or signing credentials.

## Author

**Ini Isaac** — [GitHub](https://github.com/isaacini23)
