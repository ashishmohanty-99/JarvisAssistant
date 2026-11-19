"""Side-hustle ideas ranked by how well they fit skills you already have."""

from __future__ import annotations

from dataclasses import asdict, dataclass

IDEAS = [
    {
        "name": "Fractional engineering leadership and AI-native delivery advisory",
        "skills": ["Engineering Management", "Architecture", "System Design", "Hiring", "Stakeholder Management",
                   "Distributed Systems", "Agentic", "Claude Code", "Fintech"],
        "effort": "4-8 h/week", "time_to_first_income": "4-8 weeks",
        "first_steps": ["Define one offer: architecture review, AI-assisted SDLC rollout, or hiring-loop design.",
                        "Write up two anonymised results from your own teams (cycle time, defect rate, cost).",
                        "Take one early-stage startup on a monthly retainer; check your employment contract first."],
    },
    {
        "name": "Freelance development or automation",
        "skills": ["Python", "JavaScript", "TypeScript", "React", "Node.js", "Django", "FastAPI", "AWS", "SQL"],
        "effort": "5-10 h/week", "time_to_first_income": "2-6 weeks",
        "first_steps": ["Pick one narrow service (e.g. 'API integrations for D2C stores').",
                        "Publish two case studies from past work on GitHub or LinkedIn.",
                        "Send 10 tailored proposals a week on Upwork, Toptal or to local businesses."],
    },
    {
        "name": "AI agent and LLM integration consulting",
        "skills": ["LLM", "Generative AI", "RAG", "AI Agents", "MCP", "Prompt Engineering", "LangChain", "Python"],
        "effort": "5-10 h/week", "time_to_first_income": "3-8 weeks",
        "first_steps": ["Package one repeatable offer: an internal chatbot, a document Q&A or an agent workflow.",
                        "Build a public demo repository and a 3-minute walkthrough video.",
                        "Offer a fixed-price pilot to three companies in your network."],
    },
    {
        "name": "Workshops and paid training on your engineering method",
        "skills": ["Spec-Driven Development", "Agile", "Scrum", "Test Automation", "System Design", "AI Agents"],
        "effort": "4-8 h/week", "time_to_first_income": "4-10 weeks",
        "first_steps": ["Turn your open-source method into a 2-hour live workshop outline.",
                        "Run it free once, record it, collect testimonials.",
                        "Sell team sessions and a recorded course (Topmate, Gumroad, Udemy)."],
    },
    {
        "name": "Technical content: YouTube, newsletter, blog",
        "skills": ["Python", "LLM", "Generative AI", "DevOps", "System Design", "Machine Learning", "AI Agents"],
        "effort": "4-6 h/week", "time_to_first_income": "3-9 months",
        "first_steps": ["Choose one topic you already ship code in; publish weekly for 12 weeks.",
                        "Reuse each piece: long video, three shorts, one post.",
                        "Monetise through sponsorships, affiliates and your own paid products, not ads alone."],
    },
    {
        "name": "Test automation and QA-as-a-service",
        "skills": ["Playwright", "Selenium", "Cypress", "Test Automation", "Cucumber", "Pytest", "JUnit", "CI/CD"],
        "effort": "5-10 h/week", "time_to_first_income": "3-6 weeks",
        "first_steps": ["Offer a fixed-price 'first 20 end-to-end tests in CI' package to startups.",
                        "Show a sample repository with reports and a flake-free run history."],
    },
    {
        "name": "Micro-SaaS or paid developer tool",
        "skills": ["React", "Next.js", "Node.js", "Python", "PostgreSQL", "AWS", "Docker", "TypeScript"],
        "effort": "8-12 h/week", "time_to_first_income": "3-12 months",
        "first_steps": ["Find a problem you have solved for an employer more than once.",
                        "Pre-sell to five users before building; ship the smallest paid version."],
    },
    {
        "name": "Data dashboards and analytics for small businesses",
        "skills": ["SQL", "Power BI", "Tableau", "Excel", "Pandas", "Data Analysis", "BigQuery", "Snowflake"],
        "effort": "4-8 h/week", "time_to_first_income": "2-6 weeks",
        "first_steps": ["Build one polished public dashboard on open data.",
                        "Offer a monthly reporting retainer to local retailers, clinics or agencies."],
    },
    {
        "name": "Cloud cost and DevOps audits",
        "skills": ["AWS", "Azure", "GCP", "Kubernetes", "Terraform", "Docker", "DevOps", "SRE", "CI/CD"],
        "effort": "4-8 h/week", "time_to_first_income": "4-8 weeks",
        "first_steps": ["Sell a one-week audit with a written savings report.",
                        "Price a share of the first year's saving or a flat fee."],
    },
]


@dataclass
class Idea:
    name: str
    fit: int
    matched_skills: list[str]
    effort: str
    time_to_first_income: str
    first_steps: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def rank(skills: list[str], top: int = 5) -> list[Idea]:
    """Order ideas by overlap with *skills* (resume skills plus GitHub languages and topics)."""
    have = {s.lower() for s in skills}
    ideas = []
    coverage: dict[str, float] = {}
    for idea in IDEAS:
        matched = [s for s in idea["skills"] if s.lower() in have]
        # Three matching skills is treated as a full fit: nobody needs every tool on the list.
        fit = min(100, round(100 * len(matched) / 3))
        ideas.append(Idea(idea["name"], fit, matched, idea["effort"], idea["time_to_first_income"], idea["first_steps"]))
        coverage[idea["name"]] = len(matched) / len(idea["skills"])
    # Ties on fit go to the idea that uses more of what it needs.
    ideas.sort(key=lambda i: (-i.fit, -coverage[i.name], i.name))
    return ideas[:top]
