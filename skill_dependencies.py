"""Implicit skill prerequisites.

Given a set of skills the candidate explicitly has, we can deduce a set of
skills they implicitly demonstrate knowledge of (e.g. React implies HTML/CSS/JS).
We use this to filter the model's `Currently Exploring:` line so it doesn't
embarrass the candidate by listing things they obviously already know.
"""

from __future__ import annotations

import re


# Common spelling variations -> canonical form.
# Always normalize through this map before lookup or comparison.
_ALIASES: dict[str, str] = {
    # JavaScript ecosystem
    "js": "javascript",
    "es6": "javascript",
    "es2015": "javascript",
    "ecmascript": "javascript",
    "ts": "typescript",
    "nodejs": "node.js",
    "node": "node.js",
    "react.js": "react",
    "reactjs": "react",
    "vue.js": "vue",
    "vuejs": "vue",
    "nextjs": "next.js",
    "nuxtjs": "nuxt.js",
    "nuxt": "nuxt.js",
    "express": "express.js",
    "expressjs": "express.js",
    "nest": "nest.js",
    "nestjs": "nest.js",
    "tailwindcss": "tailwind",
    "tailwind css": "tailwind",
    "html5": "html",
    "css3": "css",

    # Python ecosystem
    "py": "python",
    "py3": "python",
    "python3": "python",
    "sklearn": "scikit-learn",
    "hf": "huggingface",
    "hugging face": "huggingface",

    # Languages
    "golang": "go",
    "c#": "csharp",
    "c sharp": "csharp",
    "cplusplus": "cpp",
    "c++": "cpp",

    # Cloud / DevOps
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "kubectl": "kubernetes",
    "cicd": "ci/cd",
    "ci-cd": "ci/cd",
    "github actions": "github actions",
    "gh actions": "github actions",
    "ec2": "aws ec2",
    "s3": "aws s3",
    "lambda": "aws lambda",

    # Databases
    "postgres": "postgresql",
    "psql": "postgresql",
    "mongo": "mongodb",
    "rdbms": "sql",

    # APIs
    "rest api": "rest",
    "restful api": "rest",
    "restful": "rest",
    "rest apis": "rest",
    "graph ql": "graphql",
    "ws": "websockets",
    "socketio": "socket.io",

    # Auth
    "json web token": "jwt",
    "json web tokens": "jwt",
    "oauth 2": "oauth2",
    "oauth 2.0": "oauth2",
}


# Maps a skill -> set of skills it implicitly demonstrates competence in.
# Keys and values use canonical normalized forms (see _ALIASES).
IMPLICIT_PREREQUISITES: dict[str, set[str]] = {
    # ── Frontend frameworks ────────────────────────────────────────────────
    "react": {"html", "css", "javascript", "jsx", "dom", "frontend"},
    "next.js": {"react", "node.js", "javascript", "html", "css", "ssr", "frontend"},
    "vue": {"html", "css", "javascript", "dom", "frontend"},
    "nuxt.js": {"vue", "node.js", "javascript", "html", "css", "frontend"},
    "angular": {"typescript", "javascript", "html", "css", "rxjs", "frontend"},
    "svelte": {"html", "css", "javascript", "dom", "frontend"},
    "tailwind": {"css", "html"},
    "bootstrap": {"css", "html"},
    "redux": {"javascript", "state management"},
    "mui": {"react", "css"},
    "shadcn": {"react", "tailwind", "css"},

    # ── JavaScript ecosystem ───────────────────────────────────────────────
    "typescript": {"javascript"},
    "node.js": {"javascript", "npm", "backend"},
    "express.js": {"node.js", "javascript", "http", "rest", "backend"},
    "nest.js": {"node.js", "typescript", "javascript", "backend"},
    "deno": {"javascript", "typescript"},
    "bun": {"javascript", "typescript", "node.js"},
    "socket.io": {"websockets", "node.js"},

    # ── Python ecosystem ───────────────────────────────────────────────────
    "django": {"python", "http", "rest", "mvc", "orm", "backend"},
    "flask": {"python", "http", "rest", "backend"},
    "fastapi": {"python", "http", "rest", "async", "pydantic", "backend"},
    "tornado": {"python", "http", "async"},
    "celery": {"python", "redis", "async"},
    "streamlit": {"python", "frontend"},
    "gradio": {"python", "frontend"},

    # ── ML / Data ──────────────────────────────────────────────────────────
    "numpy": {"python"},
    "pandas": {"python", "numpy"},
    "polars": {"python"},
    "matplotlib": {"python", "data visualization"},
    "seaborn": {"python", "matplotlib", "data visualization"},
    "pytorch": {"python", "numpy", "deep learning", "neural networks", "tensors"},
    "tensorflow": {"python", "numpy", "deep learning", "neural networks"},
    "keras": {"python", "tensorflow", "deep learning"},
    "scikit-learn": {"python", "numpy", "machine learning"},
    "xgboost": {"python", "machine learning"},
    "lightgbm": {"python", "machine learning"},
    "huggingface": {"python", "transformers", "deep learning"},
    "transformers": {"python", "deep learning"},
    "langchain": {"python", "llm", "openai api", "rag"},
    "langgraph": {"python", "langchain", "llm"},
    "llamaindex": {"python", "llm", "rag"},
    "openai api": {"python", "llm", "rest", "api"},
    "rag": {"llm", "embeddings", "vector search"},
    "ai agents": {"llm"},

    # ── Java ecosystem ─────────────────────────────────────────────────────
    "spring": {"java", "maven", "dependency injection", "backend"},
    "spring boot": {"java", "spring", "maven", "rest", "backend"},
    "hibernate": {"java", "jpa", "sql", "orm"},
    "junit": {"java", "testing"},

    # ── Messaging / Distributed ────────────────────────────────────────────
    "kafka": {"distributed systems", "messaging", "pub/sub", "event streaming"},
    "rabbitmq": {"messaging", "pub/sub"},
    "redis pub/sub": {"redis", "messaging"},
    "websockets": {"http", "real-time"},

    # ── Containers / Orchestration / DevOps ────────────────────────────────
    "docker": {"linux", "shell", "yaml", "containers"},
    "kubernetes": {"docker", "yaml", "containers", "linux", "orchestration"},
    "helm": {"kubernetes", "yaml"},
    "docker compose": {"docker", "yaml"},
    "terraform": {"yaml", "iac", "cloud", "hcl"},
    "ansible": {"yaml", "ssh", "linux", "iac"},
    "github actions": {"git", "github", "yaml", "ci/cd"},
    "jenkins": {"git", "ci/cd"},
    "gitlab ci": {"git", "yaml", "ci/cd"},
    "circleci": {"git", "yaml", "ci/cd"},
    "ci/cd": {"git", "deployment"},
    "nginx": {"linux", "http", "reverse proxy"},
    "prometheus": {"monitoring", "yaml"},
    "grafana": {"monitoring", "data visualization"},

    # ── Cloud providers (specific services imply parent cloud) ─────────────
    "aws lambda": {"aws", "serverless"},
    "aws ec2": {"aws", "linux", "ssh", "compute"},
    "aws s3": {"aws", "object storage"},
    "aws rds": {"aws", "sql", "databases"},
    "aws dynamodb": {"aws", "nosql"},
    "aws cloudformation": {"aws", "yaml", "iac"},
    "aws cloudwatch": {"aws", "monitoring"},
    "aws iam": {"aws", "authentication"},
    "gcp": set(),
    "azure": set(),
    "vercel": {"node.js", "deployment"},
    "netlify": {"deployment"},

    # ── Databases ──────────────────────────────────────────────────────────
    "postgresql": {"sql", "rdbms", "databases"},
    "mysql": {"sql", "rdbms", "databases"},
    "sqlite": {"sql", "rdbms", "databases"},
    "mariadb": {"sql", "rdbms", "databases"},
    "oracle db": {"sql", "rdbms", "databases"},
    "mongodb": {"nosql", "databases", "json"},
    "cassandra": {"nosql", "distributed systems", "databases"},
    "dynamodb": {"nosql", "aws", "databases"},
    "redis": {"caching", "key-value store", "nosql", "in-memory"},
    "memcached": {"caching", "key-value store", "in-memory"},
    "elasticsearch": {"search", "nosql", "full-text search"},
    "neo4j": {"graph database", "nosql"},
    "firebase": {"nosql", "backend-as-a-service", "real-time"},
    "supabase": {"postgresql", "backend-as-a-service", "real-time"},

    # ── Tools / VCS / OS ───────────────────────────────────────────────────
    "git": {"version control"},
    "github": {"git", "version control"},
    "gitlab": {"git", "version control"},
    "bitbucket": {"git", "version control"},
    "linux": {"shell", "bash", "command line", "unix"},
    "ubuntu": {"linux", "shell", "command line"},
    "wsl": {"linux", "shell", "windows"},
    "bash": {"shell", "command line"},
    "zsh": {"shell", "command line"},

    # ── Web concepts ───────────────────────────────────────────────────────
    "jwt": {"authentication", "http", "json"},
    "oauth2": {"authentication", "http"},
    "saml": {"authentication", "http"},
    "graphql": {"http", "api"},
    "rest": {"http", "api"},
    "grpc": {"api", "protobuf"},
    "microservices": {"api", "distributed systems", "http"},
    "service mesh": {"microservices", "kubernetes"},

    # ── Testing ────────────────────────────────────────────────────────────
    "pytest": {"python", "testing", "unit testing"},
    "unittest": {"python", "testing", "unit testing"},
    "jest": {"javascript", "testing", "unit testing"},
    "vitest": {"javascript", "testing", "unit testing"},
    "mocha": {"javascript", "testing"},
    "cypress": {"javascript", "testing", "e2e testing"},
    "playwright": {"testing", "e2e testing"},
    "selenium": {"testing", "e2e testing"},
}


_PUNCT_STRIP = re.compile(
    r"^[\s\(\[\{\"'*_`]+|[\s\)\]\}\"'*_`.,;:!?]+$"
)
_WHITESPACE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Normalize a skill name for matching."""
    if not s:
        return ""
    s = s.lower().strip()
    s = _WHITESPACE.sub(" ", s)
    s = _PUNCT_STRIP.sub("", s)
    # Drop trailing version numbers ("python 3.11" -> "python")
    s = re.sub(r"\s+v?\d+(\.\d+)*$", "", s)
    return _ALIASES.get(s, s)


def compute_implicit_skills(known_skills: set[str]) -> set[str]:
    """Return the transitive closure of all skills implicit in `known_skills`."""
    implicit: set[str] = set()
    frontier = {normalize(s) for s in known_skills if s.strip()}
    seen: set[str] = set()
    while frontier:
        skill = frontier.pop()
        if skill in seen:
            continue
        seen.add(skill)
        prereqs = IMPLICIT_PREREQUISITES.get(skill, set())
        for p in prereqs:
            pn = normalize(p)
            if pn and pn not in implicit:
                implicit.add(pn)
                frontier.add(pn)
    return implicit


def filter_exploring_items(
    items: list[str], known_skills: set[str]
) -> tuple[list[str], list[str]]:
    """Filter a Currently Exploring list.

    Removes items that:
      1. Match a skill the candidate already has (direct duplicate).
      2. Match a skill implied by something they already have (prerequisite).

    Returns (kept, removed) preserving the ORIGINAL casing of each item.
    """
    known_norm = {normalize(k) for k in known_skills if k.strip()}
    implicit = compute_implicit_skills(known_norm)

    kept: list[str] = []
    removed: list[str] = []
    for item in items:
        norm = normalize(item)
        if not norm:
            continue
        if norm in known_norm or norm in implicit:
            removed.append(item)
        else:
            kept.append(item)
    return kept, removed
