"""Read a resume (PDF, DOCX, Markdown or text) into a profile used for job matching."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Lower-case skill -> display name. Multi-word and symbol skills are matched as phrases.
SKILLS = {
    name.lower(): name
    for name in (
        "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "C++", "C#", "Kotlin", "Swift", "Scala", "Ruby",
        "PHP", "SQL", "Bash", "React", "Next.js", "Angular", "Vue", "Node.js", "Django", "Flask", "FastAPI",
        "Spring Boot", "Spring", ".NET", "Rails", "GraphQL", "REST", "gRPC", "Microservices", "Kafka", "RabbitMQ",
        "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Snowflake", "BigQuery", "Spark", "Airflow",
        "dbt", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins", "GitHub Actions",
        "CI/CD", "Linux", "Git", "Machine Learning", "Deep Learning", "NLP", "LLM", "Generative AI", "RAG",
        "PyTorch", "TensorFlow", "scikit-learn", "Pandas", "NumPy", "LangChain", "Prompt Engineering", "MLOps",
        "Data Engineering", "Data Analysis", "Tableau", "Power BI", "Excel", "Selenium", "Playwright", "Cypress",
        "Test Automation", "Cucumber", "JUnit", "Pytest", "Agile", "Scrum", "Jira", "Product Management",
        "System Design", "DevOps", "SRE", "Security", "Android", "iOS", "Flutter", "React Native", "Figma",
        "Salesforce", "SAP", "ServiceNow", "Spec-Driven Development", "AI Agents", "MCP",
        "Engineering Management", "People Management", "Hiring", "Mentoring", "Stakeholder Management",
        "Architecture", "Distributed Systems", "Event-Driven Architecture", "API Design", "Fintech", "Banking",
        "Agentic", "Vector Search", "Observability", "Claude Code", "Azure DevOps", "OCI", "Infrastructure as Code",
    )
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,16}\d)(?!\d)")
_LINK = re.compile(r"(?:https?://)?(?:www\.)?(linkedin\.com/in/[\w%-]+|github\.com/[\w-]+)", re.IGNORECASE)
_YEARS = re.compile(r"(\d{1,2})\+?\s*(?:years|yrs)", re.IGNORECASE)


@dataclass
class Profile:
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    github: str = ""
    years_experience: int = 0
    skills: list[str] = field(default_factory=list)
    text: str = ""
    path: str = ""

    def to_dict(self, include_text: bool = False) -> dict:
        data = asdict(self)
        if not include_text:
            data.pop("text")
        return data


def read_text(path: str | Path) -> str:
    path = Path(path).expanduser()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("Reading PDF resumes needs: pip install 'jarvis-sidekick[resume]'") from None
        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        try:
            import docx
        except ImportError:
            raise RuntimeError("Reading DOCX resumes needs: pip install 'jarvis-sidekick[resume]'") from None
        return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)
    return path.read_text(encoding="utf-8", errors="replace")


def find_skills(text: str) -> list[str]:
    """Skills from the vocabulary that appear in *text* as whole words or phrases."""
    lowered = text.lower()
    found = []
    for key, display in SKILLS.items():
        if re.search(rf"(?<![\w+#.]){re.escape(key)}(?![\w+#])", lowered):
            found.append(display)
    return found


def parse(text: str, path: str = "") -> Profile:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    links = {m.group(1).split("/")[0].lower(): "https://" + m.group(1) for m in _LINK.finditer(text)}
    # The name is normally the first short line that is not contact details.
    name = next(
        (ln for ln in lines[:5] if len(ln.split()) <= 5 and not _EMAIL.search(ln) and not any(c.isdigit() for c in ln)),
        "",
    )
    return Profile(
        name=name,
        email=email.group(0) if email else "",
        phone=phone.group(0).strip() if phone else "",
        linkedin=links.get("linkedin.com", ""),
        github=links.get("github.com", ""),
        years_experience=max((int(y) for y in _YEARS.findall(text) if int(y) < 50), default=0),
        skills=find_skills(text),
        text=text,
        path=str(path),
    )


def load(path: str | Path) -> Profile:
    return parse(read_text(path), str(Path(path).expanduser()))
