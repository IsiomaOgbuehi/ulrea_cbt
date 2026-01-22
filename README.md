# ulrea_cbt
# Backend Service for Multi-Institution CBT Platform
## Built with: Python • FastAPI • PostgreSQL • SQLAlchemy • Alembic • Docker

## 🧠 Table of Contents

📌 About

🚀 Features

🧩 Architecture

⚙️ Prerequisites

🛠️ Setup & Installation

▶️ Run Locally

🔍 API Documentation

🧪 Testing

🧬 Database Migrations

🧑‍💻 Development Workflow

📦 Deployment

🛡️ Security / Multi-Tenant Notes

📄 License


## 📌 About

ulrea_cbt is a multi-tenant backend service for a Computer-Based Testing (CBT) platform. It allows multiple institutions (tenants) to securely run CBT services within isolated contexts, providing APIs for authentication, test creation, scheduling, result management, etc.


## 🚀 Features

- Multi-Tenant support (tenant isolation per institution)

- FastAPI async backend with automatic OpenAPI/Swagger docs

- PostgreSQL database with SQLAlchemy ORM

- Authentication (JWT or similar)

- Database migrations with Alembic

- Docker support for easy setup

- API documentation out of the box


## 🧩 Architecture
Client (Web/Mobile)
       │
       ▼
    FastAPI Backend
       │
       ├── PostgreSQL (tenant data)
       │
       ├── SQLAlchemy ORM
       │
       └── Alembic (migrations)


## ⚙️ Prerequisites

1. Install the following before you begin:

2. Python 3.10+

3. PostgreSQL (13+)

4. Docker & Docker Compose (optional but recommended)

5. pipenv or venv (Python virtual environment) or uv


## 🛠️ Setup & Installation

Clone the repository:

`git clone https://github.com/IsiomaOgbuehi/ulrea_cbt.git`
`cd ulrea_cbt`


**Create a virtual environment:**

`python -m venv .venv`
`source .venv/bin/activate`   # Linux / macOS
# .venv\Scripts\activate     # Windows

**Install dependencies:**

`pip install --upgrade pip`
`pip install -r requirements.txt`
OR
`pip install -e .`


## ▶️ Run Locally
🐳 Using Docker (recommended)

Create a .env file based on .env.example:

cp .env.example .env


Edit .env to set your database credentials:

POSTGRES_USER=youruser
POSTGRES_PASSWORD=yourpassword
POSTGRES_DB=ulrea_cbt
DB_HOST=database
DB_PORT=5432
SECRET_KEY=your_jwt_secret

## Start services:

**docker compose up --build -d**


**Run migrations (inside container):**

`docker compose exec backend alembic upgrade head`


*Your API should be available at:*

(http://localhost:8000)


## 🧑‍💻 Without Docker

Ensure PostgreSQL is running and accessible. Set the environment variables:

`export DATABASE_URL="postgresql://youruser:yourpassword@localhost:5432/ulrea_cbt"`
`export SECRET_KEY="your_jwt_secret"`


**Run migrations:**

`alembic upgrade head`


**Start the FastAPI server:**

`uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000`


## 🔍 API Documentation

FastAPI auto-generates documentation:

Swagger UI: (http://localhost:8000/docs)

ReDoc: (http://localhost:8000/redoc)

## 🧪 Testing

Tests might be implemented with pytest. To run tests:

Install dev/test dependencies:

`pip install pytest pytest-asyncio httpx`


**Run the test suite:**

`pytest tests/ -v`


**Optional: generate coverage report:**

`pytest --cov=backend --cov-report=html`


*These commands assume you have a tests/ directory with test files.*


## 🧬 Database Migrations

Schema changes are managed by Alembic:

Generate a migration script:

`alembic revision --autogenerate -m "Describe your change"`


**Apply migrations:**

`alembic upgrade head`


**Rollback:**

`alembic downgrade -1`


## 🧑‍💻 Development Workflow

Clone & setup dev environment

**Create new branch:**

`git checkout -b feature/your-feature`


**Implement feature**

Write tests

Run tests

Create Pull Request



## 📦 Deployment

**Deployment options include:**

1. Docker Compose on a server

2. Cloud services (e.g., Render, Fly.io, Railway)

3. Kubernetes stack for scaling

4. General deployment steps:

5. Build Docker images

6. Push to registry

7. Configure environment variables in your platform

8. Run migrations

9. Start services


## 🛡️ Security and Multi-Tenant Notes

API endpoints should validate tenant context (e.g., via JWT token or tenant identifier in header).

Database should enforce tenant data separation (schema or tenant_id filtering).

Protect secret keys and credentials using environment variables and secret stores.

## 📄 License

This project is licensed under the MIT License — see the LICENSE
 file for details.


## 👍 Acknowledgements

Thanks for building this project! It’s a solid starter backend for multi-tenant applications with modern tooling.